"""活动：从一条动态里解析出来的"可参与对象"。

这里的 `confidence` 不是装饰字段。低于阈值的活动一律不允许自动参与，
因为抽奖文案五花八门，"转发抽奖"和"转发给你的朋友看看"在字面上差别很小，
猜错的代价是发出一条不该发的转发。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .draw import DrawFacts


class CampaignKind(StrEnum):
    """参与方式。`UNKNOWN` 是合法值，表示"看出来了是活动，但没看懂怎么参加"。"""

    SHARE = "share"
    INTERACTIVE = "interactive"  # Presentation grouping for verified business_type=1.
    COMMENT = "comment"
    RESERVE = "reserve"
    CHECK_IN = "check_in"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Campaign:
    dyn_id: str
    up_uid: str
    title: str
    kind: CampaignKind = CampaignKind.UNKNOWN
    up_name: str = ""
    reward: str = ""
    deadline: datetime | None = None
    confidence: float = 0.0
    source_ref: str = ""
    raw_ref: str = ""
    seen_at: datetime | None = None
    published_at: datetime | None = None
    body_text: str = ""
    tags: tuple[str, ...] = field(default=())
    draw: DrawFacts | None = None

    def __post_init__(self) -> None:
        if not self.dyn_id:
            raise ValueError("活动缺少动态 ID")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"置信度必须在 0..1 之间，收到 {self.confidence}")

    def is_open(self, now: datetime) -> bool:
        """截止时间未知时按"仍然开放"处理；已知且已过则关闭。"""
        if self.draw and (
            self.draw.state != "active" or (self.draw.deadline and now >= self.draw.deadline)
        ):
            return False
        return self.deadline is None or now < self.deadline

    def minutes_left(self, now: datetime) -> int | None:
        if self.deadline is None:
            return None
        return max(0, int((self.deadline - now).total_seconds() // 60))

    def auto_eligible(self, now: datetime, *, floor: float = 0.8) -> bool:
        """能否不经人工确认直接参与。三个条件缺一不可。"""
        return (
            self.kind is not CampaignKind.UNKNOWN and self.confidence >= floor and self.is_open(now)
        )

    def same_as(self, other: Campaign) -> bool:
        """去重只看动态 ID —— 同一条动态不会因为来源不同而变成两个活动。"""
        return self.dyn_id == other.dyn_id
