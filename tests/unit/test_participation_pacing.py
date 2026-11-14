import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.enrollment import Stage
from tobili.domain.pool import PoolOp
from tobili.domain.throttle import Budget, Usage, Verdict, assess
from tobili.runtime.lane import BusyAccount
from tobili.runtime.quota import Gate
from tobili.runtime.source_refresh import SourceRefresh
from tobili.runtime.work_priority import participation_turn, requested
from tobili.usecases.batch_enroll import BatchEnroll
from tobili.usecases.pool import ManagePool, RunPool


@pytest.mark.parametrize("mode", ["queue", "catalog"])
@pytest.mark.parametrize("interrupt", ["", "pause", "remove", "cancel"])
def test_short_gap_continues_and_wait_is_interruptible(repos, clock, account_id, mode, interrupt):
    if mode == "catalog" and interrupt in ("pause", "remove"):
        pytest.skip("Catalog has no editable queue")
    ids = [
        repos.campaigns.upsert(
            Campaign(
                dyn_id=str(i),
                up_uid="2",
                title="活动",
                reward="鼠标",
                kind=CampaignKind.SHARE,
                confidence=0.95,
                seen_at=clock.now(),
            )
        )
        for i in (1, 2)
    ]
    ManagePool(
        pool=repos.pool, campaigns=repos.campaigns, enrollments=repos.enrollments, clock=clock
    ).edit(account_id, PoolOp.ADD, campaign_ids=ids)
    now = [0.0]
    writes = []
    cancel = [False]
    progress = []

    class PacedQuota:
        budget = Budget(per_hour=0, per_day=0)

        def check(self, owner):
            remaining = 15 - (now[0] - writes[-1][1]) if writes else 0
            return Gate(Verdict.HOLD if remaining > 0 else Verdict.GO, max(0, remaining))

    def enroll(owner, dyn_id, **kwargs):
        writes.append((dyn_id, now[0]))
        return SimpleNamespace(
            stage=Stage.SETTLED,
            steps=[("share", SimpleNamespace(outcome=SimpleNamespace(value="ok")))],
        )

    def wait(seconds):
        now[0] += seconds
        if interrupt == "pause":
            repos.pool.set_paused(account_id, True, note="暂停", now=clock.now())
        elif interrupt == "remove":
            repos.pool.drop(account_id, ids[1])
        elif interrupt == "cancel":
            cancel[0] = True

    if mode == "queue":
        runner = RunPool(
            pool=repos.pool,
            enroll=lambda: SimpleNamespace(run=enroll),
            clock=clock,
            quota=PacedQuota(),
            wait=wait,
            progress=lambda owner, text: progress.append(text),
        )
        result = runner.run(account_id, apply=True, cancelled=lambda: cancel[0])
        assert result.failed == 0
        assert result.pending == (1 if interrupt in ("pause", "cancel") else 0)
        assert any("秒后继续" in p for p in progress) and progress[-1] == ""
    else:
        runner = BatchEnroll(
            campaigns=repos.campaigns,
            enroll=SimpleNamespace(run=enroll),
            clock=clock,
            quota=PacedQuota(),
            wait=wait,
        )
        result = runner.run(account_id, dry_run=False, cancelled=lambda: cancel[0])
        assert result.failed == 0
    assert len(writes) == (1 if interrupt else 2)
    if not interrupt:
        assert writes[1][1] - writes[0][1] == 15


def test_unlimited_counters_keep_short_gap():
    budget = Budget(per_hour=0, per_day=0)
    assert (
        assess(budget, Usage(last_hour=900, last_day=5000, since_last_seconds=15))[0] == Verdict.GO
    )
    assert assess(budget, Usage(last_hour=900, last_day=5000, since_last_seconds=2)) == (
        Verdict.HOLD,
        13,
    )


def test_manual_request_deduplicates_and_yields_background(repos, clock, account_id, monkeypatch):
    repos.feeds.add(account_id, "topic", "1", "A")
    repos.settings.put(account_id, "sources.refresh", "1")
    scanned = []
    box = SimpleNamespace(
        repos=repos,
        clock=clock,
        pool_progress={},
        has_session=lambda o: True,
        lanes=SimpleNamespace(exclusive=lambda o: nullcontext()),
        scan_feeds=lambda o: SimpleNamespace(
            run=lambda *a, **kw: scanned.append(kw) or SimpleNamespace(problems=[])
        ),
    )
    worker = SourceRefresh.__new__(SourceRefresh)
    worker.box, worker.stop = box, threading.Event()
    with participation_turn(box, account_id):
        assert requested(box, account_id)
        with pytest.raises(BusyAccount), participation_turn(box, account_id):
            pytest.fail("Duplicate request acquired the lane")
        assert requested(box, account_id)
        worker.tick()
        assert scanned == []
    assert not requested(box, account_id)
    worker.tick()
    assert len(scanned) == 1


def test_automatic_feeds_stagger_and_continuation_does_not_accelerate(
    repos, clock, account_id, monkeypatch
):
    first = repos.feeds.add(account_id, "topic", "1", "A")
    second = repos.feeds.add(account_id, "topic", "2", "B")
    repos.settings.put(account_id, "sources.refresh", "1")
    # 这里测的是"错峰 + 续扫不加速"，间隔取基准值即可，不叠加话题降频。
    repos.settings.put(account_id, "sources.topic.interval_hours", "0")
    repos.feeds.save_cursor(first, 'v2:{"latest":"next"}', now=clock.now())
    now, calls = [1800000000.0], []
    monkeypatch.setattr("tobili.runtime.source_refresh.time.time", lambda: now[0])
    box = SimpleNamespace(
        repos=repos,
        clock=clock,
        has_session=lambda o: True,
        lanes=SimpleNamespace(exclusive=lambda o: nullcontext()),
        scan_feeds=lambda o: SimpleNamespace(
            run=lambda *a, **kw: calls.append(kw) or SimpleNamespace(problems=[])
        ),
    )
    worker = SourceRefresh.__new__(SourceRefresh)
    worker.box, worker.stop = box, threading.Event()
    worker.tick()
    now[0] += 121
    worker.tick()
    assert [c["feed_id"] for c in calls] == [first]
    now[0] += 180
    worker.tick()
    assert [c["feed_id"] for c in calls] == [first, second]
    assert all(c["per_feed"] == 5 for c in calls)
    assert float(repos.settings.get(account_id, f"sources.due.{first}")) == 1800003600.0


def test_topic_interval_lengthens_topic_refresh(repos, clock, account_id, monkeypatch):
    """话题源按 sources.topic.interval_hours 排期，不必和索引源抢同一个轮转额度。"""
    feed = repos.feeds.add(account_id, "topic", "9", "话题")
    repos.settings.put(account_id, "sources.refresh", "1")
    repos.settings.put(account_id, "sources.topic.interval_hours", "3")
    now = [1800000000.0]
    monkeypatch.setattr("tobili.runtime.source_refresh.time.time", lambda: now[0])
    calls = []
    box = SimpleNamespace(
        repos=repos,
        clock=clock,
        has_session=lambda o: True,
        lanes=SimpleNamespace(exclusive=lambda o: nullcontext()),
        scan_feeds=lambda o: SimpleNamespace(
            run=lambda *a, **kw: calls.append(kw) or SimpleNamespace(problems=[])
        ),
    )
    worker = SourceRefresh.__new__(SourceRefresh)
    worker.box, worker.stop = box, threading.Event()
    worker.tick()
    assert len(calls) == 1
    assert float(repos.settings.get(account_id, f"sources.due.{feed}")) == now[0] + 3 * 3600
