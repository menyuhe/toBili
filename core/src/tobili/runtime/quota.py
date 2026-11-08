"""出站配额门禁。

`domain.throttle` 只回答"现在能不能写、要等多久"，它不查库也不看时钟。这里把它
接到真实数据上。

用量来自 `intent` 表，而不是另开一张计数表：每一次真正发出去的写操作都在那张表里
留了痕，单独的计数器迟早会和实际请求对不上（崩溃、重启、手工改库都会）。多算了
只会更保守，少算了才是事故。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from ..domain.throttle import Budget, Usage, Verdict, assess
from ..ports.collab import Clock
from ..ports.repo import IntentRepo


@dataclass(frozen=True, slots=True)
class Gate:
    verdict: Verdict
    wait_seconds: float
    last_hour: int = 0
    last_day: int = 0
    idle_seconds: float | None = None

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.GO

    def explain(self) -> str:
        if self.verdict is Verdict.STOP:
            return f"今日写操作已达上限（{self.last_day} 次），明天再继续"
        if self.verdict is Verdict.HOLD:
            return f"需要先等 {self.wait_seconds:.0f} 秒（近一小时已写 {self.last_hour} 次）"
        return "可以写"


class Quota:
    def __init__(
        self,
        *,
        intents: IntentRepo,
        clock: Clock,
        budget: Budget | None = None,
    ) -> None:
        self._intents = intents
        self._clock = clock
        self._budget = budget or Budget()

    @property
    def budget(self) -> Budget:
        return self._budget

    def check(self, account_id: int) -> Gate:
        now = self._clock.now()
        hour_count, last_at = self._intents.writes_since(account_id, since=now - timedelta(hours=1))
        day_count, _ = self._intents.writes_since(account_id, since=now - timedelta(days=1))
        idle = (now - last_at).total_seconds() if last_at else None
        verdict, wait = assess(
            self._budget,
            Usage(last_hour=hour_count, last_day=day_count, since_last_seconds=idle),
        )
        return Gate(
            verdict=verdict,
            wait_seconds=wait,
            last_hour=hour_count,
            last_day=day_count,
            idle_seconds=idle,
        )
