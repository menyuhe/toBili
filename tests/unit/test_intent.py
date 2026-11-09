"""意图表的幂等性。这里验证的是数据库层面的事实，不是逻辑判断。"""

from __future__ import annotations

from tobili.domain.action import ActionKind
from tobili.domain.campaign import Campaign


def _campaign_id(repos) -> int:  # type: ignore[no-untyped-def]
    return repos.campaigns.upsert(Campaign(dyn_id="dyn-1", up_uid="2002", title="测试活动"))


def test_second_reserve_is_not_created(repos, account_id, clock) -> None:  # type: ignore[no-untyped-def]
    campaign_id = _campaign_id(repos)
    first, created_first = repos.intents.reserve(
        account_id=account_id,
        campaign_id=campaign_id,
        action=ActionKind.SHARE,
        fingerprint="fp-1",
        content="转发抽奖",
        now=clock.now(),
    )
    second, created_second = repos.intents.reserve(
        account_id=account_id,
        campaign_id=campaign_id,
        action=ActionKind.SHARE,
        fingerprint="fp-1",
        content="转发抽奖",
        now=clock.now(),
    )
    assert created_first is True
    assert created_second is False
    assert first == second


def test_changed_comment_text_cannot_bypass_participation_deduplication(repos, account_id, clock) -> None:  # type: ignore[no-untyped-def]
    campaign_id = _campaign_id(repos)
    _id1, created1 = repos.intents.reserve(
        account_id=account_id,
        campaign_id=campaign_id,
        action=ActionKind.COMMENT,
        fingerprint="fp-a",
        content="恭喜",
        now=clock.now(),
    )
    _id2, created2 = repos.intents.reserve(
        account_id=account_id,
        campaign_id=campaign_id,
        action=ActionKind.COMMENT,
        fingerprint="fp-b",
        content="恭喜恭喜",
        now=clock.now(),
    )
    assert created1 and not created2
    assert _id1 == _id2


def test_settle_updates_state(repos, account_id, clock) -> None:  # type: ignore[no-untyped-def]
    campaign_id = _campaign_id(repos)
    intent_id, _created = repos.intents.reserve(
        account_id=account_id,
        campaign_id=campaign_id,
        action=ActionKind.LIKE,
        fingerprint="fp-like",
        content="",
        now=clock.now(),
    )
    assert repos.intents.state_of(intent_id) == "reserved"
    repos.intents.settle(
        intent_id, ok=True, ambiguous=False, fault="", message="ok", now=clock.now()
    )
    assert repos.intents.state_of(intent_id) == "done"


def test_ambiguous_write_is_tracked_as_unresolved(repos, account_id, clock) -> None:  # type: ignore[no-untyped-def]
    campaign_id = _campaign_id(repos)
    intent_id, _ = repos.intents.reserve(
        account_id=account_id,
        campaign_id=campaign_id,
        action=ActionKind.SHARE,
        fingerprint="fp-share",
        content="转发抽奖",
        now=clock.now(),
    )
    repos.intents.settle(
        intent_id, ok=False, ambiguous=True, fault="timeout", message="超时", now=clock.now()
    )
    assert repos.intents.state_of(intent_id) == "unknown"
    assert [row["id"] for row in repos.intents.unresolved(account_id)] == [intent_id]


def test_cascade_delete_removes_intents(repos, account_id, clock) -> None:  # type: ignore[no-untyped-def]
    """外键真的生效：删掉活动，相关意图跟着走。"""
    campaign_id = _campaign_id(repos)
    repos.intents.reserve(
        account_id=account_id,
        campaign_id=campaign_id,
        action=ActionKind.SHARE,
        fingerprint="fp-x",
        content="",
        now=clock.now(),
    )
    with repos.db.write() as conn:
        conn.execute("DELETE FROM campaign WHERE id = ?", (campaign_id,))
    rows = (
        repos.db.connect()
        .execute("SELECT COUNT(*) AS n FROM intent WHERE campaign_id = ?", (campaign_id,))
        .fetchone()
    )
    assert rows["n"] == 0
