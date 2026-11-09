from datetime import timedelta
from types import SimpleNamespace

from test_queue_review import seed
from test_taskbook_api import config
from test_taskbook_api import setup as setup

from tobili.domain.enrollment import Stage
from tobili.domain.pool import PoolItem, PoolState
from tobili.domain.probe import ProbeState
from tobili.runtime.journal import Outcome, Step
from tobili.usecases.enroll import EnrollReport


def test_read_coverage_rechecks_rotate_without_spending_write_quota(setup):
    client, box = setup
    ids = seed(box, 7)
    for i, cid in enumerate(ids[:6]):
        box.repos.pool.update_one(
            1,
            PoolItem(cid, i, PoolState.REVIEW, "转发历史核查中"),
            now=box.clock.now() - timedelta(days=1),
        )
    checked = []

    def recheck(owner, dyn):
        checked.append(dyn)
        return EnrollReport(dyn, 0, 0, Stage.PLANNED, reason="转发历史核查中")

    box._enroll = lambda _: SimpleNamespace(
        recheck=recheck, run=lambda owner, dyn, **kw: EnrollReport(dyn, ids[-1], 1, Stage.SETTLED)
    )
    cfg = config(client, selection="queue", limit=1)
    result = box.tasks.run(1, "participate", cfg, apply=True)
    assert result["written"] == 1 and len(checked) == 3 and len(box.tasks.attempts(1)) == 1
    box.tasks.run(1, "participate", cfg, apply=True)
    assert len(checked) == 6 and len(set(checked)) == 6 and len(box.tasks.attempts(1)) == 1


def test_only_completed_read_evidence_resumes_original_item(setup):
    client, box = setup
    ids = seed(box, 3)
    for i, reason in enumerate(["转发历史核查中", "已有发送记录但结果未确认", "预约：活动已结束"]):
        box.repos.pool.update_one(
            1, PoolItem(ids[i], i, PoolState.REVIEW, reason), now=box.clock.now()
        )
    called = []

    def recheck(owner, dyn):
        return EnrollReport(
            dyn,
            ids[0],
            1,
            Stage.PLANNED,
            steps=(("转发", Step(Outcome.SETTLED, ProbeState.ABSENT, "确认未转发")),),
        )

    box._enroll = lambda _: SimpleNamespace(
        recheck=recheck,
        run=lambda owner, dyn, **kw: (
            called.append(dyn) or EnrollReport(dyn, ids[0], 1, Stage.SETTLED)
        ),
    )
    result = box.tasks.run(
        1, "participate", config(client, selection="catalog", limit=15), apply=True
    )
    assert result["written"] == 1 and called == ["99000"]
    assert {r.campaign_id for r in box.repos.pool.states(1)} == set(ids[1:])
