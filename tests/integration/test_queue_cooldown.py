import time
from types import SimpleNamespace

from test_queue_review import seed
from test_taskbook_api import config
from test_taskbook_api import setup as setup

from tobili.domain.action import Action, ActionKind
from tobili.domain.enrollment import Stage
from tobili.domain.errors import Fault, RemoteError
from tobili.domain.pool import PoolItem, PoolState
from tobili.domain.receipt import Receipt
from tobili.runtime.journal import Journal
from tobili.usecases.enroll import EnrollReport


def test_platform_throttle_keeps_plan_and_item_and_defers_other_tasks(setup):
    client, box = setup
    seed(box, 2)
    cfg = config(
        client, running=True, apply=True, confirmed=True, selection="queue", hourly_limit=50
    )
    assert client.put("/api/tasks", json=cfg).is_success
    cfg = box.tasks.config(1)

    def run(*a, **k):
        raise RemoteError(Fault.RISK, "HTTP 412", code=412)

    box._enroll = lambda _: SimpleNamespace(run=run)
    result = box.tasks.run(1, "participate", cfg, apply=True, scheduled=True)
    assert result["cooldown"] and result["next_seconds"] == 900
    assert box.tasks.config(1) == cfg
    assert all(r.state is PoolState.PENDING for r in box.repos.pool.states(1))
    assert not box.repos.pool.paused(1)[0]
    assert client.get("/api/tasks").json()["next_scheduled"]["at"] >= time.time() + 899
    called = []
    box.inbox = lambda _: called.append(1)
    assert "冷却" in box.tasks.run(1, "messages", cfg)["note"] and not called


def test_legacy_rechecks_bounded_but_fresh_tail_still_runs(setup):
    client, box = setup
    ids = seed(box, 11)
    for cid in ids[:10]:
        eid = box.repos.enrollments.create(1, cid, now=box.clock.now())
        box.repos.enrollments.move(eid, Stage.BLOCKED, now=box.clock.now(), reason="旧记录不完整")
        box.repos.pool.update_one(
            1, PoolItem(cid, ids.index(cid), message="旧错误"), now=box.clock.now()
        )
    checked = []

    def recheck(owner, dyn):
        checked.append(dyn)
        return EnrollReport(dyn, 0, 1, Stage.BLOCKED, reason="评论记录不足")

    box._enroll = lambda _: SimpleNamespace(
        recheck=recheck, run=lambda owner, dyn, **kw: EnrollReport(dyn, ids[-1], 1, Stage.SETTLED)
    )
    result = box.tasks.run(1, "participate", config(client, selection="queue", limit=1), apply=True)
    assert len(checked) == 3 and result["written"] == 1
    rows = box.repos.pool.states(1)
    assert sum(r.state is PoolState.REVIEW for r in rows) == 3
    assert sum(r.state is PoolState.PENDING for r in rows) == 7


def test_uncertain_reservation_quarantined_and_next_item_runs(setup, gateway):
    client, box = setup
    ids = seed(box, 2)
    gateway.receipt = Receipt.unclear("预约响应无效", fault=Fault.PARSE)
    journal = Journal(gateway=gateway, intents=box.repos.intents, clock=box.clock)

    def run(owner, dyn, **kw):
        cid = box.repos.campaigns.by_dyn(dyn)[0]
        if cid == ids[1]:
            return EnrollReport(dyn, cid, 1, Stage.SETTLED)
        step = journal.attempt(
            account_id=owner,
            campaign_id=cid,
            action=Action(ActionKind.RESERVE, dyn, {"reserve_id": "456"}),
        )
        return EnrollReport(dyn, cid, 1, Stage.BLOCKED, steps=(("预约", step),))

    box._enroll = lambda _: SimpleNamespace(run=run)
    result = box.tasks.run(1, "participate", config(client, selection="queue", limit=2), apply=True)
    assert result["written"] == 1 and not box.repos.pool.paused(1)[0]
    assert box.repos.pool.states(1)[0].state is PoolState.REVIEW
    assert len(gateway.writes) == 1
    step = journal.attempt(
        account_id=1,
        campaign_id=ids[0],
        action=Action(ActionKind.RESERVE, "99000", {"reserve_id": "456"}),
    )
    assert step.outcome.value == "unknown" and len(gateway.writes) == 1
