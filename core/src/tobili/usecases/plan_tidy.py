"""清理预演。

**只生成计划，不执行。** 这一层存在的意义就是让破坏性动作先变成一张可读的清单：
每一条都带"为什么可以清 / 为什么还不能清"，理由最终要原样显示给人看。一个说不清
理由的清理工具，没人敢点确认。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from ..domain.tidy import (
    KeepEntry,
    KeepKind,
    Outcome,
    TidyAction,
    TidyCandidate,
    TidyPolicy,
    judge,
    keep_hit,
    plan_order,
)
from ..ports.collab import Clock
from ..ports.repo import KeepRepo, TidyRepo, VerdictRepo


@dataclass(frozen=True, slots=True)
class PlanLine:
    enrollment_id: int
    dyn_id: str
    up_name: str
    action: str
    allowed: bool
    reason: str
    decided_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TidyPreview:
    plan_id: int = 0
    lines: tuple[PlanLine, ...] = field(default=())
    persisted: bool = False

    @property
    def clearable(self) -> int:
        return sum(1 for line in self.lines if line.allowed)


class PlanTidy:
    def __init__(
        self,
        *,
        verdicts: VerdictRepo,
        tidy: TidyRepo,
        clock: Clock,
        keep: KeepRepo | None = None,
        policy: TidyPolicy | None = None,
    ) -> None:
        self._verdicts = verdicts
        self._tidy = tidy
        self._clock = clock
        self._keep = keep
        self._policy = policy or TidyPolicy()

    @property
    def policy(self) -> TidyPolicy:
        return self._policy

    def run(
        self,
        account_id: int,
        *,
        limit: int | None = None,
        persist: bool = True,
    ) -> TidyPreview:
        now = self._clock.now()
        rows = self._verdicts.lost_before(account_id, before=now)
        cleaned = self._tidy.cleaned_campaign_ids(account_id)
        kept = self._kept_entries(account_id)

        candidates = [
            TidyCandidate(
                enrollment_id=int(row.get("enrollment_id") or 0),  # type: ignore[arg-type]
                dyn_id=str(row.get("dyn_id") or ""),
                up_uid=str(row.get("up_uid") or ""),
                outcome=Outcome.LOST,
                decided_at=row.get("decided_at"),  # type: ignore[arg-type]
                already_cleaned=int(row.get("campaign_id") or 0) in cleaned,
            )
            for row in rows
        ]
        names = {str(row.get("dyn_id") or ""): str(row.get("up_name") or "") for row in rows}

        per_up: dict[str, int] = {}
        lines: list[PlanLine] = []
        picked: list[TidyCandidate] = []
        cap = limit if limit is not None else self._policy.max_per_round

        for candidate in plan_order(candidates):
            hit = keep_hit(kept, up_uid=candidate.up_uid, dyn_id=candidate.dyn_id)
            verdict = judge(
                candidate,
                now=now,
                policy=self._policy,
                up_deleted_in_round=per_up.get(candidate.up_uid, 0),
                kept=hit.label() if hit else "",
            )
            allowed = verdict.allowed and len(picked) < cap
            reason = verdict.reason if allowed or not verdict.allowed else f"本轮已达上限 {cap} 条"
            if allowed:
                per_up[candidate.up_uid] = per_up.get(candidate.up_uid, 0) + 1
                picked.append(candidate)
            lines.append(
                PlanLine(
                    enrollment_id=candidate.enrollment_id,
                    dyn_id=candidate.dyn_id,
                    up_name=names.get(candidate.dyn_id, ""),
                    action=(verdict.action or TidyAction.DROP_SHARE).value,
                    allowed=allowed,
                    reason=reason,
                    decided_at=candidate.decided_at,
                )
            )

        plan_id = 0
        if persist and picked:
            plan_id = self._tidy.create_plan(account_id, now=now)
            for candidate in picked:
                self._tidy.add_item(
                    plan_id,
                    campaign_id=self._campaign_id_of(candidate, rows),
                    action=TidyAction.DROP_SHARE.value,
                    snapshot=json.dumps(
                        {"dyn_id": candidate.dyn_id, "up_uid": candidate.up_uid},
                        ensure_ascii=False,
                    ),
                )
            self._tidy.mark_previewed(plan_id, now=now)

        return TidyPreview(plan_id=plan_id, lines=tuple(lines), persisted=bool(plan_id))

    def _kept_entries(self, account_id: int) -> list[KeepEntry]:
        if self._keep is None:
            return []
        return [
            KeepEntry(
                kind=KeepKind(str(row["kind"])),
                value=str(row["value"]),
                note=str(row.get("note") or ""),
            )
            for row in self._keep.all_of(account_id)
        ]

    @staticmethod
    def _campaign_id_of(candidate: TidyCandidate, rows: list[dict[str, object]]) -> int:
        for row in rows:
            if str(row.get("dyn_id") or "") == candidate.dyn_id:
                return int(row.get("campaign_id") or 0)  # type: ignore[arg-type]
        return 0
