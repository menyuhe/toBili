"""报名状态机。

只有五个状态，且**没有"重试中"**——重试不是状态，是运行时行为。把重试做成状态，
就会出现"卡在重试中"这种谁也不敢碰的僵尸记录。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Stage(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    SETTLED = "settled"
    BLOCKED = "blocked"
    FAILED = "failed"


_ALLOWED: dict[Stage, frozenset[Stage]] = {
    Stage.PLANNED: frozenset({Stage.RUNNING, Stage.BLOCKED, Stage.SETTLED}),
    Stage.RUNNING: frozenset({Stage.SETTLED, Stage.BLOCKED, Stage.FAILED}),
    Stage.BLOCKED: frozenset({Stage.RUNNING, Stage.SETTLED, Stage.FAILED}),
    Stage.FAILED: frozenset({Stage.RUNNING}),
    Stage.SETTLED: frozenset(),
}


class IllegalTransition(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Enrollment:
    id: int
    account_id: int
    campaign_id: int
    stage: Stage
    created_at: datetime
    updated_at: datetime
    reason: str = ""

    def can_move_to(self, target: Stage) -> bool:
        return target in _ALLOWED[self.stage]

    def move(self, target: Stage, *, now: datetime, reason: str = "") -> Enrollment:
        if not self.can_move_to(target):
            raise IllegalTransition(f"{self.stage.value} 不能直接变成 {target.value}")
        return Enrollment(
            id=self.id,
            account_id=self.account_id,
            campaign_id=self.campaign_id,
            stage=target,
            created_at=self.created_at,
            updated_at=now,
            reason=reason,
        )

    @property
    def is_final(self) -> bool:
        return self.stage is Stage.SETTLED
