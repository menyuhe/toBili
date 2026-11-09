"""意图日志。重点是"不该写的时候一次都不发"。"""

from __future__ import annotations

from tobili.domain.action import Action, ActionKind
from tobili.domain.campaign import Campaign
from tobili.domain.probe import ProbeState
from tobili.domain.receipt import Receipt
from tobili.runtime.journal import Journal, Outcome


def _setup(repos, gateway, clock, account_id):  # type: ignore[no-untyped-def]
    campaign_id = repos.campaigns.upsert(Campaign(dyn_id="dyn-1", up_uid="2002", title="测试活动"))
    journal = Journal(gateway=gateway, intents=repos.intents, clock=clock)
    action = Action(ActionKind.SHARE, "dyn-1", {"text": "转发抽奖"})
    return journal, campaign_id, action


def test_unknown_probe_never_writes(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    gateway.probe = ProbeState.UNKNOWN
    journal, campaign_id, action = _setup(repos, gateway, clock, account_id)

    step = journal.attempt(account_id=account_id, campaign_id=campaign_id, action=action)

    assert step.outcome is Outcome.SKIPPED_UNKNOWN
    assert gateway.writes == []
    assert repos.intents.unresolved(account_id) == []


def test_present_probe_never_writes(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    gateway.probe = ProbeState.PRESENT
    journal, campaign_id, action = _setup(repos, gateway, clock, account_id)

    step = journal.attempt(account_id=account_id, campaign_id=campaign_id, action=action)

    assert step.outcome is Outcome.SKIPPED_PRESENT
    assert gateway.writes == []


def test_absent_probe_writes_exactly_once(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    gateway.probe = ProbeState.ABSENT
    journal, campaign_id, action = _setup(repos, gateway, clock, account_id)

    step = journal.attempt(
        account_id=account_id, campaign_id=campaign_id, action=action, content="转发抽奖"
    )

    assert step.outcome is Outcome.SETTLED
    assert len(gateway.writes) == 1


def test_second_attempt_is_duplicate_not_rewrite(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    """探测仍然说 ABSENT 也不该再写 —— 意图表才是最终裁决。"""
    gateway.probe = ProbeState.ABSENT
    journal, campaign_id, action = _setup(repos, gateway, clock, account_id)

    journal.attempt(account_id=account_id, campaign_id=campaign_id, action=action)
    second = journal.attempt(account_id=account_id, campaign_id=campaign_id, action=action)

    assert second.outcome is Outcome.DUPLICATE
    assert len(gateway.writes) == 1


def test_ambiguous_receipt_is_recorded_as_unresolved(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    gateway.probe = ProbeState.ABSENT
    gateway.receipt = Receipt.unclear("连接中断")
    journal, campaign_id, action = _setup(repos, gateway, clock, account_id)

    step = journal.attempt(account_id=account_id, campaign_id=campaign_id, action=action)

    assert step.outcome is Outcome.UNKNOWN
    assert step.receipt is not None and step.receipt.ambiguous
    assert len(repos.intents.unresolved(account_id)) == 1


def test_rejected_receipt_marks_failure(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    from tobili.domain.errors import Fault

    gateway.probe = ProbeState.ABSENT
    gateway.receipt = Receipt.rejected(Fault.RISK, "风控拦截")
    journal, campaign_id, action = _setup(repos, gateway, clock, account_id)

    step = journal.attempt(account_id=account_id, campaign_id=campaign_id, action=action)

    assert step.outcome is Outcome.REJECTED
    assert step.fault is Fault.RISK


def test_inspect_does_not_write_or_reserve(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    gateway.probe = ProbeState.ABSENT
    journal, _campaign_id, action = _setup(repos, gateway, clock, account_id)

    step = journal.inspect(account_id=account_id, action=action)

    assert step.outcome is Outcome.SETTLED
    assert gateway.writes == []
    assert repos.intents.unresolved(account_id) == []


def test_events_are_published(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    from tobili.runtime.events import EventBus

    bus = EventBus()
    gateway.probe = ProbeState.ABSENT
    campaign_id = repos.campaigns.upsert(Campaign(dyn_id="dyn-1", up_uid="2002", title="t"))
    journal = Journal(gateway=gateway, intents=repos.intents, clock=clock, events=bus)
    action = Action(ActionKind.LIKE, "dyn-1", {})

    journal.attempt(account_id=account_id, campaign_id=campaign_id, action=action)

    topics = [e.topic for e in bus.since(0)]
    assert topics == ["probe", "write"]


def test_success_record_precedes_unavailable_remote_probe(repos, gateway, clock, account_id):
    journal, campaign_id, action = _setup(repos, gateway, clock, account_id)
    journal.attempt(account_id=account_id, campaign_id=campaign_id, action=action)
    gateway.probes.clear()
    gateway.probe = ProbeState.UNKNOWN
    second = journal.attempt(account_id=account_id, campaign_id=campaign_id, action=action)
    inspected = journal.inspect(account_id=account_id, campaign_id=campaign_id, action=action)
    assert second.outcome is Outcome.DUPLICATE and inspected.outcome is Outcome.DUPLICATE
    assert not gateway.probes and len(gateway.writes) == 1


def test_unresolved_send_stays_blocked_even_if_remote_probe_unavailable(repos, gateway, clock, account_id):
    journal, campaign_id, action = _setup(repos, gateway, clock, account_id)
    gateway.receipt = Receipt.unclear('结果未知')
    journal.attempt(account_id=account_id, campaign_id=campaign_id, action=action)
    gateway.probes.clear()
    gateway.probe = ProbeState.UNKNOWN
    step = journal.attempt(account_id=account_id, campaign_id=campaign_id, action=action)
    assert step.outcome is Outcome.UNKNOWN and not gateway.probes and len(gateway.writes) == 1
