"""运行时基础设施：串行道、意图日志、事件、脱敏、时钟。"""

from __future__ import annotations

from datetime import UTC, datetime

from .events import Event, EventBus
from .journal import Journal, Outcome, Step
from .lane import BusyAccount, Lanes
from .redact import mask_id, safe_exception, scrub, scrub_text


class SystemClock:
    """唯一允许直接读系统时间的地方。测试里换成固定时钟即可复现一切。"""

    def now(self) -> datetime:
        return datetime.now(UTC)


__all__ = [
    "BusyAccount",
    "Event",
    "EventBus",
    "Journal",
    "Lanes",
    "Outcome",
    "Step",
    "SystemClock",
    "mask_id",
    "safe_exception",
    "scrub",
    "scrub_text",
]
