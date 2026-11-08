from types import SimpleNamespace

import pytest

from tobili.domain.action import Action, ActionKind
from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.errors import WriteUnavailable
from tobili.domain.pool import PoolOp
from tobili.runtime.journal import Journal
from tobili.usecases.catalog_controls import edit_prize, guard_action, put_block
from tobili.usecases.enroll import Enroll
from tobili.usecases.pool import ManagePool


def test_block_removes_candidates_and_stops_existing_queue(repos, clock, account_id, gateway):
    cid = repos.campaigns.upsert(
        Campaign(dyn_id="99", up_uid="2002", title="文章", kind=CampaignKind.SHARE, confidence=1)
    )
    box = SimpleNamespace(db=repos.db, repos=repos, clock=clock)
    pool = ManagePool(
        pool=repos.pool, campaigns=repos.campaigns, enrollments=repos.enrollments, clock=clock
    )
    pool.edit(account_id, PoolOp.ADD, campaign_ids=[cid])
    put_block(box, "2002", "作者", "不是抽奖", True)
    assert pool.view(account_id).items[0]["state"] == "skipped"
    assert repos.campaigns.catalog(account_id)["total"] == 0
    assert not repos.campaigns.candidates(account_id, now=clock.now(), floor=0, limit=10)
    journal = Journal(gateway=gateway, intents=repos.intents, clock=clock)
    enroll = Enroll(
        gateway=gateway,
        campaigns=repos.campaigns,
        enrollments=repos.enrollments,
        journal=journal,
        clock=clock,
    )
    for dry in (True, False):
        with pytest.raises(WriteUnavailable, match="黑名单"):
            enroll.run(account_id, "99", dry_run=dry)
    assert not gateway.writes
    assert repos.campaigns.catalog(account_id, state="all_records")["total"] == 1
    put_block(box, "2002", "作者", "", False)
    assert repos.campaigns.catalog(account_id)["total"] == 1


def test_block_added_during_probe_stops_before_reserving_or_writing(
    repos, clock, account_id, gateway
):
    cid = repos.campaigns.upsert(Campaign(dyn_id="99", up_uid="2002", title="文章"))
    box = SimpleNamespace(db=repos.db, repos=repos, clock=clock)
    original = gateway.did

    def probe(*args, **kwargs):
        put_block(box, "2002", "作者", "", True)
        return original(*args, **kwargs)

    gateway.did = probe
    journal = Journal(
        gateway=gateway,
        intents=repos.intents,
        clock=clock,
        action_guard=lambda c, a: guard_action(box, c, a),
    )
    with pytest.raises(WriteUnavailable):
        journal.attempt(
            account_id=account_id, campaign_id=cid, action=Action(ActionKind.SHARE, "99")
        )
    assert not gateway.writes
    assert repos.db.connect().execute("SELECT count(*) FROM intent").fetchone()[0] == 0


def test_manual_prize_survives_new_source_and_rejects_stale_editor(repos, clock):
    cid = repos.campaigns.upsert(
        Campaign(dyn_id="99", up_uid="2", title="活动", reward="未知", body_text="抽奖")
    )
    box = SimpleNamespace(db=repos.db, repos=repos, clock=clock)
    edit_prize(box, cid, "显示器一台", "未知", "图片第 2 张")
    repos.campaigns.upsert(
        Campaign(dyn_id="99", up_uid="2", title="更新", reward="送给你", body_text="修改后的抽奖")
    )
    row = repos.campaigns.get(cid)
    assert row["reward"] == "显示器一台" and row["reward_status"] == "manual"
    with pytest.raises(ValueError, match="已被更新"):
        edit_prize(box, cid, "鼠标", "未知", "")
    assert repos.db.connect().execute("SELECT count(*) FROM prize_edit").fetchone()[0] == 1
