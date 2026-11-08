"""清理执行。

四个阶段是硬的，不能跳：**预演 → 确认 → 执行 → 记录**。`previewed` 的计划拒绝执行，
`draft` 的连执行都不给看。这不是流程洁癖：一条取关请求发出去，对方的粉丝数就少了
一个，回到原状再做一次是做不到的（重新关注不会恢复关注时间、分组、特别关注）。

三条执行期规则，都会在真机上被验证到：

1. **默认不写。** `apply=False` 时只做只读核查：这条转发到底在不在、目标归属对不对。
   预演和执行的差别只在最后那一下，前面的判断完全一样，所以预演结果可信。
2. **动手前先确认目标。** 删的是"我自己那条转发"，ID 从我的空间动态里现找，并且
   必须满足 `may_delete_share` —— 是我发的、转的是这条活动。任何一条不满足就跳过，
   不猜。
3. **随时能停。** 闸门在执行前查一次、执行中每几条再查一次；遇到结果未知或风控
   直接停手并把闸门关上。一批操作里出现"不知道结果"，继续做后面的只会让现场更乱。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ..domain.action import Action, ActionKind
from ..domain.errors import Fault, MissingCredential
from ..domain.tidy import (
    PlanState,
    TidyAction,
    advance,
    executable,
    is_closed,
    may_delete_share,
)
from ..ports.bili import Gateway
from ..ports.collab import Clock
from ..ports.repo import TidyRepo
from ..runtime.gate import CleaningGate
from ..runtime.journal import Journal, Outcome, Step
from ..runtime.quota import Quota

# 执行过程中多久复查一次闸门。太频繁是浪费请求，太稀疏等于没有刹车。
_GATE_EVERY = 5

# 被人在清单里取消掉的理由。写进库而不是留在界面上，是因为"这条为什么没执行"
# 必须和"这条执行了吗"一样查得到。
_DESELECT = "在清单里被取消勾选"


class TidyRejected(RuntimeError):
    """计划状态或目标不允许执行。属于"调用顺序错了"，不是远端问题。"""


class TidyBlocked(RuntimeError):
    """闸门关闭。带着人能看懂的关闭理由。"""


@dataclass(frozen=True, slots=True)
class ExecLine:
    item_id: int
    dyn_id: str
    up_name: str
    action: str
    state: str  # planned | done | failed | skipped
    message: str = ""


@dataclass(frozen=True, slots=True)
class ExecReport:
    plan_id: int
    plan_state: str
    dry_run: bool
    lines: tuple[ExecLine, ...] = field(default=())
    note: str = ""
    snapshot: str = ""
    """执行前那份库快照的路径。删转发是回不来的，至少要留得下"当时我看到了什么"。"""

    @property
    def done(self) -> int:
        return sum(1 for line in self.lines if line.state == "done")

    @property
    def failed(self) -> int:
        return sum(1 for line in self.lines if line.state == "failed")

    @property
    def skipped(self) -> int:
        return sum(1 for line in self.lines if line.state == "skipped")

    @property
    def planned(self) -> int:
        return sum(1 for line in self.lines if line.state == "planned")

    @property
    def wrote_anything(self) -> bool:
        return not self.dry_run and self.done > 0


@dataclass(frozen=True, slots=True)
class UndoReport:
    item_id: int
    ok: bool
    message: str


@dataclass(frozen=True, slots=True)
class SelectReport:
    plan_id: int
    selected: int
    dropped: int
    revived: int


class EditTidy:
    """Local selection and confirmation do not require a remote session."""

    def __init__(self, *, tidy: TidyRepo, clock: Clock) -> None:
        self._tidy = tidy
        self._clock = clock

    # ---- 阶段二：确认 ----
    def confirm(self, plan_id: int) -> PlanState:
        plan = self._plan(plan_id)
        current = PlanState(str(plan["state"]))
        target = advance(current, PlanState.CONFIRMED)
        self._tidy.mark_confirmed(plan_id, now=self._clock.now())
        return target

    # ---- 阶段一点五：勾选 ----
    def select(self, plan_id: int, item_ids: Sequence[int]) -> SelectReport:
        """把清单对齐到"人要执行的这一组"。

        传进来的是**全集**，不是增量：在集合里的恢复成待执行，不在集合里的取消。
        这样界面无论怎么勾、怎么反复勾，结果只取决于最后一次提交的集合，不会因为
        漏传某个 id 而把一条本来要做的事悄悄扔掉。

        只允许在 `previewed` 阶段改清单。确认之后还能改的话，"确认时看到 5 条、
        执行时变成 8 条"，确认这一步就失去意义了。
        """
        plan = self._plan(plan_id)
        state = PlanState(str(plan["state"]))
        if state is not PlanState.PREVIEWED:
            raise TidyRejected(f"计划 #{plan_id} 当前是 {_label(state)}，清单已锁定")

        rows = self._tidy.selectable_items(plan_id)
        known = {int(row["id"]) for row in rows}  # type: ignore[arg-type]
        keep = {int(i) for i in item_ids} & known
        now = self._clock.now()
        dropped = revived = 0
        for row in rows:
            item_id = int(row["id"])  # type: ignore[arg-type]
            current = str(row["state"])
            if item_id in keep:
                if current == "dropped" and self._tidy.revive_item(item_id, now=now):
                    revived += 1
            elif current == "pending" and self._tidy.drop_item(item_id, reason=_DESELECT, now=now):
                dropped += 1

        counts = self._tidy.counts_of(plan_id)
        return SelectReport(
            plan_id=plan_id,
            selected=counts.get("pending", 0),
            dropped=counts.get("dropped", 0),
            revived=revived,
        )

    def _plan(self, plan_id: int) -> dict[str, object]:
        plan = self._tidy.plan_of(plan_id)
        if plan is None:
            raise TidyRejected(f"计划 #{plan_id} 不存在")
        state = PlanState(str(plan["state"]))
        if is_closed(state):
            raise TidyRejected(f"计划 #{plan_id} 已经是 {_label(state)}，不能再动")
        return plan


class ExecuteTidy(EditTidy):
    def __init__(
        self,
        *,
        gateway: Gateway,
        tidy: TidyRepo,
        journal: Journal,
        gate: CleaningGate,
        clock: Clock,
        quota: Quota | None = None,
        protected: Callable[[int, int], bool] | None = None,
        pages: int = 4,
        snapshot: Callable[[], str] | None = None,
    ) -> None:
        self._gateway = gateway
        self._tidy = tidy
        self._journal = journal
        self._gate = gate
        self._clock = clock
        self._quota = quota
        self._protected = protected
        self._pages = pages
        self._snapshot = snapshot

    # ---- 阶段三：执行 ----
    def run(self, plan_id: int, *, apply: bool = False, limit: int | None = None) -> ExecReport:
        plan = self._plan(plan_id)
        account_id = int(plan["account_id"])  # type: ignore[arg-type]
        current = PlanState(str(plan["state"]))
        if not executable(current):
            raise TidyRejected(f"计划 #{plan_id} 当前是 {_label(current)}，必须先确认才能执行")

        if apply and self._journal.preflight:
            self._journal.preflight(account_id)
        items = list(self._tidy.pending_items(plan_id))
        if limit is not None:
            items = items[: max(0, limit)]
        if not items:
            # 清单被勾空了也得收尾。否则计划永远停在 confirmed，下次点执行还是
            # 同一句"没有待执行的条目"，人只会以为按钮坏了。
            if apply:
                self._tidy.finish_plan(
                    plan_id,
                    state=PlanState.DONE.value,
                    note="清单里没有勾选任何条目",
                    now=self._clock.now(),
                )
                return ExecReport(
                    plan_id,
                    self._tidy.state_of(plan_id),
                    dry_run=False,
                    note="清单里没有勾选任何条目",
                )
            return ExecReport(plan_id, current.value, not apply, note="没有待执行的条目")

        # 快照放在闸门和配额之后：前面两道拒绝了就什么都不写，没必要占一份备份。
        shot = ""
        if apply:
            decision = self._gate.check(account_id)
            if not decision.open:
                raise TidyBlocked(decision.explain())
            self._check_quota(account_id)
            shot = self._take_snapshot()
            self._tidy.mark_state(plan_id, PlanState.RUNNING.value, now=self._clock.now())

        # 删转发前必须知道"我是谁"，否则没法核对归属。取关不需要，所以只在有
        # 删转发条目时才去查一次。
        me = ""
        if any(str(item["action"]) == TidyAction.DROP_SHARE.value for item in items):
            me = self._gateway.whoami()
            if not me:
                raise MissingCredential("拿不到当前账号，无法核对转发归属")

        lines: list[ExecLine] = []
        note = ""
        stopped = False
        for index, item in enumerate(items):
            if apply and index and index % _GATE_EVERY == 0:
                decision = self._gate.check(account_id)
                if not decision.open:
                    note = f"执行到第 {index} 条时闸门关闭：{decision.explain()}"
                    stopped = True
                    break
            line, stop = self._one(account_id, item, apply=apply, me=me)
            lines.append(line)
            if stop:
                note = line.message or "遇到无法确定的结果，已停止"
                stopped = True
                break

        # 无论正常走完还是中途停手，计划都要收尾。停在 running 会让下一次运行
        # 以为"上一轮还在跑"，而它对一个已经死掉的进程毫无意义。
        if apply:
            closing = self._wrap_up(plan_id, aborted=stopped)
            note = note or closing
        else:
            note = note or "预演，没有写任何东西"
        return ExecReport(
            plan_id=plan_id,
            plan_state=self._tidy.state_of(plan_id),
            dry_run=not apply,
            lines=tuple(lines),
            note=note,
            snapshot=shot,
        )

    def _one(
        self, account_id: int, item: dict[str, object], *, apply: bool, me: str
    ) -> tuple[ExecLine, bool]:
        item_id = int(item["id"])  # type: ignore[arg-type]
        dyn_id = str(item.get("dyn_id") or "")
        up_name = str(item.get("up_name") or "")
        action = str(item.get("action") or "")
        campaign_id = int(item["campaign_id"])  # type: ignore[arg-type]

        if self._protected and self._protected(account_id, campaign_id):
            reason = "活动在建立清单后被留存或改判，跳过"
            if apply:
                self._close_item(item_id, "skipped", reason)
            return ExecLine(item_id, dyn_id, up_name, action, "skipped", reason), False

        if action == TidyAction.UNFOLLOW.value:
            return self._unfollow(account_id, item_id, item, apply=apply)
        return self._drop_share(
            account_id, item_id, campaign_id, dyn_id, up_name, apply=apply, me=me
        )

    # ---- 删转发 ----
    def _drop_share(
        self,
        account_id: int,
        item_id: int,
        campaign_id: int,
        dyn_id: str,
        up_name: str,
        *,
        apply: bool,
        me: str,
    ) -> tuple[ExecLine, bool]:
        try:
            ref = self._gateway.find_share(dyn_id, pages=self._pages)
        except Exception as exc:
            message = f"找我的转发时失败：{exc}"
            self._close_item(item_id, "skipped", message)
            return ExecLine(item_id, dyn_id, up_name, "drop_share", "skipped", message), False

        verdict = may_delete_share(ref, me=me, campaign_dyn_id=dyn_id)
        if not verdict.allowed or ref is None:
            self._close_item(item_id, "skipped", verdict.reason)
            return ExecLine(
                item_id, dyn_id, up_name, "drop_share", "skipped", verdict.reason
            ), False

        snapshot = json.dumps(
            {
                "my_dyn_id": ref.dyn_id,
                "orig_dyn_id": ref.orig_dyn_id,
                "published_at": ref.published_at.isoformat() if ref.published_at else "",
            },
            ensure_ascii=False,
        )
        # 先落"我要删哪一条"，再发请求。顺序反过来的话，进程在两步之间被杀，
        # 事后就没人知道到底删没删、删的是哪条。
        self._tidy.set_target(item_id, target=ref.dyn_id, snapshot=snapshot)

        if not apply:
            return (
                ExecLine(
                    item_id,
                    dyn_id,
                    up_name,
                    "drop_share",
                    "planned",
                    f"会删除我的转发 {ref.dyn_id}",
                ),
                False,
            )

        step = self._journal.attempt(
            account_id=account_id,
            campaign_id=campaign_id,
            action=Action(ActionKind.DELETE_DYN, ref.dyn_id, {"orig_dyn_id": dyn_id}),
        )
        state, message, stop = _translate(step)
        self._close_item(item_id, state, message)
        if stop:
            self._pause_on_risk(account_id, step)
        return ExecLine(item_id, dyn_id, up_name, "drop_share", state, message), stop

    # ---- 取关 ----
    def _unfollow(
        self, account_id: int, item_id: int, item: dict[str, object], *, apply: bool
    ) -> tuple[ExecLine, bool]:
        dyn_id = str(item.get("dyn_id") or "")
        up_uid = str(item.get("up_uid") or "")
        up_name = str(item.get("up_name") or "")
        campaign_id = int(item["campaign_id"])  # type: ignore[arg-type]

        if not up_uid:
            message = "这条记录没有 UP 的 UID，无法取关"
            self._close_item(item_id, "skipped", message)
            return ExecLine(item_id, dyn_id, up_name, "unfollow", "skipped", message), False

        if not apply:
            return (
                ExecLine(
                    item_id, dyn_id, up_name, "unfollow", "planned", f"会取关 {up_name or up_uid}"
                ),
                False,
            )

        self._tidy.set_target(
            item_id,
            target=up_uid,
            snapshot=json.dumps({"up_name": up_name}, ensure_ascii=False),
        )
        step = self._journal.attempt(
            account_id=account_id,
            campaign_id=campaign_id,
            action=Action(ActionKind.UNFOLLOW, dyn_id, {"up_uid": up_uid}),
        )
        state, message, stop = _translate(step)
        self._close_item(item_id, state, message, target=up_uid)

        if state == "done":
            # 取关能回滚：重新关注。这条记录必须落库，否则"后悔"就只是一句话。
            self._tidy.record_undo(
                item_id,
                action="follow_back",
                target=up_uid,
                snapshot=json.dumps({"up_name": up_name}, ensure_ascii=False),
                now=self._clock.now(),
            )
        if stop:
            self._pause_on_risk(account_id, step)
        return ExecLine(item_id, dyn_id, up_name, "unfollow", state, message), stop

    # ---- 阶段四：回滚 ----
    def undo(self, item_id: int) -> UndoReport:
        record = self._tidy.undo_of(item_id)
        if record is None:
            return UndoReport(item_id, False, "这一条没有回滚记录（删转发的回不来）")
        if record.get("used_at"):
            return UndoReport(item_id, False, "这次回滚已经用过了")
        if str(record.get("action") or "") != "follow_back":
            return UndoReport(item_id, False, f"不支持的回滚方式：{record.get('action')}")

        target = str(record.get("target") or "")
        if not target:
            return UndoReport(item_id, False, "回滚记录缺少目标 UID")

        account_id = int(record.get("account_id") or 0)
        campaign_id = int(record.get("campaign_id") or 0)
        if not account_id:
            return UndoReport(item_id, False, "回滚记录找不到所属账号")

        step = self._journal.attempt(
            account_id=account_id,
            campaign_id=campaign_id,
            action=Action(ActionKind.FOLLOW, "", {"up_uid": target}),
        )
        if step.outcome is Outcome.SETTLED:
            self._tidy.mark_undo_used(int(record["id"]), now=self._clock.now())
            return UndoReport(item_id, True, "已重新关注（关注时间与分组无法恢复）")
        if step.outcome is Outcome.SKIPPED_PRESENT:
            self._tidy.mark_undo_used(int(record["id"]), now=self._clock.now())
            return UndoReport(item_id, True, "本来就还关注着，无需回滚")
        return UndoReport(item_id, False, step.message or "重新关注失败")

    # ---- 杂项 ----
    def _take_snapshot(self) -> str:
        """执行前留一份库快照。失败就停手 —— 没有凭据的删除不做。

        这不是"顺手备份"，是这条流程里唯一能证明"执行前清单长什么样"的东西：
        `tidy_item` 只记了每条的目标，不记当时的活动标题、截止时间、判定依据。
        删错了要复盘，靠的就是这份文件。
        """
        if self._snapshot is None:
            return ""
        try:
            return self._snapshot()
        except Exception as exc:
            raise TidyRejected(f"执行前备份失败，已停止：{exc}") from exc

    def _check_quota(self, account_id: int) -> None:
        if self._quota is None:
            return
        gate = self._quota.check(account_id)
        if gate.allowed:
            return
        # 清理不紧急，额度不够就等，不硬闯。
        raise TidyBlocked(f"写配额不足：{gate.explain()}")

    def _close_item(self, item_id: int, state: str, message: str, *, target: str = "") -> None:
        self._tidy.finish_item(
            item_id, state=state, reason=message, target=target, now=self._clock.now()
        )

    def _pause_on_risk(self, account_id: int, step: Step) -> None:
        if step.fault is Fault.RISK:
            self._gate.pause(account_id, "执行清理时遇到风控")

    def _wrap_up(self, plan_id: int, *, aborted: bool) -> str:
        pending = self._tidy.pending_items(plan_id)
        state = PlanState.ABORTED if (aborted or pending) else PlanState.DONE
        note = "" if not pending else f"还有 {len(pending)} 条没执行"
        self._tidy.finish_plan(plan_id, state=state.value, note=note, now=self._clock.now())
        return note


def _translate(step: Step) -> tuple[str, str, bool]:
    """把一次写操作的结果翻译成条目状态，并回答"要不要停下来"。"""
    if step.outcome is Outcome.SETTLED:
        return "done", step.message or "已完成", False
    if step.outcome is Outcome.SKIPPED_PRESENT:
        return "done", "此前已经完成", False
    if step.outcome is Outcome.DUPLICATE:
        return "skipped", step.message or "已有记录，未重复执行", False
    if step.outcome is Outcome.SKIPPED_UNKNOWN:
        return "skipped", step.message or "无法确认状态，已跳过", False
    if step.outcome is Outcome.UNKNOWN:
        return "failed", step.message or "结果未知", True
    if step.fault is Fault.RISK:
        return "failed", f"被风控：{step.message}", True
    return "failed", step.message or "远端拒绝", False


def _label(state: PlanState) -> str:
    return {
        PlanState.DRAFT: "草稿",
        PlanState.PREVIEWED: "已预演（未确认）",
        PlanState.CONFIRMED: "已确认",
        PlanState.RUNNING: "执行中",
        PlanState.DONE: "已完成",
        PlanState.ABORTED: "已中止",
    }.get(state, state.value)
