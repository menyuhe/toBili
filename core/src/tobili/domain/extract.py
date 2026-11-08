"""从中文文本里提取活动事实。

纯函数，不联网。抽奖文案的写法比想象中更杂：截止时间可能写成「1月5日 23:59」
「2026-01-05 24:00」「本周五截止」，也可能根本不写。所以这里不做"猜得很准"的
承诺，只输出两样东西：能确定的事实，以及**有多确定**。后者决定要不要自动参与。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from ..domain.campaign import CampaignKind

_SHARE_WORDS = ("转发", "转发抽", "抽奖", "转抽", "转+评", "转评")
_COMMENT_WORDS = ("评论抽", "评论区", "留言", "评论里")
_RESERVE_WORDS = ("预约", "预约抽奖", "开售提醒")
_CHECKIN_WORDS = ("签到", "打卡", "每日")

_RULES = (
    (CampaignKind.RESERVE, _RESERVE_WORDS),
    (CampaignKind.CHECK_IN, _CHECKIN_WORDS),
    (CampaignKind.COMMENT, _COMMENT_WORDS),
    (CampaignKind.SHARE, _SHARE_WORDS),
)

_DEADLINE_HINT = re.compile(r"(截止|截至|开奖|结束|到\s*期|有效期)")
_ABS_TIME = re.compile(
    r"(?:(20\d{2})\s*[年./-]\s*)?(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*[日号]?"
    r"(?:\s*[（(]?\s*(?:凌晨|早上|上午|中午|下午|晚上|晚间)?\s*(\d{1,2})\s*[:：点时]\s*(\d{2})?\s*分?)?"
)
_REL_DATE = r"大后天|今天|今日|今晚|明天|明日|明晚|后天|(?:本|这|下下|下)?(?:周|星期|礼拜)[一二三四五六日天1-7]|[一二两三四五六七八九十\d]+天后"
_REL_TIME = re.compile(
    rf"(?P<date>{_REL_DATE})\s*"
    r"(?:[（(]?\s*(?P<period>凌晨|早上|上午|中午|下午|晚上|晚间|晚)?\s*"
    r"(?P<hour>\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*"
    r"(?:[:：](?P<minute>\d{1,2})|[点时](?P<cnminute>半|\d{1,2}|[零一二三四五六七八九十]{1,3})?分?))?"
)
_CLOCK = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})")

_PRIZE = re.compile(r"(?:送|抽|奖|瓜分|领取|获得)\s*[^，。！？\n]{2,24}")


def _clamp_deadline(value: datetime, now: datetime) -> datetime | None:
    """跨年修正：只说「1月5日」而当前已是 12 月时，应当指明年。"""
    if value < now - timedelta(days=1):
        try:
            return value.replace(year=value.year + 1)
        except ValueError:
            return None
    return value


def _number(text: str) -> int:
    if text.isdigit():
        return int(text)
    digits = dict(zip("零一二三四五六七八九", range(10), strict=True)) | {"两": 2}
    if "十" in text:
        head, tail = text.split("十", 1)
        return (digits.get(head, 1) * 10) + digits.get(tail, 0)
    return digits.get(text, -1)


def deadline_from(text: str, *, now: datetime, allow_relative: bool = True) -> datetime | None:
    """只在出现截止/开奖类提示词时才解析时间，避免把正文里的其他日期当截止。"""
    text = text.replace("\u200b", "").replace("\ufeff", "")
    if not _DEADLINE_HINT.search(text):
        return None

    window = _context_window(text)

    rel = _REL_TIME.search(window)
    if rel is not None:
        if not allow_relative:
            return None
        word = rel.group("date")
        offset = {
            "今天": 0,
            "今日": 0,
            "今晚": 0,
            "明天": 1,
            "明日": 1,
            "明晚": 1,
            "后天": 2,
            "大后天": 3,
        }.get(word)
        if offset is None:
            if word.endswith("天后"):
                offset = _number(word[:-2])
                if not 0 <= offset <= 366:
                    return None
            else:
                weekday = {
                    "一": 0,
                    "二": 1,
                    "三": 2,
                    "四": 3,
                    "五": 4,
                    "六": 5,
                    "日": 6,
                    "天": 6,
                }.get(word[-1], int(word[-1]) - 1 if word[-1].isdigit() else 0)
                weeks = 2 if word.startswith("下下") else 1 if word.startswith("下") else 0
                offset = weeks * 7 + weekday - now.weekday()
                if not word.startswith(("本", "这", "下")) and offset < 0:
                    offset += 7
        base = now + timedelta(days=offset)
        if rel.group("hour") is None:
            return base.replace(hour=23, minute=59, second=0, microsecond=0)
        hour = _number(rel.group("hour"))
        raw_minute = rel.group("minute") or rel.group("cnminute") or "0"
        minute = 30 if raw_minute == "半" else _number(raw_minute)
        period = rel.group("period") or ("晚上" if word.endswith("晚") else "")
        if (period in ("下午", "晚上", "晚间", "晚") and hour < 12) or (
            period == "中午" and hour < 11
        ):
            hour += 12
        elif period == "凌晨" and hour == 12:
            hour = 0
        if not 0 <= minute <= 59 or not 0 <= hour <= 24 or (hour == 24 and minute != 0):
            return None
        result = base.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
        return result + timedelta(days=1) if hour == 24 else result

    match = _ABS_TIME.search(window)
    if match is None:
        return None
    year, month, day, hour, minute = match.groups()
    try:
        resolved_year = int(year) if year else now.year
        hours = int(hour) if hour else 23
        minutes = int(minute or 0) if hour else 59
        if hours > 24 or (hours == 24 and minutes != 0):
            return None
        if hour and hours < 12 and re.search(r"下午|晚上|晚间", match.group(0)):
            hours += 12
        moment = datetime(
            resolved_year,
            int(month),
            int(day),
            hours % 24,
            minutes,
            tzinfo=now.tzinfo,
        )
        if hours == 24:
            moment += timedelta(days=1)
    except ValueError:
        return None
    return moment if year else _clamp_deadline(moment, now)


def _context_window(text: str, radius: int = 24) -> str:
    """优先使用明确时间标签旁的日期，跳过未开奖描述和往期期号。"""
    candidates = []
    for hit in _DEADLINE_HINT.finditer(text):
        if re.search(r"(?:未|没|尚未|还没)$", text[max(0, hit.start() - 3) : hit.start()]):
            continue
        start = max(0, hit.start() - radius)
        window = text[start : hit.end() + radius]
        explicit = bool(re.match(r"(?:日期|时间)", text[hit.end() :]))
        for pattern in (_ABS_TIME, _REL_TIME):
            for value in pattern.finditer(window):
                left, right = start + value.start(), start + value.end()
                if text[right : right + 1] == "期":
                    continue
                distance = max(hit.start() - right, left - hit.end(), 0)
                candidates.append(
                    (
                        hit.group(0) not in ("截止", "截至"),
                        not explicit,
                        explicit and left < hit.end(),
                        distance,
                        left,
                        value.group(0),
                    )
                )
    return min(candidates)[-1] if candidates else ""


def kind_from(text: str) -> CampaignKind:
    lowered = text.lower()
    for kind, words in _RULES:
        if any(word in lowered for word in words):
            return kind
    return CampaignKind.UNKNOWN


def reward_from(text: str) -> str:
    found = _PRIZE.search(text)
    return found.group(0).strip() if found else ""


def confidence_of(text: str, kind: CampaignKind, deadline: datetime | None) -> float:
    """三个来源各自加分，最高 0.95 —— 永远不给满分，留一点人工确认的余地。"""
    if kind is CampaignKind.UNKNOWN:
        return 0.0
    score = 0.5
    if deadline is not None:
        score += 0.25
    if reward_from(text):
        score += 0.1
    if any(word in text for word in ("@", "关注", "点赞")):
        score += 0.05
    if len(text) > 200:
        score -= 0.05
    return round(min(0.95, max(0.0, score)), 3)


def keywords_of(text: str) -> tuple[str, ...]:
    hits = []
    for _kind, words in _RULES:
        hits.extend(w for w in words if w in text)
    return tuple(dict.fromkeys(hits))


def looks_like_campaign(text: str) -> bool:
    """有没有可能是活动。只做粗筛，真正的判断在 confidence 上。"""
    if len(text.strip()) < 8:
        return False
    return any(word in text for word in ("抽", "奖", "送", "预约", "签到"))
