from datetime import timedelta

from test_taskbook_api import setup as setup

from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.enrollment import Stage
from tobili.domain.pool import PoolItem
from tobili.runtime.catalog_worker import run, view


def test_expiry_without_curated_preserves_history_and_never_calls_remote(setup):
    client, box = setup
    box.resource_policy = lambda owner: False
    now = box.clock.now()
    ids = [
        box.repos.campaigns.upsert(
            Campaign(
                dyn_id=str(99000 + i),
                up_uid="2",
                title="奖品",
                kind=CampaignKind.SHARE,
                reward="鼠标",
                confidence=1,
                deadline=now + timedelta(seconds=offset),
            )
        )
        for i, offset in enumerate((-1, 0, 3600))
    ]
    eid = box.repos.enrollments.create(1, ids[0], now=now)
    box.repos.enrollments.move(eid, Stage.BLOCKED, now=now, reason="发送结果未知")
    box.repos.pool.save(1, [PoolItem(cid, i) for i, cid in enumerate(ids)], now=now)
    before = [tuple(r) for r in box.db.connect().execute("SELECT * FROM enrollment")]
    plans = box.repos.settings.get(1, "tasks.config", "")
    box.plan_tidy = lambda: (_ for _ in ()).throw(AssertionError("remote tidy forbidden"))
    r = client.post("/api/catalog-cleanup").json()
    assert r["ok"] and r["expired"] == 2 and r["pool_removed"] == 2
    assert r["with_history"] == r["without_history"] == 1
    assert [tuple(r) for r in box.db.connect().execute("SELECT * FROM enrollment")] == before
    assert box.repos.settings.get(1, "tasks.config", "") == plans
    assert [p.campaign_id for p in box.repos.pool.states(1)] == [ids[2]]
    assert run(box) is None  # persistent interval survives a new worker instance
    assert run(box, force=True)["removed"] == 0
    assert box.repos.campaigns.removed("99000")


def test_interval_validation_and_failure_retry(setup, monkeypatch):
    client, box = setup
    assert client.put("/api/catalog-cleanup", json={"minutes": 0}).status_code == 422
    assert client.put("/api/catalog-cleanup", json={"minutes": 15}).json()["minutes"] == 15
    assert run(box) is None

    def broken(*args):
        raise RuntimeError("test failure")

    monkeypatch.setattr("tobili.runtime.catalog_worker.maintain", broken)
    r = run(box, force=True)
    assert not r["ok"] and "test failure" in r["note"]
    state = view(box)
    assert state["next_at"] - box.clock.now().timestamp() <= 60
    assert state["latest"] == state["history"][0] == r


def test_winners_unknown_results_and_send_journal_survive(setup):
    from tobili.usecases.result_ledger import ledger

    client, box = setup
    now = box.clock.now()
    for i, outcome in enumerate(("won", "unknown")):
        cid = box.repos.campaigns.upsert(
            Campaign(
                dyn_id=str(88100 + i),
                up_uid="2",
                title="奖",
                kind=CampaignKind.SHARE,
                deadline=now - timedelta(days=1),
            )
        )
        eid = box.repos.enrollments.create(1, cid, now=now)
        box.repos.enrollments.move(eid, Stage.SETTLED, now=now)
        with box.db.write() as c:
            c.execute(
                "INSERT INTO intent(account_id,campaign_id,action,fingerprint,state,created_at) VALUES(1,?,'share',?,'unknown',?)",
                (cid, str(i), now.isoformat()),
            )
            if outcome == "won":
                c.execute(
                    "INSERT INTO verdict(campaign_id,account_id,outcome,decided_at) VALUES(?,1,'won',?)",
                    (cid, now.isoformat()),
                )
    before = {
        table: [tuple(r) for r in box.db.connect().execute("SELECT * FROM " + table)]
        for table in ("intent", "enrollment", "verdict")
    }
    assert client.post("/api/catalog-cleanup").json()["expired"] == 2
    assert ledger(box, 1)["outcomes"]["won"] == 1
    assert ledger(box, 1)["outcomes"]["unknown"] == 1
    assert before == {
        table: [tuple(r) for r in box.db.connect().execute("SELECT * FROM " + table)]
        for table in before
    }
