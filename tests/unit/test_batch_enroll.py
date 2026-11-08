"""批量参与。

这里测的是"什么时候不该做"，而不是"能不能做"：能做的路径很窄，做错的代价很宽。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.enrollment import Stage
from tobili.domain.throttle import Verdict
from tobili.runtime.journal import Journal
from tobili.runtime.quota import Gate
from tobili.usecases.batch_enroll import BatchEnroll
from tobili.usecases.enroll import Enroll

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def test_enroll_recheck_uses_stored_publication_not_today(repos, gateway, clock, account_id):
    from dataclasses import replace

    published = datetime(2026, 10, 4, 4, tzinfo=UTC)
    repos.campaigns.upsert(
        Campaign(dyn_id="12345", up_uid="2002", title="抽奖", published_at=published)
    )
    original = gateway.detail
    gateway.detail = lambda dyn: replace(
        original(dyn), text="转发抽奖，奖品为画册，下周五晚上八点开奖", published_at=None
    )
    enroll = Enroll(
        gateway=gateway,
        campaigns=repos.campaigns,
        enrollments=repos.enrollments,
        journal=Journal(gateway=gateway, intents=repos.intents, clock=clock),
        clock=clock,
    )
    assert enroll.run(account_id, "12345", dry_run=True).stage is Stage.SETTLED
    card = repos.campaigns.by_dyn("12345")[1]
    assert card.deadline.isoformat() == "2026-10-09T12:00:00+00:00"
    assert card.published_at == published
    assert not gateway.writes


class ScriptedQuota:
    """按脚本回答"还能不能写"。用于验证配额见底时整轮停下。"""

    def __init__(self, allow: int) -> None:
        self._left = allow
        self.checks = 0

    def check(self, account_id: int) -> Gate:
        self.checks += 1
        if self._left <= 0:
            return Gate(verdict=Verdict.STOP, wait_seconds=0, last_day=20)
        self._left -= 1
        return Gate(verdict=Verdict.GO, wait_seconds=0)


def _campaign(dyn_id: str, *, minutes: int | None, confidence: float = 0.9) -> Campaign:
    deadline = NOW + timedelta(minutes=minutes) if minutes is not None else None
    return Campaign(
        dyn_id=dyn_id,
        up_uid="2002",
        title="转发抽奖",
        kind=CampaignKind.SHARE,
        up_name="某个 UP",
        deadline=deadline,
        confidence=confidence,
        seen_at=NOW,
    )


def _batch(repos, gateway, clock, quota=None):  # type: ignore[no-untyped-def]
    enroll = Enroll(
        gateway=gateway,
        campaigns=repos.campaigns,
        enrollments=repos.enrollments,
        journal=Journal(gateway=gateway, intents=repos.intents, clock=clock),
        clock=clock,
        quota=quota,
    )
    return BatchEnroll(campaigns=repos.campaigns, enroll=enroll, clock=clock, quota=quota)


def test_nothing_to_do(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    report = _batch(repos, gateway, clock).run(account_id)
    assert report.candidates == 0
    assert report.ok


def test_stop_callback_prevents_next_real_attempt(repos, gateway, clock, account_id):
    repos.campaigns.upsert(_campaign("dyn-1", minutes=100))
    repos.campaigns.upsert(_campaign("dyn-2", minutes=200))
    result = _batch(repos, gateway, clock).run(
        account_id, dry_run=False, before_each=lambda i: "stopped" if i else ""
    )
    assert result.attempted == 1 and result.stop_reason == "stopped"
    rows = repos.db.connect().execute("SELECT stage FROM enrollment").fetchall()
    assert len(rows) == 1 and rows[0]["stage"] == Stage.SETTLED.value


def test_queue_stop_keeps_remaining_items_pending(repos, gateway, clock, account_id):
    from types import SimpleNamespace

    from tobili.domain.pool import PoolOp
    from tobili.usecases.pool import ManagePool, RunPool

    ids = [repos.campaigns.upsert(_campaign(f"dyn-{i}", minutes=100)) for i in (1, 2)]
    ManagePool(
        pool=repos.pool, campaigns=repos.campaigns, enrollments=repos.enrollments, clock=clock
    ).edit(account_id, PoolOp.ADD, campaign_ids=ids)
    calls = []
    enroll = SimpleNamespace(
        run=lambda *a, **kw: calls.append(a) or SimpleNamespace(stage=Stage.SETTLED)
    )
    result = RunPool(pool=repos.pool, enroll=lambda: enroll, clock=clock).run(
        account_id, apply=True, before_each=lambda i: "stopped" if i else ""
    )
    assert len(calls) == 1 and result.note == "stopped" and result.pending == 1
    assert repos.pool.states(account_id)[0].campaign_id == ids[1]


def test_orders_by_deadline_urgency(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    repos.campaigns.upsert(_campaign("late", minutes=5000))
    repos.campaigns.upsert(_campaign("soon", minutes=30))
    repos.campaigns.upsert(_campaign("no-deadline", minutes=None))

    report = _batch(repos, gateway, clock).run(account_id, dry_run=True)

    # 快过期的先做；没有截止时间的排最后（它不紧急，而不是"最紧急"）。
    assert [line.dyn_id for line in report.lines] == ["soon", "late", "no-deadline"]


def test_skips_low_confidence_and_unknown_kind(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    repos.campaigns.upsert(_campaign("vague", minutes=100, confidence=0.4))
    repos.campaigns.upsert(
        Campaign(
            dyn_id="unknown-kind",
            up_uid="2002",
            title="可能是活动",
            kind=CampaignKind.UNKNOWN,
            confidence=0.95,
            seen_at=NOW,
        )
    )

    report = _batch(repos, gateway, clock).run(account_id, dry_run=True)
    assert report.candidates == 0


def test_skips_expired(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    repos.campaigns.upsert(_campaign("gone", minutes=-60))
    report = _batch(repos, gateway, clock).run(account_id, dry_run=True)
    assert report.candidates == 0


def test_dry_run_writes_nothing(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    repos.campaigns.upsert(_campaign("dyn-1", minutes=100))

    report = _batch(repos, gateway, clock).run(account_id, dry_run=True)

    assert report.attempted == 1
    assert gateway.writes == []
    assert report.planned == 1


def test_apply_executes_and_settles(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    repos.campaigns.upsert(_campaign("dyn-1", minutes=100))

    report = _batch(repos, gateway, clock, quota=ScriptedQuota(allow=10)).run(
        account_id, dry_run=False
    )

    assert report.attempted == 1
    assert report.settled == 1
    assert gateway.writes, "应当真的发起了写操作"
    rows = repos.db.connect().execute("SELECT stage FROM enrollment").fetchall()
    assert [r["stage"] for r in rows] == [Stage.SETTLED.value]


def test_planned_rows_are_picked_up_again(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    """预演过一次的活动，正式跑的时候必须还能被选中。"""
    repos.campaigns.upsert(_campaign("dyn-1", minutes=100))
    usecase = _batch(repos, gateway, clock, quota=ScriptedQuota(allow=10))

    usecase.run(account_id, dry_run=True)
    second = usecase.run(account_id, dry_run=False)

    assert second.candidates == 1
    assert second.settled == 1


def test_blocked_campaigns_are_not_retried(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    """已经判定"需要人看一眼"的活动，不自动重试。"""
    repos.campaigns.upsert(_campaign("dyn-1", minutes=100))
    campaign_id = repos.campaigns.by_dyn("dyn-1")[0]
    enrollment_id = repos.enrollments.create(account_id, campaign_id, now=NOW)
    repos.enrollments.move(enrollment_id, Stage.RUNNING, now=NOW)
    repos.enrollments.move(enrollment_id, Stage.BLOCKED, now=NOW, reason="要求看不懂")

    report = _batch(repos, gateway, clock).run(account_id, dry_run=False)
    assert report.candidates == 0


def test_single_failure_does_not_stop_the_round(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    repos.campaigns.upsert(_campaign("bad", minutes=10))
    repos.campaigns.upsert(_campaign("good", minutes=20))
    gateway.detail_fails = {"bad"}

    report = _batch(repos, gateway, clock, quota=ScriptedQuota(allow=10)).run(
        account_id, dry_run=False
    )

    assert report.attempted == 2
    assert report.failed == 1
    assert report.settled == 1
    assert any("bad" in line.dyn_id for line in report.lines)
    assert report.problems


def test_scheduled_write_stops_at_first_failure(repos, gateway, clock, account_id):
    repos.campaigns.upsert(_campaign("bad", minutes=10))
    repos.campaigns.upsert(_campaign("good", minutes=20))
    gateway.detail_fails = {"bad"}
    report = _batch(repos, gateway, clock).run(account_id, dry_run=False, stop_on_failure=True)
    assert report.attempted == 1 and report.failed == 1 and report.stop_reason
    assert not gateway.writes


def test_quota_stop_ends_the_round_instead_of_skipping(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    for index in range(3):
        repos.campaigns.upsert(_campaign(f"dyn-{index}", minutes=10 + index))

    report = _batch(repos, gateway, clock, quota=ScriptedQuota(allow=1)).run(
        account_id, dry_run=False
    )

    # 只做了第一条，后面的主动放弃，而不是一路空转把配额撞到底。
    assert report.attempted == 1
    assert report.stop_reason
    assert report.unfinished == 2


def test_limit_is_respected(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    for index in range(5):
        repos.campaigns.upsert(_campaign(f"dyn-{index}", minutes=10 + index))

    report = _batch(repos, gateway, clock).run(account_id, limit=2, dry_run=True)
    assert report.candidates == 2
