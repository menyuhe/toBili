"""一轮里参与多条活动。

三个决定：

1. **排序按截止时间，不按发现顺序。** 快过期的先做，因为"晚做一会儿"对快截止的
   是致命的，对还有五天的没有影响。
2. **失败即跳过，不重试。** 一条出错通常意味着它需要人看一眼。在同一轮里重试，
   只会把同一个错误再犯一遍，还可能因为重试把配额烧光。
3. **配额见底就整轮停，不是跳过这条继续下一条。** 配额挡的从来不是某一条活动，
   而是"现在不该再写了"。跳过继续只会一路空转到配额彻底见底。

预演不占配额，也不落任何写操作 —— 它和真正执行走同一套选取与判断，所以预演
看到的清单就是执行时会动的清单（除非期间数据变了）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import sleep

from ..domain.enrollment import Stage
from ..domain.errors import WriteUnavailable
from ..ports.collab import Clock
from ..ports.repo import CampaignRepo
from ..runtime.quota import Quota
from ..runtime.redact import safe_exception
from .enroll import Enroll, EnrollReport

# 置信度默认门槛。`Campaign.auto_eligible` 用的是同一个值，两处不一致会造出
# "能选出来但拒绝执行"的候选，看起来像 bug。
DEFAULT_FLOOR = 0.8


@dataclass(frozen=True, slots=True)
class BatchLine:
    dyn_id: str
    up_name: str
    stage: Stage
    note: str = ""


@dataclass(frozen=True, slots=True)
class BatchReport:
    candidates: int = 0
    attempted: int = 0
    settled: int = 0
    blocked: int = 0
    failed: int = 0
    planned: int = 0
    stop_reason: str = ""
    lines: tuple[BatchLine, ...] = field(default=())
    problems: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.problems and not self.failed

    @property
    def unfinished(self) -> int:
        return max(0, self.candidates - self.attempted)


class BatchEnroll:
    def __init__(
        self,
        *,
        campaigns: CampaignRepo,
        enroll: Enroll,
        clock: Clock,
        quota: Quota | None = None,
        wait: Callable[[float], None] = sleep,
    ) -> None:
        self._campaigns = campaigns
        self._enroll = enroll
        self._clock = clock
        self._quota = quota
        self._wait = wait

    def run(
        self,
        account_id: int,
        *,
        limit: int = 10,
        floor: float = DEFAULT_FLOOR,
        dry_run: bool = True,
        include_like: bool = False,
        before_each: Callable[[int], str] | None = None,
        stop_on_failure: bool = False,
        cancelled: Callable[[], bool] | None = None,
    ) -> BatchReport:
        now = self._clock.now()
        rows = self._campaigns.candidates(account_id, now=now, floor=floor, limit=max(1, limit))
        if not rows:
            return BatchReport()

        lines: list[BatchLine] = []
        problems: list[str] = []
        counts = {Stage.SETTLED: 0, Stage.BLOCKED: 0, Stage.FAILED: 0, Stage.PLANNED: 0}
        attempted = 0
        stop_reason = ""

        for row in rows:
            if cancelled and cancelled():
                stop_reason = "任务已停止"
                break
            dyn_id = str(row.get("dyn_id") or "")
            up_name = str(row.get("up_name") or "")
            if not dyn_id:
                continue

            if not dry_run and self._quota is not None:
                gate = self._quota.check(account_id)
                while (
                    not gate.allowed
                    and gate.verdict.value == "hold"
                    and (
                        not self._quota.budget.per_hour
                        or gate.last_hour < self._quota.budget.per_hour
                    )
                ):
                    if cancelled and cancelled():
                        stop_reason = "任务已停止"
                        break
                    self._wait(min(1.0, max(0.05, gate.wait_seconds)))
                    gate = self._quota.check(account_id)
                if stop_reason:
                    break
                if not gate.allowed:
                    stop_reason = gate.explain()
                    break

            if before_each and (stop_reason := before_each(attempted)):
                break
            attempted += 1
            try:
                report = self._enroll.run(
                    account_id, dyn_id, dry_run=dry_run, include_like=include_like
                )
            except WriteUnavailable as exc:
                problems.append(str(exc))
                stop_reason = str(exc)
                break
            except Exception as exc:  # 单条炸掉不该中断整轮
                problems.append(f"{up_name or dyn_id}：{safe_exception(exc)}")
                counts[Stage.FAILED] += 1
                lines.append(BatchLine(dyn_id, up_name, Stage.FAILED, safe_exception(exc)))
                if stop_on_failure:
                    stop_reason = problems[-1]
                    break
                continue

            counts[report.stage] = counts.get(report.stage, 0) + 1
            lines.append(BatchLine(dyn_id, up_name, report.stage, _note_of(report)))
            if stop_on_failure and report.stage is Stage.FAILED:
                stop_reason = f"{up_name or dyn_id} 参与失败，本轮停止"
                break

        return BatchReport(
            candidates=len(rows),
            attempted=attempted,
            settled=counts[Stage.SETTLED],
            blocked=counts[Stage.BLOCKED],
            failed=counts[Stage.FAILED],
            planned=counts[Stage.PLANNED],
            stop_reason=stop_reason,
            lines=tuple(lines),
            problems=tuple(problems),
        )


def _note_of(report: EnrollReport) -> str:
    if report.stage is Stage.SETTLED and not report.steps:
        return "活动已截止，未做任何动作"
    parts = [f"{name}:{step.outcome.value}" for name, step in report.steps]
    return " · ".join(parts)
