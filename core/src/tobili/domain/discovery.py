"""找源：从搜索结果里挑出值得订阅的 UP。

这是"找源"，不是"找活动"。搜索接口给的是视频、专栏、用户三类结果，没有动态，
所以这里产出的只能是"可能天天发抽奖的人"，加进订阅源以后由关注流或空间流去
真正发现活动。把搜索结果直接当成活动是错的 —— 会去做一套根本不存在的动作。

排序规则刻意保持可解释：每一分都有名字，不搞一个说不清的黑箱权重。分数只影响
展示顺序，不影响任何自动行为 —— 加不加订阅源永远是人决定。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ports.models import UserCandidate

# 名字里带这些词的，通常就是专门做抽奖的号。
SPECIALTY_WORDS = ("抽奖", "转发", "福利", "白嫖", "锦鲤", "开奖")

FANS_HIGH = 10_000
FANS_LOW = 1_000


@dataclass(frozen=True, slots=True)
class RankedUser:
    user: UserCandidate
    score: int
    reasons: tuple[str, ...] = field(default=())

    @property
    def uid(self) -> str:
        return self.user.uid


def score_of(user: UserCandidate, keyword: str) -> RankedUser:
    word = keyword.strip()
    name = user.name or ""
    sign = user.sign or ""
    score = 0
    reasons: list[str] = []

    if word and word in name:
        score += 3
        reasons.append("名字直接命中关键词")
    elif word and word in sign:
        score += 1
        reasons.append("简介提到关键词")

    hit = next((w for w in SPECIALTY_WORDS if w in name), "")
    if hit:
        score += 2
        reasons.append(f"名字带「{hit}」")

    if user.fans >= FANS_HIGH:
        score += 2
        reasons.append("粉丝过万")
    elif user.fans >= FANS_LOW:
        score += 1
        reasons.append("粉丝过千")

    if user.videos <= 0:
        reasons.append("没有投稿，可能只是转发号")

    return RankedUser(user=user, score=score, reasons=tuple(reasons))


def rank(users: tuple[UserCandidate, ...], keyword: str, *, limit: int = 10) -> list[RankedUser]:
    """分数降序，同分按粉丝数降序，最后按 UID 定序 —— 保证结果稳定可比。"""
    ranked = [score_of(user, keyword) for user in users]
    ranked.sort(key=lambda r: (-r.score, -r.user.fans, r.user.uid))
    return ranked[: max(1, limit)]
