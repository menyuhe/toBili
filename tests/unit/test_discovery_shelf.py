from datetime import timedelta

import pytest

from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.errors import WriteUnavailable
from tobili.domain.pool import PoolOp, PoolRejected
from tobili.ports.models import AccountProfile
from tobili.runtime.journal import Journal
from tobili.usecases.enroll import Enroll
from tobili.usecases.pool import ManagePool


def campaign(repos, clock, dyn, days=1):
    return repos.campaigns.upsert(
        Campaign(
            dyn_id=str(dyn),
            up_uid="2002",
            title="活动",
            kind=CampaignKind.SHARE,
            confidence=1,
            published_at=clock.now() + timedelta(minutes=dyn),
            deadline=clock.now() + timedelta(days=days),
        )
    )


@pytest.mark.parametrize("order", ["newest", "deadline"])
def test_unjoined_first_before_pagination_per_account(repos, clock, account_id, order):
    ids = [campaign(repos, clock, i, 10 - i) for i in range(1, 7)]
    for cid in ids[3:]:
        repos.enrollments.create(account_id, cid, now=clock.now())
        with repos.db.write() as db:
            db.execute("UPDATE enrollment SET stage='settled' WHERE campaign_id=?", (cid,))
    first = repos.campaigns.catalog(account_id, limit=3, order=order)["items"]
    second = repos.campaigns.catalog(account_id, limit=3, offset=3, order=order)["items"]
    assert [r["id"] for r in first] == ids[:3][::-1]
    assert [r["id"] for r in second] == ids[3:][::-1]
    other = repos.accounts.upsert(AccountProfile(uid="other", nickname="其他账号"), "")
    assert [r["id"] for r in repos.campaigns.catalog(other, limit=3, order=order)["items"]] == ids[
        3:
    ][::-1]


def test_dismiss_removes_queue_blocks_scheduled_and_direct_participation(
    repos, clock, account_id, gateway
):
    cid = campaign(repos, clock, 99)
    pool = ManagePool(
        pool=repos.pool, campaigns=repos.campaigns, enrollments=repos.enrollments, clock=clock
    )
    pool.edit(account_id, PoolOp.ADD, campaign_ids=[cid])
    repos.campaigns.dismiss(account_id, cid)
    assert not pool.view(account_id).items
    assert repos.campaigns.catalog(account_id)["total"] == 0
    assert not repos.campaigns.candidates(account_id, now=clock.now(), floor=0, limit=10)
    assert (
        repos.campaigns.catalog(account_id, state="shelved")["items"][0]["shelf_reason"] == "manual"
    )
    with pytest.raises(PoolRejected):
        pool.edit(account_id, PoolOp.ADD, campaign_ids=[cid])
    enroll = Enroll(
        gateway=gateway,
        campaigns=repos.campaigns,
        enrollments=repos.enrollments,
        journal=Journal(gateway=gateway, intents=repos.intents, clock=clock),
        clock=clock,
    )
    with pytest.raises(WriteUnavailable, match="往期架"):
        enroll.run(account_id, "99")
    assert not gateway.writes
    repos.campaigns.unshelve(account_id, cid)
    assert repos.campaigns.catalog(account_id)["total"] == 1
    assert len(pool.edit(account_id, PoolOp.ADD, campaign_ids=[cid]).items) == 1


def test_shelf_provenance_and_account_isolation(repos, clock, account_id):
    manual = campaign(repos, clock, 1, -1)
    expired = campaign(repos, clock, 2, -1)
    repos.campaigns.dismiss(account_id, manual)
    repos.campaigns.shelve_closed(account_id, clock.now())
    reasons = {
        r["id"]: r["shelf_reason"]
        for r in repos.campaigns.catalog(account_id, state="shelved")["items"]
    }
    assert reasons == {manual: "manual", expired: "expired"}
    other = repos.accounts.upsert(AccountProfile(uid="other", nickname="其他账号"), "")
    assert repos.campaigns.catalog(other)["total"] == 2
    assert repos.campaigns.catalog(other, state="shelved")["total"] == 0


def test_dismiss_never_hides_participation_history(repos, clock, account_id):
    cid = campaign(repos, clock, 1)
    repos.enrollments.create(account_id, cid, now=clock.now())
    with pytest.raises(ValueError, match="参与记录"):
        repos.campaigns.dismiss(account_id, cid)
    assert not repos.campaigns.shelved(account_id, cid)


def test_prize_attention_uses_blacklist_and_shelf_and_preserves_history(repos, clock, account_id):
    from types import SimpleNamespace

    from tobili.usecases.catalog_controls import put_block
    from tobili.usecases.prize_review import review_status
    from tobili.usecases.result_ledger import ledger

    ids = [campaign(repos, clock, i) for i in range(1, 4)]
    with repos.db.write() as db:
        db.execute("UPDATE campaign SET reward_status='unknown'")
        db.execute("UPDATE campaign SET up_uid='blocked' WHERE id=?", (ids[0],))
    box = SimpleNamespace(db=repos.db, repos=repos, clock=clock, account=lambda: (account_id, ""))
    put_block(box, "blocked", "UP", "", True)
    repos.campaigns.dismiss(account_id, ids[1])
    assert [
        r["id"]
        for r in repos.campaigns.catalog(account_id, state="prizes", prize_state="unknown")["items"]
    ] == [ids[2]]
    assert review_status(box)["attention"] == 1
    repos.enrollments.create(account_id, ids[2], now=clock.now())
    with repos.db.write() as db:
        db.execute("UPDATE enrollment SET stage='settled' WHERE campaign_id=?", (ids[2],))
    repos.campaigns.dismiss(account_id, ids[2], prize_only=True)
    assert review_status(box)["attention"] == 0
    assert ledger(box, account_id)["total"] == 1
    repos.campaigns.unshelve(account_id, ids[2])
    assert review_status(box)["attention"] == 1
    with repos.db.write() as db:
        db.execute("UPDATE campaign SET reward_status='manual' WHERE id=?", (ids[2],))
    with pytest.raises(ValueError, match="已补全"):
        repos.campaigns.dismiss(account_id, ids[2], prize_only=True)
