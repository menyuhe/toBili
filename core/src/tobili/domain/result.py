"""开奖判定。

这条链路上最容易出错的不是"认不出开奖公告"，而是把**没找到**当成**没中奖**。
前者只是这次没看到，后者会直接触发删转发、取关 —— 不可恢复。

所以规则是单向的，且默认保守：

    没读到公告            → 继续等（pending）
    读到了、名单只有图片    → 继续等（核对不了）
    读到了、还没过观察期    → 继续等（可能补发）
    读到了、名单里没有你    → 未中奖（lost）
    任何一条提到你         → 中奖（won）

判成 `lost` 的条件必须同时满足"明确读到公告正文 + 已过观察期"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .tidy import Outcome

# 开奖公告的常见说法。宁可漏，不可错：漏了只是多等一轮。
RESULT_WORDS = (
    "开奖",
    "抽奖结果",
    "中奖名单",
    "获奖名单",
    "结果公示",
    "公布结果",
    "恭喜以下",
)

# 昵称太短时用字符串包含判断会误命中（"小" 出现在任何句子里）。
MIN_NICKNAME = 2


@dataclass(frozen=True, slots=True)
class Announcement:
    dyn_id: str
    text: str
    published_at: datetime | None = None
    media: int = 0

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())


@dataclass(frozen=True, slots=True)
class Decision:
    outcome: Outcome
    evidence: str


def official_decision(notice: dict, uid: str) -> Decision:
    """A negative conclusion requires a complete, internally consistent UID roster."""
    roster = notice.get("lottery_result")
    if not isinstance(roster, dict):
        return Decision(Outcome.PENDING, "官方名单尚未完整发布")
    groups = {
        name + "_prize_result": notice.get(name + "_prize") for name in ("first", "second", "third")
    }
    winners = []
    complete = notice.get("status") == 2 and not (roster.keys() - groups.keys())
    for group, count in groups.items():
        entries = roster.get(group, [])
        if not isinstance(entries, list):
            complete = False
            continue
        ids = [str(entry.get("uid", "")) if isinstance(entry, dict) else "" for entry in entries]
        if uid in ids and uid.isdigit() and int(uid) > 0:
            return Decision(Outcome.WON, "官方中奖名单包含本账号")
        complete &= (
            type(count) is int
            and count >= 0
            and len(ids) == count
            and all(i.isdigit() and int(i) > 0 for i in ids)
        )
        winners.extend(ids)
    if complete and winners and len(winners) == len(set(winners)):
        return Decision(Outcome.LOST, "官方完整名单中未包含本账号")
    return Decision(Outcome.PENDING, "官方名单不完整，待确认")


def looks_like_result(text: str) -> bool:
    return any(word in text for word in RESULT_WORDS)


def mentions(text: str, *, uid: str, nickname: str) -> bool:
    """公告正文里有没有指向这个账号的线索。

    B 站的 @ 显示的是昵称而不是 UID，所以昵称是主信号；UID 只在原文真的写了
    `@123456` 这种形式时才算。昵称过短时放弃判断 —— 宁可漏判，也不要误判中奖。
    """
    if uid and re.search(rf"@{re.escape(uid)}(?!\d)", text):
        return True
    return len(nickname.strip()) >= MIN_NICKNAME and nickname.strip() in text


def decide(
    announcements: tuple[Announcement, ...] | list[Announcement],
    *,
    uid: str,
    nickname: str,
    now: datetime,
    grace_hours: int = 12,
) -> Decision:
    hits = [item for item in announcements if looks_like_result(item.text)]
    if not hits:
        return Decision(Outcome.PENDING, "还没找到开奖公告")

    for item in hits:
        if mentions(item.text, uid=uid, nickname=nickname):
            return Decision(Outcome.WON, f"开奖公告 {item.dyn_id} 提到了你")

    # 只有"文字里认得出是开奖公告"才会走到这里。纯图片动态连命中都算不上，
    # 上一步的 `not hits` 已经挡住了 —— 图上有没有自己的名字，程序无从得知。
    newest = max(hits, key=lambda item: item.published_at or datetime.min)
    if newest.media:
        return Decision(Outcome.PENDING, "公告包含图片名单，待人工核对")
    if newest.published_at is None:
        return Decision(Outcome.PENDING, "开奖公告没有发布时间，无法确认已过观察期")
    if now - newest.published_at < timedelta(hours=grace_hours):
        return Decision(
            Outcome.PENDING,
            f"开奖公告 {newest.dyn_id} 发布不足 {grace_hours} 小时，留观察期",
        )
    return Decision(Outcome.LOST, f"开奖公告 {newest.dyn_id} 未提及你")
