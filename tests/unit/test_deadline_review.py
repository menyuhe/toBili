import json
from datetime import UTC, datetime

from tobili.domain.campaign import Campaign
from tobili.usecases.deadline_review import infer, review_existing


def test_draw_winners_date_is_not_retail_release_or_fake_midnight():
    at = datetime(2026, 11, 6, 2, tzinfo=UTC)
    result = infer("11月6日新品开售。11月23日小蛇将在评论区抽5位玩家获得游戏礼包码及挂件。", at)
    assert result[0].date().isoformat() == "2026-11-23"
    assert result[1] == "day"
    assert infer("将在11月9日揪1位家人，送出电源", at)[1] == "day"
    assert infer("11月23日晚上八点抽5位玩家获得游戏礼包码", at)[1] == "minute"


def test_backfill_preserves_existing_and_manual_null_and_does_not_invent_clock(
    repos, clock, account_id
):
    published = datetime(2026, 11, 8, 2, tzinfo=UTC)
    ids = []
    for i in range(6):
        ids.append(
            repos.campaigns.upsert(
                Campaign(
                    dyn_id=str(100 + i),
                    up_uid="1",
                    title="奖品",
                    body_text="转发抽奖，下周五晚上八点开奖",
                    published_at=published,
                )
            )
        )
    with repos.db.write() as db:
        db.execute("CREATE TABLE pro_edits(dyn TEXT PRIMARY KEY,accepted TEXT)")
        db.execute("INSERT INTO pro_edits VALUES(?,?)", ("101", json.dumps({"deadline_at": None})))
        db.execute(
            "UPDATE campaign SET deadline_at='2026-12-31T12:00:00+00:00' WHERE id=?", (ids[2],)
        )
        db.execute("UPDATE campaign SET body_text='下周五开奖' WHERE id=?", (ids[3],))
        db.execute("UPDATE campaign SET published_at=NULL WHERE id=?", (ids[4],))
        db.execute("UPDATE campaign SET body_text='近期择日开奖' WHERE id=?", (ids[5],))
    report = review_existing(repos.db, clock.now())
    assert report["counts"] == {
        "filled": 1,
        "manual": 1,
        "conflict": 1,
        "date_only": 1,
        "unresolved": 2,
    }
    assert repos.campaigns.get(ids[0])["deadline_at"] == "2026-11-13T12:00:00+00:00"
    assert repos.campaigns.get(ids[1])["deadline_at"] is None
    assert repos.campaigns.get(ids[2])["deadline_at"] == "2026-12-31T12:00:00+00:00"
    assert repos.campaigns.get(ids[3])["deadline_at"] is None
    rows = {r["id"]: r for r in repos.campaigns.catalog(account_id)["items"]}
    assert rows[ids[3]]["deadline_date"] == "2026-11-13T15:59:00+00:00"
    assert review_existing(repos.db, clock.now())["counts"].get("filled", 0) == 0


def test_uncertain_and_other_dates_are_not_promoted():
    at = datetime(2026, 11, 8, tzinfo=UTC)
    assert infer("抽奖黑幕：原本4月25日开奖改成4月30日开奖", at) is None
    assert infer("预计下周五左右开奖", at) is None
    assert infer("下周五开奖。下周日开奖。", at) is None
    assert infer("明天开售！", at) is None
    assert infer("11月20日晚上8点开奖", None) is None
    assert infer("2026年11月20日晚上8点开奖", None)[1] == "minute"


def test_model_excerpt_is_only_used_when_verbatim(repos, clock):
    body = "11月1日开售，下周五晚上八点开奖"
    repos.campaigns.upsert(
        Campaign(
            dyn_id="300",
            up_uid="1",
            title="奖品",
            body_text=body,
            published_at=datetime(2026, 11, 8, tzinfo=UTC),
        )
    )
    assert review_existing(repos.db, clock.now(), {"300": "明天晚上八点开奖"})["counts"] == {
        "unresolved": 1
    }
    assert review_existing(repos.db, clock.now(), {"300": "下周五晚上八点开奖"})["counts"] == {
        "filled": 1
    }


def test_clock_linkers_bounds_and_secondary_live_draw():
    at = datetime(2026, 9, 1, tzinfo=UTC)
    for text in ("会在9/30的20:00抽出1位", "9月30日的20点开奖"):
        value, precision, _ = infer(text, at)
        assert value.astimezone(UTC) == datetime(2026, 9, 30, 12, tzinfo=UTC)
        assert precision == "minute"
    assert infer("9月30日17:00前抽取3位", at)[1] == "day"
    assert infer("另今晚8:00直播抽取礼物", at) is None


def test_day_only_expiry_blocks_next_day_not_the_whole_draw_day(repos, clock, account_id):
    from tobili.domain.campaign import CampaignKind

    cid = repos.campaigns.upsert(
        Campaign(
            dyn_id="400",
            up_uid="1",
            title="奖品",
            kind=CampaignKind.SHARE,
            confidence=0.95,
            body_text="2026年11月13日开奖",
            published_at=datetime(2026, 11, 8, tzinfo=UTC),
        )
    )
    review_existing(repos.db, clock.now())
    row = repos.campaigns.get(cid)
    assert row["deadline_at"] is None
    assert row["expires_at"] == "2026-11-13T16:00:00+00:00"
    listing = repos.campaigns.catalog(
        account_id,
        time_field="deadline",
        after="2026-11-12T16:00:00+00:00",
        before="2026-11-13T15:59:59+00:00",
    )
    assert listing["total"] == 1
    listing = repos.campaigns.catalog(
        account_id, time_field="deadline", after="2026-11-13T16:00:00+00:00"
    )
    assert listing["total"] == 0
    before = datetime(2026, 11, 13, 15, 59, 59, tzinfo=UTC)
    after = datetime(2026, 11, 13, 16, tzinfo=UTC)
    assert len(repos.campaigns.candidates(account_id, now=before, floor=0.8, limit=10)) == 1
    assert not repos.campaigns.candidates(account_id, now=after, floor=0.8, limit=10)
    assert repos.campaigns.shelve_closed(account_id, before) == 0
    assert repos.campaigns.shelve_closed(account_id, after) == 1
    with repos.db.write() as db:
        db.execute("UPDATE campaign SET body_text='开奖另行通知' WHERE id=?", (cid,))
    assert repos.campaigns.get(cid)["expires_at"] is None


def test_expired_day_cannot_join_queue_or_write(repos, clock, gateway, account_id):
    from types import SimpleNamespace

    import pytest

    from tobili.domain.pool import PoolOp, PoolRejected
    from tobili.usecases.enroll import Enroll
    from tobili.usecases.pool import ManagePool

    cid = repos.campaigns.upsert(
        Campaign(
            dyn_id="500",
            up_uid="1",
            title="奖品",
            body_text="2026年11月3日开奖",
            published_at=datetime(2026, 11, 1, tzinfo=UTC),
        )
    )
    review_existing(repos.db, clock.now())
    pool = ManagePool(
        pool=repos.pool, campaigns=repos.campaigns, enrollments=repos.enrollments, clock=clock
    )
    with pytest.raises(PoolRejected):
        pool.edit(account_id, PoolOp.ADD, campaign_ids=[cid])
    enroll = Enroll(
        gateway=gateway,
        campaigns=repos.campaigns,
        enrollments=repos.enrollments,
        journal=SimpleNamespace(preflight=None),
        clock=clock,
    )
    result = enroll.run(account_id, "500")
    assert result.skipped and result.reason == "活动奖品已截止"
    assert not gateway.writes
