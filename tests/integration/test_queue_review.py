from types import SimpleNamespace

from test_taskbook_api import config
from test_taskbook_api import setup as setup

from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.enrollment import Stage
from tobili.domain.pool import PoolItem, PoolState
from tobili.usecases.enroll import EnrollReport


def seed(box, count):
    ids = [
        box.repos.campaigns.upsert(
            Campaign(dyn_id=str(99000 + i), up_uid="up", title="奖品", kind=CampaignKind.COMMENT)
        )
        for i in range(count)
    ]
    box.repos.pool.save(1, [PoolItem(cid, i) for i, cid in enumerate(ids)], now=box.clock.now())
    return ids


def test_unconfirmed_items_leave_active_queue_and_later_round_reaches_tail(setup):
    client, box = setup
    ids = seed(box, 20)
    seen = []

    def enroll(owner, dyn, **kw):
        cid = box.repos.campaigns.by_dyn(dyn)[0]
        seen.append(cid)
        return EnrollReport(
            dyn,
            cid,
            1,
            Stage.BLOCKED if cid in ids[:9] else Stage.SETTLED,
            reason="评论记录不完整" if cid in ids[:9] else "",
        )

    box._enroll = lambda _: SimpleNamespace(run=enroll)
    body = config(client, selection="catalog", limit=15, hourly_limit=50)
    first = box.tasks.run(1, "participate", body, apply=True)
    assert first["written"] == 6 and "待处理 9" in first["note"]
    second = box.tasks.run(1, "participate", body, apply=True)
    assert second["written"] == 5 and len(seen) == len(set(seen)) == 20
    rows = box.repos.pool.states(1)
    assert len(rows) == 9 and all(
        r.state is PoolState.REVIEW and r.message == "评论记录不完整" for r in rows
    )
    assert client.get("/api/pool/count").json()["pending"] == 0
    assert not box.repos.pool.paused(1)[0]


def test_existing_blocked_rows_are_quarantined_without_spending_round_or_hour_quota(setup):
    client, box = setup
    ids = seed(box, 10)
    for cid in ids[:9]:
        eid = box.repos.enrollments.create(1, cid, now=box.clock.now())
        box.repos.enrollments.move(
            eid, Stage.BLOCKED, now=box.clock.now(), reason="转发查询范围不足"
        )
        box.repos.pool.update_one(
            1, PoolItem(cid, ids.index(cid), message="需要核对"), now=box.clock.now()
        )
    seen = []
    box._enroll = lambda _: SimpleNamespace(
        run=lambda owner, dyn, **kw: (
            seen.append(dyn) or EnrollReport(dyn, ids[-1], 1, Stage.SETTLED)
        )
    )
    result = box.tasks.run(
        1, "participate", config(client, selection="catalog", limit=1), apply=True
    )
    assert result["written"] == 1 and len(seen) == 1
    assert len(box.tasks.attempts(1)) == 1
    assert all(
        r.state is PoolState.REVIEW and r.message == "转发查询范围不足"
        for r in box.repos.pool.states(1)
    )
    # A deliberate retry clears only that row; the existing diagnosis doesn't re-quarantine it.
    response = client.post("/api/pool", json={"operation": "retry", "campaign_id": ids[0]})
    assert response.is_success and response.json()["counts"]["pending"] == 1


def test_review_row_cannot_reenter_through_catalog_without_enrollment(setup):
    _client, box = setup
    cid = box.repos.campaigns.upsert(
        Campaign(
            dyn_id="catalog-review",
            up_uid="up",
            title="奖品",
            kind=CampaignKind.COMMENT,
            confidence=0.95,
        )
    )
    box.repos.pool.save(1, [PoolItem(cid, 0, PoolState.REVIEW, "需要核对")], now=box.clock.now())
    assert not box.repos.campaigns.candidates(1, now=box.clock.now(), floor=0.8, limit=15)


def test_unreadable_legacy_item_does_not_block_later_valid_item(setup):
    client, box = setup
    ids = seed(box, 2)
    eid = box.repos.enrollments.create(1, ids[0], now=box.clock.now())
    box.repos.enrollments.move(eid, Stage.BLOCKED, now=box.clock.now(), reason="旧错误")
    box.repos.pool.update_one(1, PoolItem(ids[0], 0, message="旧错误"), now=box.clock.now())

    def recheck(*a):
        raise RuntimeError("原动态无法读取")

    box._enroll = lambda _: SimpleNamespace(
        recheck=recheck, run=lambda owner, dyn, **kw: EnrollReport(dyn, ids[1], 1, Stage.SETTLED)
    )
    result = box.tasks.run(
        1, "participate", config(client, selection="catalog", limit=1), apply=True
    )
    assert result["written"] == 1
    row = box.repos.pool.states(1)[0]
    assert row.state is PoolState.REVIEW and "原动态无法读取" in row.message
