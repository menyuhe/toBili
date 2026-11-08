"""清理资格。这是唯一会造成不可恢复损失的功能，规则必须逐条锁死。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tobili.domain.tidy import (
    Outcome,
    TidyAction,
    TidyCandidate,
    TidyPolicy,
    judge,
    plan_order,
)

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
POLICY = TidyPolicy()


def candidate(**overrides: object) -> TidyCandidate:
    base = {
        "enrollment_id": 1,
        "dyn_id": "dyn-1",
        "up_uid": "2002",
        "outcome": Outcome.LOST,
        "decided_at": NOW - timedelta(hours=48),
    }
    base.update(overrides)
    return TidyCandidate(**base)  # type: ignore[arg-type]


def test_lost_after_grace_is_allowed() -> None:
    verdict = judge(candidate(), now=NOW, policy=POLICY)
    assert verdict.allowed and verdict.action is TidyAction.DROP_SHARE


def test_within_grace_is_refused() -> None:
    verdict = judge(candidate(decided_at=NOW - timedelta(hours=2)), now=NOW, policy=POLICY)
    assert not verdict.allowed
    assert "宽限期" in verdict.reason


def test_won_is_never_cleaned() -> None:
    verdict = judge(candidate(outcome=Outcome.WON), now=NOW, policy=POLICY)
    assert not verdict.allowed


def test_pending_is_never_cleaned() -> None:
    assert not judge(candidate(outcome=Outcome.PENDING), now=NOW, policy=POLICY)


def test_missing_decided_at_is_refused() -> None:
    verdict = judge(candidate(decided_at=None), now=NOW, policy=POLICY)
    assert not verdict.allowed and "判定时间" in verdict.reason


def test_old_records_are_frozen() -> None:
    old = NOW - timedelta(days=POLICY.freeze_days + 1)
    verdict = judge(candidate(decided_at=old), now=NOW, policy=POLICY)
    assert not verdict.allowed and "冻结" in verdict.reason


def test_per_up_limit_blocks_third_one() -> None:
    verdict = judge(candidate(), now=NOW, policy=POLICY, up_deleted_in_round=POLICY.max_per_up)
    assert not verdict.allowed and "上限" in verdict.reason


def test_already_cleaned_is_skipped() -> None:
    assert not judge(candidate(already_cleaned=True), now=NOW, policy=POLICY)


def test_reason_is_always_human_readable() -> None:
    for cand in (candidate(), candidate(outcome=Outcome.WON), candidate(already_cleaned=True)):
        verdict = judge(cand, now=NOW, policy=POLICY)
        assert verdict.reason and len(verdict.reason) > 3


def test_plan_order_is_oldest_first() -> None:
    newer = candidate(enrollment_id=2, decided_at=NOW - timedelta(days=2))
    older = candidate(enrollment_id=3, decided_at=NOW - timedelta(days=5))
    assert [c.enrollment_id for c in plan_order([newer, older])] == [3, 2]
