"""出站配额。

B 站对高频写操作会直接上风控，而风控一旦触发，不只是这一次失败——接下来几小时的
所有请求都会被拒。所以配额判断必须是**保守的、可解释的**：告诉调用方"等多久"，
而不是"再试试"。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Budget:
    per_hour: int = 20
    per_day: int = 120
    min_gap_seconds: float = 15
    jitter_seconds: int = 5

    def __post_init__(self) -> None:
        if self.per_day and self.per_hour > self.per_day:
            raise ValueError("每小时上限不应大于每日上限")


@dataclass(frozen=True, slots=True)
class Usage:
    """由调用方统计好再传进来 —— 纯函数不查库。"""

    last_hour: int = 0
    last_day: int = 0
    since_last_seconds: float | None = None


class Verdict(StrEnum):
    GO = "go"
    HOLD = "hold"
    STOP = "stop"


def assess(budget: Budget, usage: Usage) -> tuple[Verdict, float]:
    """返回 (结论, 建议等待秒数)。`STOP` 表示今天到此为止。"""
    if budget.per_day and usage.last_day >= budget.per_day:
        return Verdict.STOP, 0.0

    if budget.per_hour and usage.last_hour >= budget.per_hour:
        # 等到整点窗口滑出去，给一个上界估计即可，不需要精确
        return Verdict.HOLD, float(budget.min_gap_seconds * 4)

    if usage.since_last_seconds is not None and usage.since_last_seconds < budget.min_gap_seconds:
        return Verdict.HOLD, float(budget.min_gap_seconds - usage.since_last_seconds)

    return Verdict.GO, 0.0
