"""把 B 站返回的 JSON 拆成朴素结构。

单独一层的原因是：B 站字段名与嵌套层级变化频繁，而业务代码不该跟着改。
这里只做"取出来"，不做"下结论"。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ...ports.models import CampaignDetail, FeedItem, UserCandidate


def _modules(item: Mapping[str, Any]) -> Mapping[str, Any]:
    modules = item.get("modules")
    return modules if isinstance(modules, Mapping) else {}


def epoch_of(value: object) -> float | None:
    """时间戳。数字和**数字字符串**都要认。

    实测动态接口把 `pub_ts` 返回成字符串 `"1793769466"`，而详情接口返回整数。
    只认数字会让发布时间恒为空，进而让两件事静默失效：开奖核验永远等不到观察期
    结束（结论卡在"未定"），转发探测永远翻不到时间边界（只能一直返回"不知道"）。
    这类 bug 不会报错，只会让功能"看起来在跑但永远得出同一个结果"。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if number > 0 else None
    return None


def author_of(item: Mapping[str, Any]) -> tuple[str, str, datetime | None]:
    author = _modules(item).get("module_author")
    if not isinstance(author, Mapping):
        return "", "", None
    uid = str(author.get("mid") or "")
    name = str(author.get("name") or "")
    published = None
    stamp = epoch_of(author.get("pub_ts"))
    if stamp is not None:
        published = datetime.fromtimestamp(int(stamp), tz=UTC)
    return uid, name, published


def _dynamic(item: Mapping[str, Any]) -> Mapping[str, Any]:
    dynamic = _modules(item).get("module_dynamic")
    return dynamic if isinstance(dynamic, Mapping) else {}


REPOST_TYPE = "DYNAMIC_TYPE_FORWARD"


def is_repost(item: Mapping[str, Any]) -> bool:
    return str(item.get("type") or "").upper() == REPOST_TYPE


def subject_of(item: Mapping[str, Any]) -> Mapping[str, Any]:
    """转发动态的"活动主体"是原动态，不是转发动作本身。

    抽奖号转发的帖子，`desc.text` 只有"转发动态"四个字，真正的活动内容在 `orig`
    里。照原样解析的话，关注流里所有转发型抽奖都会被当成空正文筛掉 —— 而转发恰恰
    是这类账号的主要内容形态（实测某个抽奖号整页 20 条全是转发）。

    归一化之后还有两个附带好处：多人转发同一个活动会塌缩成同一个 dyn_id，去重
    自动成立；参与时转发的是原动态，而不是"转发的转发"。
    """
    if not is_repost(item):
        return item
    orig = item.get("orig")
    if isinstance(orig, Mapping) and dyn_id_of(orig):
        return orig
    return item


def text_of(item: Mapping[str, Any]) -> str:
    """正文。纯图片/纯视频的动态没有 desc，去 major 里捞标题。"""
    dynamic = _dynamic(item)
    desc = dynamic.get("desc")
    parts: list[str] = []
    if isinstance(desc, Mapping):
        body = desc.get("text")
        if isinstance(body, str) and body.strip():
            parts.append(body.strip())

    major = dynamic.get("major")
    if isinstance(major, Mapping):
        for key in ("opus", "archive", "article", "draw"):
            block = major.get(key)
            if not isinstance(block, Mapping):
                continue
            for field in ("title", "summary", "desc"):
                value = block.get(field)
                if isinstance(value, Mapping):
                    value = value.get("text")
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())

    return "\n".join(dict.fromkeys(parts))


def dyn_id_of(item: Mapping[str, Any]) -> str:
    for key in ("id_str", "id"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def attachments_of(item: Mapping[str, Any]) -> tuple[str, ...]:
    major = _dynamic(item).get("major")
    if not isinstance(major, Mapping):
        return ()
    urls: list[str] = []
    draw = major.get("draw")
    if isinstance(draw, Mapping):
        for entry in draw.get("items") or []:
            if isinstance(entry, Mapping) and entry.get("src"):
                urls.append(str(entry["src"]))
    return tuple(urls[:9])


def to_feed_item(item: Mapping[str, Any]) -> FeedItem | None:
    subject = subject_of(item)
    dyn_id = dyn_id_of(subject)
    if not dyn_id:
        return None
    uid, name, published = author_of(subject)
    return FeedItem(
        dyn_id=dyn_id,
        up_uid=uid,
        text=text_of(subject),
        published_at=published,
        up_name=name,
        raw_ref=f"https://www.bilibili.com/opus/{dyn_id}",
        attachments=attachments_of(subject),
    )


def to_detail(item: Mapping[str, Any], *, dyn_id: str = "") -> CampaignDetail:
    subject = subject_of(item)
    resolved = dyn_id_of(subject) or dyn_id
    uid, name, published = author_of(subject)
    text = text_of(subject)
    reserve_id, reserve_total = _reserve_of(subject)
    rid, comment_type = _comment_target(subject, resolved)
    return CampaignDetail(
        dyn_id=resolved,
        up_uid=uid,
        up_name=name,
        text=text,
        published_at=published,
        deadline=None,
        rid=rid,
        comment_type=comment_type,
        reserve_id=reserve_id,
        reserve_total=reserve_total,
        raw_ref=f"https://www.bilibili.com/opus/{resolved}",
    )


def _comment_target(subject: Mapping[str, Any], dyn_id: str) -> tuple[str, int]:
    """评论区真正要的两个参数。

    2026-11 实测：动态 ID 不能直接当评论区的 oid。一条图文动态的 `id_str` 是
    `1265640769565229076`，而它的评论区 oid 是 `414056618`、type 是 `11`。
    拿动态 ID 加 type=17 去请求，接口回的是 `-404 啥都木有`——看起来像"这条没人
    评论"，实际是参数错了。这种错最难查，因为它伪装成"数据就是空的"。

    `basic` 在 `item.basic` 里（不是 `modules.module_basic`，那个在新版接口里
    已经不返回了）。取不到就退回动态 ID + 17，让上层拿到明确的 -404 而不是崩掉。
    """
    basic = subject.get("basic")
    if not isinstance(basic, dict):
        basic = {}
    rid = str(basic.get("comment_id_str") or "").strip() or dyn_id
    try:
        ctype = int(basic.get("comment_type") or 0) or 17
    except (TypeError, ValueError):
        ctype = 17
    return rid, ctype


def _reserve_of(item: Mapping[str, Any]) -> tuple[str, int]:
    """预约类活动。字段位置在不同版本里不一致，这里做宽松探测。

    找不到就返回空 —— 让上层走"无法确认"分支，而不是猜一个 ID 发出去。
    """
    dynamic = _dynamic(item)
    additional = dynamic.get("additional")
    for holder in (dynamic, additional if isinstance(additional, Mapping) else {}, item):
        reserve = holder.get("reserve")
        if isinstance(reserve, Mapping):
            rid = reserve.get("rid") or reserve.get("reserve_id")
            total = reserve.get("total") or reserve.get("reserve_total") or 0
            if rid:
                try:
                    return str(rid), int(total)
                except (TypeError, ValueError):
                    return str(rid), 0
    return "", 0


def like_status(item: Mapping[str, Any]) -> bool | None:
    """我是否点过赞。取不到返回 None —— 上层必须当成"不知道"。"""
    stat = _modules(item).get("module_stat")
    if not isinstance(stat, Mapping):
        return None
    like = stat.get("like")
    if not isinstance(like, Mapping):
        return None
    value = like.get("status")
    return value if isinstance(value, bool) else None


def orig_id_of(item: Mapping[str, Any]) -> str:
    orig = item.get("orig")
    if not isinstance(orig, Mapping):
        return ""
    return dyn_id_of(orig)


def reply_mid_list(payload: Mapping[str, Any]) -> list[str]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return []
    replies = data.get("replies")
    if not isinstance(replies, list):
        return []
    return [str(r.get("mid") or "") for r in replies if isinstance(r, Mapping)]


_TAG = re.compile(r"<[^>]+>")


def strip_tags(raw: object) -> str:
    """搜索结果里的字段带高亮标签（`<em class="keyword">抽奖</em>`）。

    不去掉的话，写进库里、显示在界面上都是一串 HTML，而且同一个名字会因为命中的
    关键词不同而变成不同的字符串，去重和匹配都会失效。
    """
    if not isinstance(raw, str):
        return ""
    return _TAG.sub("", raw).strip()


def to_user_candidate(raw: Mapping[str, Any]) -> UserCandidate | None:
    uid = str(raw.get("mid") or "").strip()
    if not uid.isdigit():
        return None
    return UserCandidate(
        uid=uid,
        name=strip_tags(raw.get("uname")),
        fans=_as_int(raw.get("fans")),
        sign=strip_tags(raw.get("usign")),
        videos=_as_int(raw.get("videos")),
    )


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
