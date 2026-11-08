"""清理闸门。

清理是本项目里**唯一会造成不可恢复损失**的动作，所以它需要一道刹车，而不是一个
"是否继续执行"的确认框。确认框只在开始时出现一次，刹车要在整个过程里随时生效。

刹车由四路信号驱动，任何一路亮红灯，整轮清理立即停止：

1. 手动暂停开关 —— 人觉得不对劲时按下的那个按钮。
2. 近期风控 —— 最近若干小时的写操作里出现过风控拒绝或请求被拦截。风控不是"这一
   次失败"，它是"这一段时间别动"。
3. 未读私信 —— 站内信、UP 主私信、官方通知都在这里。
4. 未读系统通知 —— 运营推送也在里面，所以这一条允许"我看到了，继续"。

信号读取失败（消息中心打不开）按**关闭**处理：没办法确认没有坏消息，就等于不能
排除。这个方向写错一次，代价是删掉不该删的东西。

这里不读库、不看时钟、不发请求，所有输入都是参数，所以每一条规则都能单独验证。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CleaningSignals:
    """一次判定的全部输入。`readable` 为假表示消息中心没读到。"""

    paused: bool = False
    pause_reason: str = ""
    risk_hits: int = 0
    unread_chat: int = 0
    unread_system: int = 0
    acked_chat: int = 0
    acked_system: int = 0
    readable: bool = True


@dataclass(frozen=True, slots=True)
class GatePolicy:
    """阈值。风控 0 容忍：一次就够停手了。"""

    max_risk_hits: int = 0
    risk_window_hours: int = 6
    block_on_chat: bool = True
    block_on_system: bool = True


@dataclass(frozen=True, slots=True)
class GateDecision:
    open: bool
    reason: str = ""
    hint: str = ""

    def __bool__(self) -> bool:
        return self.open

    def explain(self) -> str:
        if self.open:
            return "闸门开放，可以执行"
        return f"{self.reason}（{self.hint}）" if self.hint else self.reason


ALLOW = GateDecision(True, "没有拦截信号")


def decide(signals: CleaningSignals, policy: GatePolicy | None = None) -> GateDecision:
    policy = policy or GatePolicy()

    if signals.paused:
        reason = signals.pause_reason or "已手动暂停"
        return GateDecision(False, f"清理已暂停：{reason}", "确认无异常后执行 tidy resume")

    if signals.risk_hits > policy.max_risk_hits:
        return GateDecision(
            False,
            f"最近出现过 {signals.risk_hits} 次风控拒绝",
            "等风控窗口过去（默认 6 小时）再试，期间不要去操作账号",
        )

    if not signals.readable:
        return GateDecision(
            False,
            "消息中心读取失败，无法确认是否有站内信",
            "检查网络后重试；这一步不做降级",
        )

    if policy.block_on_chat and signals.unread_chat > signals.acked_chat:
        return GateDecision(
            False,
            f"有 {signals.unread_chat} 条未读私信",
            "看完私信后执行 tidy ack 记一次人工确认",
        )

    if policy.block_on_system and signals.unread_system > signals.acked_system:
        return GateDecision(
            False,
            f"有 {signals.unread_system} 条未读系统通知",
            "看完后执行 tidy ack；确认不影响则继续",
        )

    return ALLOW
