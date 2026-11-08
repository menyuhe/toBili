"""清理闸门。

这个文件测的不是"能不能清理"，而是"什么情况下必须不许清理"。每一条关闭理由都
要能被用户读懂，并且能自己解开 —— 一个说不清、也解不开的刹车，用户会绕过它。
"""

from __future__ import annotations

from tobili.domain.gate import (
    CleaningSignals,
    GatePolicy,
    decide,
)


def test_nothing_wrong_means_open() -> None:
    assert decide(CleaningSignals()).open is True


def test_manual_pause_wins_over_everything() -> None:
    judgement = decide(CleaningSignals(paused=True, pause_reason="在核对数据"))
    assert judgement.open is False
    assert "在核对数据" in judgement.reason
    assert "resume" in judgement.hint


def test_single_risk_hit_closes_the_gate() -> None:
    """风控不是"这一次失败"，是"这一段时间别动"。一次就够。"""
    judgement = decide(CleaningSignals(risk_hits=1))
    assert judgement.open is False
    assert "风控" in judgement.reason


def test_unread_direct_message_closes_the_gate() -> None:
    judgement = decide(CleaningSignals(unread_chat=2))
    assert judgement.open is False
    assert "私信" in judgement.reason


def test_acked_messages_do_not_block_forever() -> None:
    """看过之后要把刹车松开，否则这个开关会让功能永久失效。"""
    assert decide(CleaningSignals(unread_chat=2, acked_chat=2)).open is True


def test_new_message_after_ack_closes_again() -> None:
    assert decide(CleaningSignals(unread_chat=3, acked_chat=2)).open is False


def test_unread_system_notice_closes_the_gate() -> None:
    assert decide(CleaningSignals(unread_system=1)).open is False


def test_unreadable_messages_are_treated_as_closed() -> None:
    """读不到消息 = 无法排除坏消息。这个方向写错一次，代价是删错东西。"""
    judgement = decide(CleaningSignals(readable=False))
    assert judgement.open is False
    assert "读取失败" in judgement.reason


def test_reply_and_like_counts_never_block() -> None:
    """回复和点赞数量大、噪声多，只用来展示，不参与拦截。"""
    assert decide(CleaningSignals()).open is True


def test_policy_can_relax_the_gate() -> None:
    relaxed = GatePolicy(block_on_chat=False, block_on_system=False)
    assert decide(CleaningSignals(unread_chat=5, unread_system=5), relaxed).open is True
