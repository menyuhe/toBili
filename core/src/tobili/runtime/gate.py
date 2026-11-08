"""闸门接线。

`domain.gate` 只做判断，这里负责把四路信号凑齐。凑不齐的那一路（消息中心打不开）
直接记成"不可读"，判定结果就是关闭 —— 这个降级方向是刻意的：没办法确认没有坏
消息，不等于没有坏消息。

暂停开关和"我看过了"都落在 `setting` 表里，跟着账号走。它们不是运行期状态，
进程重启之后仍然有效；否则一次重启就把刹车松开了。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from ..domain.gate import CleaningSignals, GateDecision, GatePolicy, decide
from ..ports.bili import Reader
from ..ports.collab import Clock
from ..ports.repo import IntentRepo, SettingRepo

PAUSED = "tidy.paused"
PAUSE_REASON = "tidy.pause_reason"
ACK = "tidy.msg_ack"  # "chat,system" 两个数


@dataclass(frozen=True, slots=True)
class GateSnapshot:
    decision: GateDecision
    signals: CleaningSignals

    @property
    def open(self) -> bool:
        return self.decision.open


class CleaningGate:
    def __init__(
        self,
        *,
        gateway: Reader,
        intents: IntentRepo,
        settings: SettingRepo,
        clock: Clock,
        policy: GatePolicy | None = None,
    ) -> None:
        self._gateway = gateway
        self._intents = intents
        self._settings = settings
        self._clock = clock
        self._policy = policy or GatePolicy()

    @property
    def policy(self) -> GatePolicy:
        return self._policy

    def inspect(self, account_id: int) -> GateSnapshot:
        now = self._clock.now()
        window = now - timedelta(hours=self._policy.risk_window_hours)
        risk = self._intents.risk_hits_since(account_id, since=window)

        chat = system = 0
        readable = True
        try:
            stat = self._gateway.unread()
            chat, system = stat.chat, stat.system
        except Exception:  # 任何异常都算"读不到"，不区分原因
            readable = False

        acked_chat, acked_system = _parse_ack(self._settings.get(account_id, ACK, ""))
        signals = CleaningSignals(
            paused=self._settings.get(account_id, PAUSED, "0") == "1",
            pause_reason=self._settings.get(account_id, PAUSE_REASON, ""),
            risk_hits=risk,
            unread_chat=chat,
            unread_system=system,
            acked_chat=acked_chat,
            acked_system=acked_system,
            readable=readable,
        )
        return GateSnapshot(decision=decide(signals, self._policy), signals=signals)

    def check(self, account_id: int) -> GateDecision:
        return self.inspect(account_id).decision

    # ---- 刹车 ----
    def pause(self, account_id: int, reason: str = "") -> None:
        self._settings.put(account_id, PAUSED, "1")
        self._settings.put(account_id, PAUSE_REASON, reason.strip() or "手动暂停")

    def resume(self, account_id: int) -> None:
        self._settings.put(account_id, PAUSED, "0")
        self._settings.put(account_id, PAUSE_REASON, "")

    def is_paused(self, account_id: int) -> bool:
        return self._settings.get(account_id, PAUSED, "0") == "1"

    def ack(self, account_id: int) -> tuple[int, int]:
        """ "这些消息我看过了"。记录当时的未读数，不作为永久豁免。

        下次未读数再涨（来了新消息），仍然会拦住。所以这不是关掉闸门，是承认
        已经看过这一批。
        """
        try:
            stat = self._gateway.unread()
        except Exception:
            return (0, 0)
        self._settings.put(account_id, ACK, f"{stat.chat},{stat.system}")
        return stat.chat, stat.system


def _parse_ack(raw: str) -> tuple[int, int]:
    parts = (raw or "").split(",")
    if len(parts) != 2:
        return (0, 0)
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return (0, 0)
