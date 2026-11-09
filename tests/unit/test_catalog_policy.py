from datetime import timedelta
from types import SimpleNamespace

import pytest

from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.prize_label import concise_reward
from tobili.ports.models import FeedItem, FeedSlice
from tobili.usecases.catalog_controls import edit_deadline
from tobili.usecases.catalog_policy import choose_deadline, maintain
from tobili.usecases.scan_feeds import ScanFeeds


@pytest.mark.parametrize(
    "before,after",
    [
        ("抽2位小伙伴送出：【京东卡50元】1张", "京东e卡50元 ×1"),
        ("抽取一位董事送【科睿帆布袋周边*1】", "科睿帆布袋周边 ×1"),
        ("ROG STRIX B850-G；RTX 5070", "ROG STRIX B850-G；RTX 5070"),
        ("AMD电动螺丝刀 ×2份", "AMD电动螺丝刀 ×2"),
    ],
)
def test_compact_reward(before, after):
    assert concise_reward(before) == after


def test_default_dates_use_beijing_midnight():
    r = dict(deadline_at=None, published_at="2026-11-08T17:30:00+00:00", body_text="")
    dt, _ = choose_deadline(r)
    assert dt.isoformat() == "2026-11-16T00:00:00+08:00"
    r.update(candidate_at="2026-11-17T23:59:00+08:00", precision="day", date_state="date_only")
    assert choose_deadline(r)[0].isoformat() == "2026-11-17T00:00:00+08:00"


def test_legacy_day_default_normalizes_but_human_and_official_time_stay():
    r = dict(
        deadline_at="2026-11-16T23:59:00+08:00",
        published_at="2026-11-09T00:42:00+08:00",
        body_text="评论区抽随机游戏，11月16日开奖",
    )
    assert choose_deadline(r)[0].hour == 0
    assert choose_deadline({**r, "date_pinned": True})[0].hour == 23
    assert choose_deadline({**r, "draw_state": "active"})[0].hour == 23


def test_cleanup_keeps_journal_identity_and_hides_reimport(repos, clock, account_id):
    now = clock.now()
    good = repos.campaigns.upsert(
        Campaign(
            dyn_id="1",
            up_uid="2",
            title="奖",
            kind=CampaignKind.SHARE,
            reward="鼠标",
            deadline=now + timedelta(days=2),
            confidence=1,
        )
    )
    bad = repos.campaigns.upsert(
        Campaign(
            dyn_id="2",
            up_uid="2",
            title="奖",
            kind=CampaignKind.SHARE,
            reward="鼠标",
            deadline=now - timedelta(days=1),
            confidence=1,
        )
    )
    repos.campaigns.upsert(
        Campaign(
            dyn_id="3",
            up_uid="2",
            title="奖",
            kind=CampaignKind.CHECK_IN,
            reward="鼠标",
            deadline=now + timedelta(days=1),
            confidence=1,
        )
    )
    with repos.db.write() as c:
        c.execute("UPDATE campaign SET reward_status='verified'")
    eid = repos.enrollments.create(account_id, bad, now=now)
    repos.settings.put(0, "catalog.curated", "1")
    assert maintain(repos.db, now, discard_pending=True)["removed"] == 2
    assert repos.campaigns.get(bad)["id"] == bad
    assert repos.db.connect().execute("SELECT id FROM enrollment WHERE id=?", (eid,)).fetchone()
    repos.campaigns.upsert(
        Campaign(
            dyn_id="2",
            up_uid="2",
            title="再次导入",
            kind=CampaignKind.SHARE,
            deadline=now + timedelta(days=4),
            confidence=1,
        )
    )
    ids = [r["id"] for r in repos.campaigns.candidates(account_id, now=now, floor=0, limit=10)]
    assert ids == [good]
    assert repos.campaigns.removed("2") and repos.campaigns.removed("3")
    assert maintain(repos.db, now, discard_pending=True)["removed"] == 0


def test_manual_time_survives_source_and_rejects_stale_or_naive(repos, clock):
    cid = repos.campaigns.upsert(
        Campaign(
            dyn_id="9",
            up_uid="2",
            title="奖",
            kind=CampaignKind.SHARE,
            reward="鼠标",
            published_at=clock.now(),
        )
    )
    box = SimpleNamespace(db=repos.db, clock=clock)
    edit_deadline(box, cid, "2026-11-20T18:30:00+08:00", None)
    repos.campaigns.upsert(Campaign(dyn_id="9", up_uid="2", title="奖", deadline=clock.now()))
    repos.settings.put(0, "catalog.curated", "1")
    maintain(repos.db, clock.now())
    assert repos.campaigns.get(cid)["deadline_at"] == "2026-11-20T10:30:00+00:00"
    with pytest.raises(ValueError):
        edit_deadline(box, cid, "2026-11-21T18:30:00+08:00", None)
    with pytest.raises(ValueError):
        edit_deadline(box, cid, "2026-11-21T18:30:00", None)


def test_curated_candidates_wait_for_default_time_after_shared_import(repos, clock, account_id):
    cid = repos.campaigns.upsert(
        Campaign(
            dyn_id="909",
            up_uid="2",
            title="奖",
            kind=CampaignKind.SHARE,
            reward="鼠标",
            published_at=clock.now(),
            confidence=1,
        )
    )
    with repos.db.write() as c:
        c.execute("UPDATE campaign SET reward_status='verified' WHERE id=?", (cid,))
    repos.settings.put(0, "catalog.curated", "1")
    assert not repos.campaigns.candidates(account_id, now=clock.now(), floor=0, limit=10)
    maintain(repos.db, clock.now())
    assert (
        repos.campaigns.candidates(account_id, now=clock.now(), floor=0, limit=10)[0]["id"] == cid
    )


def test_topic_seven_day_window_stops_old_pages(repos, clock, account_id, gateway):
    now = clock.now()
    calls = []
    repos.feeds.add(account_id, "topic", "12", "topic")

    def page(**kw):
        calls.append(kw["cursor"])
        age = 2 if len(calls) == 1 else 8
        return FeedSlice(
            (
                FeedItem(
                    dyn_id=str(len(calls)),
                    up_uid="2",
                    text="转发抽奖，送鼠标一份",
                    published_at=now - timedelta(days=age),
                ),
            ),
            "page:next",
            False,
        )

    gateway.scan = page
    scan = ScanFeeds(gateway=gateway, feeds=repos.feeds, campaigns=repos.campaigns, clock=clock)
    report = scan.run(account_id, pages=5)
    assert not report.problems, report.problems
    assert len(calls) == 2
    assert repos.campaigns.by_dyn("1") and not repos.campaigns.by_dyn("2")
