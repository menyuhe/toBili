from types import SimpleNamespace

from test_taskbook_api import config
from test_taskbook_api import setup as setup

from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.enrollment import Stage
from tobili.domain.pool import PoolItem, PoolState
from tobili.usecases.batch_enroll import BatchEnroll
from tobili.usecases.enroll import EnrollReport


def arrange(box, *, waiting=0):
    ids = [
        box.repos.campaigns.upsert(
            Campaign(
                dyn_id=str(31000 + i),
                up_uid="2002",
                title="奖品",
                kind=CampaignKind.COMMENT,
                confidence=0.95,
            )
        )
        for i in range(7)
    ]
    reason = "评论历史核查中：尚未核实"
    box.repos.pool.save(
        1,
        [PoolItem(cid, i, PoolState.REVIEW, reason) for i, cid in enumerate(ids[:3])]
        + [PoolItem(cid, 3 + i) for i, cid in enumerate(ids[3 : 3 + waiting])],
        now=box.clock.now(),
    )
    for cid in ids[:3]:
        eid = box.repos.enrollments.create(1, cid, now=box.clock.now())
        box.repos.enrollments.move(eid, Stage.BLOCKED, now=box.clock.now(), reason=reason)
    sent = []
    checked = []

    def enroll(owner, dyn, **kw):
        cid = box.repos.campaigns.by_dyn(dyn)[0]
        assert cid not in ids[:3]
        sent.append(dyn)
        eid = box.repos.enrollments.create(owner, cid, now=box.clock.now())
        box.repos.enrollments.move(eid, Stage.SETTLED, now=box.clock.now())
        return EnrollReport(dyn, cid, eid, Stage.SETTLED)

    def recheck(owner, dyn):
        checked.append(dyn)
        return EnrollReport(
            dyn, box.repos.campaigns.by_dyn(dyn)[0], 1, Stage.BLOCKED, reason=reason
        )

    usecase = SimpleNamespace(run=enroll, recheck=recheck)
    box._enroll = lambda owner: usecase
    box.batch_enroll = lambda owner: BatchEnroll(
        campaigns=box.repos.campaigns, enroll=usecase, clock=box.clock
    )
    return ids, sent, checked


def test_three_readonly_reviews_do_not_starve_catalogue(setup):
    client, box = setup
    _ids, sent, checked = arrange(box)
    body = config(client, selection="catalog", limit=2, hourly_limit=50)
    report = box.tasks.run(1, "participate", body, apply=True)
    assert report["ok"] and report["written"] == 2
    assert len(checked) == 3 and len(sent) == 2 and len(box.tasks.attempts(1)) == 2
    assert "奖品库候选 2" in report["note"]
    box.tasks.run(1, "participate", body, apply=True)
    assert len(sent) == len(set(sent)) == 4
    assert all(x.state is PoolState.REVIEW for x in box.repos.pool.states(1))


def test_queue_and_catalogue_share_one_round_limit(setup):
    client, box = setup
    _ids, sent, _checked = arrange(box, waiting=1)
    report = box.tasks.run(
        1, "participate", config(client, selection="catalog", limit=2, hourly_limit=50), apply=True
    )
    assert report["ok"] and report["written"] == 2
    assert sent[0] == "31003" and len(sent) == 2
    assert len(box.tasks.attempts(1)) == 2


def test_queue_only_does_not_pick_unselected_catalogue_items(setup):
    client, box = setup
    _ids, sent, checked = arrange(box)
    report = box.tasks.run(1, "participate", config(client, selection="queue", limit=2), apply=True)
    assert report["ok"] and not sent and len(checked) == 3


def test_catalogue_fill_still_respects_hourly_budget(setup):
    client, box = setup
    _ids, sent, _checked = arrange(box, waiting=1)
    report = box.tasks.run(
        1, "participate", config(client, selection="catalog", limit=3, hourly_limit=1), apply=True
    )
    assert report["ok"] and len(sent) == 1 and len(box.tasks.attempts(1)) == 1
    assert "每小时参与上限" in report["note"]


def test_pause_after_queue_cannot_fall_through_to_catalogue(setup):
    client, box = setup
    arrange(box)

    def stopped(*a, **kw):
        box.repos.pool.set_paused(1, True, note="手动暂停", now=box.clock.now())
        return SimpleNamespace(
            failed=0, attempted=0, joined=0, note="手动暂停", stop_reason="手动暂停"
        )

    box.run_pool = lambda owner: SimpleNamespace(run=stopped)
    box.batch_enroll = lambda owner: (_ for _ in ()).throw(AssertionError("must not bypass pause"))
    result = box.tasks.run(1, "participate", config(client, selection="catalog"), apply=True)
    assert result["written"] == 0 and "暂停" in result["note"]
