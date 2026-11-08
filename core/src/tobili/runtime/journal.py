"""意图日志：写操作的唯一合法入口。

顺序是固定的，而且不能调换：

    探测 → (仅 ABSENT) → 占位 → 单次发送 → 回写结果

为什么必须先占位再发送？因为如果反过来（先发再记账），进程在两步之间被杀死，
重启后就没有任何证据表明"这条已经发过了"，于是会再发一次。先占位则相反：
即使发送前崩溃，重启后看到 `reserved` 状态，也会停下来等人看，而不是重发。

`DUPLICATE` 不是一个错误分支，它是这个设计正常工作的样子。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from enum import StrEnum

from ..domain.action import Action
from ..domain.errors import Fault
from ..domain.probe import ProbeState, may_write
from ..domain.receipt import Receipt
from ..ports.bili import Gateway
from ..ports.collab import Clock, Events
from ..ports.repo import IntentRepo


class Outcome(StrEnum):
    SETTLED = "settled"
    SKIPPED_PRESENT = "skipped_present"
    SKIPPED_UNKNOWN = "skipped_unknown"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Step:
    outcome: Outcome
    probe: ProbeState
    message: str
    receipt: Receipt | None = None
    fault: Fault | None = None

    @property
    def wrote(self) -> bool:
        return self.receipt is not None


class Journal:
    def __init__(
        self,
        *,
        gateway: Gateway,
        intents: IntentRepo,
        clock: Clock,
        events: Events | None = None,
        preflight: Callable[[int], None] | None = None,
        write_scope: Callable[[int], AbstractContextManager] | None = None,
        action_guard=None,
    ) -> None:
        self._gateway = gateway
        self._intents = intents
        self._clock = clock
        self._events = events
        self._write_scope = write_scope
        self.preflight = preflight
        self.action_guard = action_guard

    def attempt(
        self,
        *,
        account_id: int,
        campaign_id: int,
        action: Action,
        content: str = "",
        probe_payload: Mapping[str, str] | None = None,
    ) -> Step:
        with self._write_scope(account_id) if self._write_scope else nullcontext():
            return self._attempt(
                account_id=account_id,
                campaign_id=campaign_id,
                action=action,
                content=content,
                probe_payload=probe_payload,
            )

    def _attempt(
        self,
        *,
        account_id: int,
        campaign_id: int,
        action: Action,
        content: str = "",
        probe_payload: Mapping[str, str] | None = None,
    ) -> Step:
        now = self._clock.now()
        if self.action_guard:
            self.action_guard(campaign_id, action)
        probe = self._gateway.did(
            action.kind, dyn_id=action.dyn_id, payload=dict(probe_payload or action.payload)
        )
        self._emit("probe", action, account_id, state=probe.value)

        if probe is ProbeState.PRESENT:
            return Step(Outcome.SKIPPED_PRESENT, probe, "已做过，跳过")
        if not may_write(probe):
            return Step(
                Outcome.SKIPPED_UNKNOWN,
                probe,
                "无法确认是否已做过，为避免重复已跳过",
            )

        if self.action_guard:
            self.action_guard(campaign_id, action)
        intent_id, created = self._intents.reserve(
            account_id=account_id,
            campaign_id=campaign_id,
            action=action.kind,
            fingerprint=action.fingerprint,
            content=content,
            now=now,
        )
        if not created:
            previous = self._intents.state_of(intent_id)
            if previous in ("reserved", "unknown"):
                return Step(
                    Outcome.UNKNOWN, probe, "已有发送记录但结果未确认，需要核对；不会重复发送"
                )
            if previous == "rejected":
                return Step(Outcome.REJECTED, probe, "上次操作被拒绝，需要处理原错误；不会自动重发")
            return Step(
                Outcome.DUPLICATE,
                probe,
                f"这条动作已有记录（{previous or '未知'}），不会重复执行",
            )

        receipt = self._gateway.perform(action)
        self._intents.settle(
            intent_id,
            ok=receipt.ok,
            ambiguous=receipt.ambiguous,
            fault=receipt.fault.value if receipt.fault else "",
            message=receipt.message,
            now=self._clock.now(),
        )
        self._emit(
            "write",
            action,
            account_id,
            ok=receipt.ok,
            ambiguous=receipt.ambiguous,
            message=receipt.message,
        )

        if receipt.ok:
            return Step(Outcome.SETTLED, probe, receipt.message or "已完成", receipt)
        if receipt.ambiguous:
            return Step(
                Outcome.UNKNOWN,
                probe,
                "请求已发出但结果未知，已记录，下次运行会先探测",
                receipt,
                receipt.fault,
            )
        return Step(Outcome.REJECTED, probe, receipt.message or "远端拒绝", receipt, receipt.fault)

    def inspect(
        self,
        *,
        account_id: int,
        action: Action,
        probe_payload: Mapping[str, str] | None = None,
    ) -> Step:
        """只探测，不写。预演与"这条能不能做"的界面用它。

        注意它**不会**占位，所以不能拿它代替 `attempt` —— 预演看到"可以做"，
        到真正执行时还要再走一遍完整流程。
        """
        probe = self._gateway.did(
            action.kind, dyn_id=action.dyn_id, payload=dict(probe_payload or action.payload)
        )
        self._emit("probe", action, account_id, state=probe.value)
        if probe is ProbeState.PRESENT:
            return Step(Outcome.SKIPPED_PRESENT, probe, "已做过，跳过")
        if not may_write(probe):
            return Step(Outcome.SKIPPED_UNKNOWN, probe, "无法确认是否已做过")
        return Step(Outcome.SETTLED, probe, "确认未做过，可以执行")

    def _emit(self, topic: str, action: Action, account_id: int, **fields: object) -> None:
        if self._events is None:
            return
        self._events.publish(
            topic,
            {
                "account_id": account_id,
                "action": action.kind.value,
                "dyn_id": action.dyn_id,
                **fields,
            },
        )
