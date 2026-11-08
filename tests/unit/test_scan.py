"""扫描用例：粗筛 + 入库。"""

from __future__ import annotations

from tobili.ports.models import FeedItem
from tobili.usecases.scan_feeds import ScanFeeds


def _scanner(repos, gateway, clock):  # type: ignore[no-untyped-def]
    return ScanFeeds(
        gateway=gateway,
        feeds=repos.feeds,
        campaigns=repos.campaigns,
        clock=clock,
    )


def test_no_feeds_reports_problem(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    report = _scanner(repos, gateway, clock).run(account_id)
    assert report.problems and "订阅源" in report.problems[0]


def test_filtered_scan_excludes_hidden_disabled_and_other_accounts(
    repos, gateway, clock, account_id
):
    from tobili.ports.models import AccountProfile

    first = repos.feeds.add(account_id, "space", "2002", "visible")
    hidden = repos.feeds.add(account_id, "space", "2003", "hidden")
    other = repos.accounts.upsert(AccountProfile(uid="33", nickname="B"), "b")
    foreign = repos.feeds.add(other, "space", "2004", "foreign")
    result = _scanner(repos, gateway, clock).run(account_id, feed_ids=[first, foreign])
    rows = {f["id"]: f for f in repos.feeds.list_for(account_id)}
    assert result.feeds == 1 and rows[first]["last_scan_at"]
    assert not rows[hidden]["last_scan_at"]
    assert not repos.feeds.list_for(other)[0]["last_scan_at"]


def test_matches_campaign_and_stores(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    repos.feeds.add(account_id, "space", "2002", "某个 UP")
    gateway.feed_items = [
        FeedItem(dyn_id="dyn-1", up_uid="2002", text="转发抽奖！1月5日 23:59 截止，送出大会员"),
        FeedItem(dyn_id="dyn-2", up_uid="2002", text="今天吃了拉面"),
    ]

    report = _scanner(repos, gateway, clock).run(account_id)

    assert report.scanned == 2
    assert report.matched == 1
    assert report.unique == 1
    assert report.sources == 1
    assert repos.campaigns.by_dyn("dyn-1") is not None
    assert repos.campaigns.by_dyn("dyn-2") is None


def test_second_scan_does_not_duplicate(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    repos.feeds.add(account_id, "space", "2002", "某个 UP")
    gateway.feed_items = [
        FeedItem(dyn_id="dyn-1", up_uid="2002", text="转发抽奖 送出 大会员"),
    ]
    scanner = _scanner(repos, gateway, clock)

    scanner.run(account_id)
    scanner.run(account_id)

    count = repos.db.connect().execute("SELECT COUNT(*) AS n FROM campaign").fetchone()["n"]
    assert count == 1


def test_cursor_is_saved(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    feed_id = repos.feeds.add(account_id, "space", "2002", "某个 UP")
    gateway.feed_items = [FeedItem(dyn_id="dyn-1", up_uid="2002", text="转发抽奖 送出 礼物")]

    _scanner(repos, gateway, clock).run(account_id)

    rows = repos.feeds.list_for(account_id)
    assert rows[0]["id"] == feed_id
    assert rows[0]["last_scan_at"] is not None
