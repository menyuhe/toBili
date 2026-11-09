"""预选池的用例：管队列、跑队列。

分成两个用例是因为它们的失败方式完全不同：

- `ManagePool` 只动本地数据，失败就是参数不对，改一下再来。
- `RunPool` 会往外写（转发、评论）；单条证据不足进入待处理，执行异常暂停队列。

跑队列刻意做成**同步、逐条、可预演**。老版本就是这样的：一轮十几条，每条之间本来
就有间隔，做成后台任务只会多一层"它到底在跑还是在卡着"的不确定性。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from time import sleep

from ..domain.enrollment import Stage
from ..domain.errors import WriteUnavailable, MissingCredential, RemoteError, Fault
from ..domain.pool import (
    POOL_LIMIT,
    PoolItem,
    PoolOp,
    PoolRejected,
    PoolState,
    apply_op,
    summarize,
    waiting,
)
from ..domain.tz import beijing
from ..ports.collab import Clock
from ..ports.repo import CampaignRepo, EnrollmentRepo, PoolRepo
from ..runtime.quota import Quota
from ..runtime.redact import safe_exception
from .enroll import Enroll


@dataclass(frozen=True, slots=True)
class PoolView:
    """给界面看的一份快照。`items` 是联表后的行（带标题、开奖时间）。"""

    items: tuple[dict[str, object], ...]
    paused: bool
    note: str
    counts: dict[str, int]
    limit: int = POOL_LIMIT


class ManagePool:
    def __init__(
        self,
        *,
        pool: PoolRepo,
        campaigns: CampaignRepo,
        enrollments: EnrollmentRepo,
        clock: Clock,
    ) -> None:
        self._pool = pool
        self._campaigns = campaigns
        self._enrollments = enrollments
        self._clock = clock

    def view(self, account_id: int) -> PoolView:
        rows = self._pool.items(account_id)
        states = [self._as_item(row) for row in rows]
        paused, note = self._pool.paused(account_id)
        return PoolView(
            items=tuple(_decorate(row) for row in rows),
            paused=paused,
            note=note,
            counts=summarize(states),
        )

    def edit(
        self,
        account_id: int,
        op: PoolOp,
        *,
        campaign_ids: Sequence[int] = (),
        campaign_id: int = 0,
        direction: int = 0,
    ) -> PoolView:
        now = self._clock.now()
        current = self._pool.states(account_id)

        if op is PoolOp.PAUSE:
            self._pool.set_paused(account_id, True, note="手动暂停", now=now)
            return self.view(account_id)
        if op is PoolOp.RESUME:
            self._pool.set_paused(account_id, False, note="", now=now)
            return self.view(account_id)

        updated = apply_op(
            current,
            op,
            campaign_ids=tuple(campaign_ids),
            campaign_id=campaign_id,
            direction=direction,
            check=lambda cid: self._why_not(account_id, cid, now),
        )
        self._pool.save(account_id, updated, now=now)
        return self.view(account_id)

    def _why_not(self, account_id: int, campaign_id: int, now: datetime) -> str:
        """能不能加进池子。返回空串表示可以，否则是给人看的原因。"""
        row = self._campaigns.get(campaign_id)
        if row is None:
            return "这条活动已经不在库里了"
        if self._campaigns.shelved(account_id, campaign_id):
            return "这条活动已移到往期架，请先取回"
        if self._campaigns.blocked(str(row.get("up_uid") or "")):
            return "该 UP 已列入黑名单"
        if row.get('draw_state') in ('ended','unverified','unsupported'):
            return str(row.get('draw_reason') or '官方抽奖已结束或参与方式待核对')
        deadline = _as_time(row.get("expires_at") or row.get("deadline_at"))
        if deadline is not None and deadline <= now:
            return f"{row.get('title') or '这条活动'}：已经过开奖时间了"
        if self._enrollments.stage_of(account_id, campaign_id) is Stage.SETTLED:
            return f"{row.get('title') or '这条活动'}：已经参与过了"
        return ""

    @staticmethod
    def _as_item(row: dict[str, object]) -> PoolItem:
        return PoolItem(
            campaign_id=int(row["campaign_id"]),  # type: ignore[arg-type]
            position=int(row["position"]),  # type: ignore[arg-type]
            state=PoolState(str(row["state"])),
            message=str(row["message"] or ""),
        )


def _as_time(value: object) -> datetime | None:
    """仓储有时候给 datetime，有时候给库里的原始字符串（`campaigns.get()` 就是后者）。

    两种都得认：只认一种的话，过期活动会被悄悄放进池子 —— 加的时候没反应，
    跑的时候才失败。
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _decorate(row: dict[str, object]) -> dict[str, object]:
    """联表行 → 界面行。开奖时间转成北京时间，前端不用再猜时区。"""
    when = beijing(_as_time(row.get("deadline_at")))
    return {
        "campaign_id": row.get("campaign_id"),
        "dyn_id": row.get("dyn_id") or "",
        "title": row.get("title") or "",
        "reward": row.get("reward") or "",
        "up_name": row.get("up_name") or "",
        "up_uid": row.get("up_uid") or "",
        "kind": row.get("kind") or "unknown",
        "deadline": when.isoformat() if when else None,
        "position": row.get("position") or 0,
        "state": row.get("state") or PoolState.PENDING.value,
        "message": row.get("message") or "",
    }


@dataclass(frozen=True, slots=True)
class PoolLine:
    campaign_id: int
    dyn_id: str
    title: str
    state: str
    message: str = ""


@dataclass(frozen=True, slots=True)
class PoolRunReport:
    applied: bool
    attempted: int = 0
    joined: int = 0
    skipped: int = 0
    failed: int = 0
    review: int = 0
    pending: int = 0
    paused: bool = False
    note: str = ""
    lines: tuple[PoolLine, ...] = field(default=())
    stop_reason: str = ""


class RunPool:
    """跑队列。

    `enroll` 传的是**工厂**不是实例：装配 `Enroll` 就要读凭据，而"池子是空的"
    "队列暂停着"这两种情况根本不需要凭据，也不该被报成"未登录"。先说要紧的那件事。
    """

    def __init__(
        self,
        *,
        pool: PoolRepo,
        enroll: Callable[[], Enroll],
        clock: Clock,
        quota: Quota | None = None,
        wait: Callable[[float], None] = sleep,
        progress: Callable[[int, str], None] | None = None,
        events=None,
    ) -> None:
        self._pool = pool
        self._enroll = enroll
        self._clock = clock
        self._quota = quota
        self._wait = wait
        self._progress = progress
        self._events = events

    def run(
        self,
        account_id: int,
        *,
        apply: bool = False,
        limit: int = 0,
        before_each: Callable[[int], str] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> PoolRunReport:
        """按人工排好的顺序逐条参与。

        `apply=False` 是预演：走同一套判断，但一条都不写，也不改池子的状态。
        预演会真的去看每条活动 —— 不然"预演通过、执行失败"就没有任何意义。
        """
        paused, note = self._pool.paused(account_id)
        if paused and apply:
            raise PoolRejected(f"预选池已暂停（{note or '手动暂停'}），先点继续再来")

        names = {int(row["campaign_id"]): row for row in self._pool.items(account_id)}
        states = self._pool.states(account_id)
        recoverable = sorted(
            (item for item in states if item.state is PoolState.REVIEW and history_pending(item.message)),
            key=lambda item: str(names.get(item.campaign_id, {}).get('updated_at') or ''),
        )[:3] if apply else []
        targets = [*recoverable, *waiting(states)]
        if not targets:
            raise PoolRejected("预选池里没有待参与的活动")

        enroll = self._enroll()

        lines: list[PoolLine] = []
        joined = skipped = failed = review = work_attempts = legacy_checks = 0
        stop = ""

        for target in targets:
            if limit > 0 and work_attempts >= limit:
                break
            if cancelled and cancelled():
                stop = "任务已停止"
                break
            label = names.get(target.campaign_id) or {}
            title = str(label.get("reward") or "奖品待补全")
            dyn_id = str(label.get("dyn_id") or "")
            if not dyn_id:
                continue

            expiry = _as_time(label.get('expires_at') or label.get('deadline_at'))
            obsolete = (str(label.get('draw_reason') or '官方抽奖已结束') if label.get('draw_state')=='ended' else
                        'UP 已屏蔽' if label.get('blocked') else
                        '已收存' if label.get('shelved') else
                        '已参与' if label.get('enrolled_stage') == Stage.SETTLED.value else
                        '已截止' if expiry and expiry <= self._clock.now() else '')
            if obsolete:
                if apply:self._settle(account_id,target,PoolState.SKIPPED,obsolete)
                skipped += 1
                lines.append(PoolLine(target.campaign_id,dyn_id,title,PoolState.SKIPPED.value,obsolete))
                continue

            # Older versions left blocked attempts pending forever. Quarantine their
            # existing diagnosis before reserving any new attempt; explicit retry clears it.
            ready = False
            if target in recoverable or (target.message and label.get('enrolled_stage') == Stage.BLOCKED.value):
                message = str(label.get('enrolled_reason') or target.message)
                ready = False
                if apply and hasattr(enroll, 'recheck'):
                    # Old queues can contain hundreds of uncertain rows. Inspect a
                    # bounded subset per round while allowing fresh rows past them.
                    if legacy_checks >= 3:
                        continue
                    legacy_checks += 1
                    try:
                        check = enroll.recheck(account_id, dyn_id)
                    except (MissingCredential, WriteUnavailable):
                        raise
                    except Exception as exc:
                        if isinstance(exc, RemoteError) and exc.fault in (Fault.AUTH, Fault.RISK, Fault.RATE_LIMIT, Fault.NETWORK, Fault.TIMEOUT):
                            raise
                        message = '重新核对失败：' + safe_exception(exc)
                    else:
                        ready = bool(check.steps) and all(
                            step.outcome.value in ('settled', 'skipped_present', 'duplicate', 'not_applicable') for _, step in check.steps
                        )
                        message = check.reason or message
                        if check.skipped:
                            self._settle(account_id,target,PoolState.SKIPPED,'原动态已截止')
                            skipped += 1
                            continue
                if not ready:
                    if apply:self._mark(account_id,target,PoolState.REVIEW,message)
                    review += 1
                    lines.append(PoolLine(target.campaign_id,dyn_id,title,PoolState.REVIEW.value,message))
                    continue

            if apply and self._quota is not None:
                gate = self._quota.check(account_id)
                while (
                    not gate.allowed
                    and gate.verdict.value == "hold"
                    and (
                        not self._quota.budget.per_hour
                        or gate.last_hour < self._quota.budget.per_hour
                    )
                ):
                    if self._pool.paused(account_id)[0] or (cancelled and cancelled()):
                        stop = "队列已暂停或任务已停止"
                        break
                    if self._progress:
                        self._progress(
                            account_id,
                            f"已完成 {len(lines)}/{len(targets)} · {gate.wait_seconds:.0f} 秒后继续",
                        )
                    self._wait(min(1.0, max(0.05, gate.wait_seconds)))
                    gate = self._quota.check(account_id)
                if stop:
                    break
                if not gate.allowed:
                    stop = gate.explain()
                    break

            if apply:
                if self._pool.paused(account_id)[0]:
                    stop = "队列已暂停"
                    break
                current = next(
                    (
                        x
                        for x in self._pool.states(account_id)
                        if x.campaign_id == target.campaign_id
                    ),
                    None,
                )
                if current is None or not (current.waiting or (target in recoverable and ready and current.state is PoolState.REVIEW)):
                    continue

            if before_each and (stop := before_each(len(lines))):
                break
            work_attempts += 1
            try:
                if self._progress:
                    self._progress(
                        account_id, f"正在参与 {len(lines) + 1}/{len(targets)} · {title}"
                    )
                report = enroll.run(account_id, dyn_id, dry_run=not apply)
            except WriteUnavailable as exc:
                raise PoolRejected(str(exc)) from exc
            except Exception as exc:
                if isinstance(exc, RemoteError) and exc.fault in (Fault.AUTH, Fault.RISK, Fault.RATE_LIMIT, Fault.NETWORK, Fault.TIMEOUT):
                    raise
                message = safe_exception(exc)
                if isinstance(exc, RemoteError) and exc.fault in (Fault.REMOTE_BUSINESS, Fault.PARSE):
                    if apply:self._mark(account_id,target,PoolState.REVIEW,message)
                    review += 1
                    lines.append(PoolLine(target.campaign_id,dyn_id,title,PoolState.REVIEW.value,message))
                    continue
                if self._events:
                    self._events.publish(
                        "participation.issue",
                        {
                            "account_id": account_id,
                            "level": "error",
                            "dyn_id": dyn_id,
                            "title": title,
                            "message": message,
                        },
                    )
                if apply:
                    self._mark(account_id, target, PoolState.FAILED, message)
                lines.append(
                    PoolLine(target.campaign_id, dyn_id, title, PoolState.FAILED.value, message)
                )
                failed += 1
                stop = f"{title} 失败：{message}"
                break

            state, message = _outcome(report.stage)
            if report.stage is Stage.BLOCKED or (apply and report.stage is Stage.PLANNED):
                state = PoolState.REVIEW
                message = getattr(report, 'reason', '') or "参与未完成，需核对后重新排队"
            if getattr(report, "skipped", False):
                state, message = PoolState.SKIPPED, getattr(report,'reason','') or "原动态已截止，已跳过"
            if report.stage in (Stage.BLOCKED, Stage.FAILED):
                details = [
                    f"{name}：{step.message}"
                    for name, step in getattr(report, "steps", ())
                    if step.outcome.value in ("skipped_unknown", "rejected", "unknown")
                ]
                message = "；".join(details) or message
            if apply:
                self._settle(account_id, target, state, message)
            if any(getattr(step, 'fault', None) in (Fault.RISK, Fault.RATE_LIMIT)
                   for _, step in getattr(report, 'steps', ())):
                raise RemoteError(Fault.RISK, message)
            lines.append(PoolLine(target.campaign_id, dyn_id, title, state.value, message))
            if state is PoolState.SUCCESS:
                joined += 1
            elif state is PoolState.SKIPPED:
                skipped += 1
            elif state is PoolState.REVIEW:
                review += 1
            elif state is PoolState.FAILED:
                failed += 1
                stop = f"{title} 失败：{message}"
                break

        if apply and stop and failed:
            self._pool.set_paused(account_id, True, note=stop, now=self._clock.now())

        paused_now, _ = self._pool.paused(account_id)
        remaining = len(waiting(self._pool.states(account_id)))
        note_text = stop or (
            f"处理 {len(lines)} 条：成功 {joined}、待处理 {review}、跳过 {skipped}、失败 {failed}，待参与 {remaining} 条"
        )
        if self._progress:
            self._progress(account_id, "")
        return PoolRunReport(
            applied=apply,
            attempted=len(lines),
            joined=joined,
            skipped=skipped,
            failed=failed,
            review=review,
            pending=remaining,
            paused=paused_now,
            note=note_text,
            lines=tuple(lines),
            stop_reason=stop,
        )

    def _settle(self, account_id: int, target: PoolItem, state: PoolState, message: str) -> None:
        now = self._clock.now()
        if state is PoolState.PENDING:
            self._mark(account_id, target, state, message, now=now)
            return
        # 成功和跳过都不需要再动了：账已经记进 enrollment，池子里留着只是噪音。
        if state in (PoolState.SUCCESS, PoolState.SKIPPED):
            self._pool.drop(account_id, target.campaign_id)
            return
        self._pool.update_one(
            account_id,
            PoolItem(
                campaign_id=target.campaign_id,
                position=target.position,
                state=state,
                message=message,
            ),
            now=now,
        )

    def _mark(
        self,
        account_id: int,
        target: PoolItem,
        state: PoolState,
        message: str,
        *,
        now: datetime | None = None,
    ) -> None:
        self._pool.update_one(
            account_id,
            PoolItem(
                campaign_id=target.campaign_id,
                position=target.position,
                state=state,
                message=message,
            ),
            now=now or self._clock.now(),
        )


def _outcome(stage: Stage) -> tuple[PoolState, str]:
    """报名结果 → 池子里的状态。

    预演保持 pending；真实执行的未完成项由调用方转入 review。
    """
    if stage is Stage.SETTLED:
        return PoolState.SUCCESS, "已参与"
    if stage is Stage.BLOCKED:
        return PoolState.REVIEW, "参与未完成，需核对后重新排队"
    if stage is Stage.PLANNED:
        return PoolState.PENDING, "还没做完，下次继续"
    return PoolState.FAILED, "参与失败"


def history_pending(message: str) -> bool:
    """Only incomplete read coverage may resume; ambiguous/rejected sends may not."""
    if message.startswith('收藏：收藏已提交，但未确认成功'):
        return True  # Reconcile a positive read only; Journal never resends it.
    return any(token in message for token in (
        '转发历史核查中', '评论历史核查中',
        '未覆盖奖品发布时间', '评论区共',
        '参与类型已更正',
    )) and not any(token in message for token in ('发送记录', '结果未确认', '拒绝', '风控'))
