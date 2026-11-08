"""跨源去重：一个活动被多个源看到，仍然是同一个活动。"""

from __future__ import annotations

from tobili.ports.models import FeedItem
from tobili.usecases.scan_feeds import ScanFeeds


def _scanner(repos, gateway, clock):  # type: ignore[no-untyped-def]
    return ScanFeeds(gateway=gateway, feeds=repos.feeds, campaigns=repos.campaigns, clock=clock)


def test_same_campaign_from_two_sources_is_one_record(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    repos.feeds.add(account_id, "following", "", "关注流")
    repos.feeds.add(account_id, "space", "2002", "某个 UP")
    gateway.feed_items = [FeedItem(dyn_id="dyn-1", up_uid="2002", text="转发抽奖，送出大会员")]

    report = _scanner(repos, gateway, clock).run(account_id)

    assert report.scanned == 2
    assert report.matched == 2
    assert report.unique == 1
    assert report.duplicates == 1

    total = repos.db.connect().execute("SELECT COUNT(*) AS n FROM campaign").fetchone()["n"]
    assert total == 1

    campaign_id = repos.campaigns.by_dyn("dyn-1")[0]
    labels = repos.campaigns.sources_of(campaign_id)
    assert labels == ["关注流", "某个 UP"]


def test_source_is_recorded_once_per_feed(repos, gateway, clock, account_id) -> None:  # type: ignore[no-untyped-def]
    repos.feeds.add(account_id, "space", "2002", "某个 UP")
    gateway.feed_items = [FeedItem(dyn_id="dyn-1", up_uid="2002", text="转发抽奖 送出 礼物")]
    scanner = _scanner(repos, gateway, clock)

    scanner.run(account_id)
    second = scanner.run(account_id)

    assert second.sources == 0, "同一个源第二次看到同一条活动，不该重复记来源"
    campaign_id = repos.campaigns.by_dyn("dyn-1")[0]
    assert repos.campaigns.sources_of(campaign_id) == ["某个 UP"]
