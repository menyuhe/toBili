import threading
from itertools import pairwise
from types import SimpleNamespace

import pytest

from tobili.domain.action import ActionKind
from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.pool import PoolOp, PoolRejected
from tobili.domain.pool import PoolItem,PoolState
from tobili.domain.enrollment import Stage
from tobili.domain.probe import ProbeState
from tobili.domain.throttle import Budget
from tobili.runtime.journal import Journal
from tobili.runtime.lane import Lanes
from tobili.runtime.quota import Quota
from tobili.runtime.source_refresh import SourceRefresh
from tobili.runtime.work_priority import participation_turn
from tobili.usecases.enroll import Enroll
from tobili.usecases.pool import ManagePool, RunPool


@pytest.mark.parametrize("gap", [0, 0.8, 5, 15])
def test_33_real_usecase_activities_no_duplicate_or_background_takeover(
    repos, clock, gateway, account_id, gap
):
    ids = [
        repos.campaigns.upsert(
            Campaign(
                dyn_id=f"batch-{i}",
                up_uid="2002",
                title="鼠标",
                kind=CampaignKind.SHARE,
                confidence=0.95,
                seen_at=clock.now(),
            )
        )
        for i in range(33)
    ]
    ManagePool(
        pool=repos.pool, campaigns=repos.campaigns, enrollments=repos.enrollments, clock=clock
    ).edit(account_id, PoolOp.ADD, campaign_ids=ids)
    calls = []
    followed = set()
    gateway.did = lambda action, **kw: ProbeState.PRESENT if action is ActionKind.FOLLOW and kw['payload'].get('up_uid') in followed else ProbeState.ABSENT
    original = gateway.perform

    def perform(action):
        calls.append((action.kind, action.dyn_id, clock.now()))
        if action.kind is ActionKind.FOLLOW:
            followed.add(action.payload['up_uid'])
        return original(action)

    gateway.perform = perform
    quota = Quota(
        intents=repos.intents,
        clock=clock,
        budget=Budget(per_hour=0, per_day=0, min_gap_seconds=gap),
    )
    enroll = Enroll(
        gateway=gateway,
        campaigns=repos.campaigns,
        enrollments=repos.enrollments,
        journal=Journal(gateway=gateway, intents=repos.intents, clock=clock),
        clock=clock,
        quota=quota,
        standard_actions=True,
    )
    scans = []
    repos.feeds.add(account_id, "topic", "1", "background")
    repos.settings.put(account_id, "sources.refresh", "1")
    box = SimpleNamespace(
        repos=repos,
        clock=clock,
        pool_progress={},
        lanes=Lanes(),
        has_session=lambda o: True,
        scan_feeds=lambda o: SimpleNamespace(
            run=lambda *a, **kw: scans.append(kw) or SimpleNamespace(problems=[])
        ),
    )
    bg = SourceRefresh.__new__(SourceRefresh)
    bg.box = box
    bg.stop = threading.Event()

    def wait(seconds):
        clock.advance(seconds=seconds)
        bg.tick()
        assert scans == []

    runner = RunPool(pool=repos.pool, enroll=lambda: enroll, clock=clock, quota=quota, wait=wait)
    with participation_turn(box, account_id):
        result = runner.run(account_id, apply=True)
    assert (result.joined, result.failed, result.pending) == (33, 0, 0)
    assert len({(kind, dyn) for kind, dyn, at in calls}) == len(calls)
    shares = [at for kind, dyn, at in calls if kind == ActionKind.SHARE]
    assert len(shares) == 33
    assert all((b - a).total_seconds() >= gap - 1e-6 for a, b in pairwise(shares))
    count = len(calls)
    with pytest.raises(PoolRejected):
        runner.run(account_id, apply=True)
    # Even after a restart, intent reservations prevent a stale view from reposting.
    for i in range(33):
        clock.advance(seconds=gap)
        enroll.run(account_id, f"batch-{i}")
    assert len(calls) == count
    bg.tick()
    assert len(scans) == 1


def test_interrupted_batch_resumes_without_replaying_or_claiming_unknown_success(
    repos, clock, gateway, account_id
):
    ids = [
        repos.campaigns.upsert(
            Campaign(
                dyn_id=f"interrupt-{i}",
                up_uid="2002",
                title="鼠标",
                kind=CampaignKind.SHARE,
                confidence=0.95,
                seen_at=clock.now(),
            )
        )
        for i in range(33)
    ]
    manager = ManagePool(
        pool=repos.pool, campaigns=repos.campaigns, enrollments=repos.enrollments, clock=clock
    )
    manager.edit(account_id, PoolOp.ADD, campaign_ids=ids)
    calls = []
    original = gateway.perform
    followed = set()
    gateway.did = lambda action, **kw: ProbeState.PRESENT if action is ActionKind.FOLLOW and kw['payload'].get('up_uid') in followed else ProbeState.ABSENT

    def perform(action):
        calls.append((action.kind, action.dyn_id))
        if action.kind == ActionKind.SHARE and action.dyn_id == "interrupt-15":
            raise OSError("simulated disconnection after request")
        if action.kind is ActionKind.FOLLOW:
            followed.add(action.payload['up_uid'])
        return original(action)

    gateway.perform = perform

    def runner():
        enrollment = Enroll(
            gateway=gateway,
            campaigns=repos.campaigns,
            enrollments=repos.enrollments,
            journal=Journal(gateway=gateway, intents=repos.intents, clock=clock),
            clock=clock,
            standard_actions=True,
        )
        return RunPool(pool=repos.pool, enroll=lambda: enrollment, clock=clock)

    first = runner().run(account_id, apply=True)
    assert first.joined == 15 and first.failed == 1 and first.paused
    manager.edit(account_id, PoolOp.RETRY)
    manager.edit(account_id, PoolOp.RESUME)
    second = runner().run(account_id, apply=True)
    assert second.joined == 17 and second.failed == 0 and second.pending == 0
    assert second.review == 1
    with pytest.raises(PoolRejected):
        runner().run(account_id, apply=True)
    assert len(calls) == len(set(calls))
    assert "未确认" in repos.pool.items(account_id)[0]["message"]


def test_legacy_block_rechecks_once_and_continues_without_recomposing_failed_rows(
    repos, clock, gateway, account_id
):
    ids=[]
    for dyn in ('fixed','uncertain','tail'):
        cid=repos.campaigns.upsert(Campaign(dyn_id=dyn,up_uid='2002',title=dyn,kind=CampaignKind.SHARE))
        ids.append(cid)
        if dyn!='tail':
            eid=repos.enrollments.create(account_id,cid,now=clock.now())
            repos.enrollments.move(eid,Stage.BLOCKED,now=clock.now(),reason='旧探测未确认')
    repos.pool.save(account_id,[PoolItem(cid,i,message='旧探测未确认' if i<2 else '') for i,cid in enumerate(ids)],now=clock.now())
    gateway.did=lambda action,*,dyn_id,payload:ProbeState.UNKNOWN if dyn_id=='uncertain' else ProbeState.ABSENT
    drafts=[]
    enroll=Enroll(gateway=gateway,campaigns=repos.campaigns,enrollments=repos.enrollments,
        journal=Journal(gateway=gateway,intents=repos.intents,clock=clock),clock=clock,
        composer=SimpleNamespace(draft=lambda **kw:drafts.append(kw['dyn_id']) or '测试文案'))
    runner=RunPool(pool=repos.pool,enroll=lambda:enroll,clock=clock)
    report=runner.run(account_id,apply=True)
    assert report.joined==2 and report.review==1 and report.pending==0
    assert drafts==['fixed','tail']
    assert all(w.dyn_id!='uncertain' for w in gateway.writes)
    assert repos.pool.states(account_id)[0].state is PoolState.REVIEW
