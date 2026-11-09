import pytest

from tobili.adapters.bili.parser import text_of
from tobili.adapters.bili.public_sources import (
    ARTICLE,
    ARTICLES,
    PublicSources,
    activity_links,
)
from tobili.ports.models import FeedItem, FeedSlice
from tobili.usecases.manage_feeds import FeedRejected, clean
from tobili.usecases.scan_feeds import ScanFeeds


class Wire:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def fetch(self, url, **kwargs):
        self.calls.append((url, kwargs["query"]))
        return {"code": 0, "data": self.pages(url, kwargs["query"])}


def test_topic_unwraps_original_and_keeps_partial_page():
    wire = Wire(
        lambda *args: {
            "topic_card_list": {
                "items": [
                    {
                        "dynamic_card_item": {
                            "id_str": "99",
                            "type": "DYNAMIC_TYPE_FORWARD",
                            "orig": {"id_str": "42"},
                        }
                    },
                    {"dynamic_card_item": {"id_str": "43"}},
                ],
                "offset": "next",
                "has_more": True,
            }
        }
    )
    source = PublicSources(wire)
    page = source.scan(kind="topic", target="12", cursor="", limit=1)
    assert page.items[0].dyn_id == "42" and page.cursor == "page:1:"
    page = source.scan(kind="topic", target="12", cursor=page.cursor, limit=1)
    assert page.items[0].dyn_id == "43" and page.cursor == "next"
    assert wire.calls[0][1]["sort_by"] == 3


def test_topic_broken_page_cannot_silently_advance():
    for data in ({}, {"topic_card_list": {"items": [], "has_more": True, "offset": "x"}}):
        with pytest.raises(ValueError):
            PublicSources(Wire(lambda *args, data=data: data)).scan(
                kind="topic", target="12", cursor="x", limit=2
            )


def test_collection_only_body_links_deduplicate_and_bound_work():
    article = {
        "content": '<a href="https://www.bilibili.com/opus/11">a</a> https://t.bilibili.com/12 https://www.bilibili.com/opus/11',
        "opus": {"opus_id": "90"},
        "recommend": "https://t.bilibili.com/99",
    }
    assert activity_links(article) == ["11", "12"]

    def pages(url, query):
        if url == ARTICLES:
            return {"articles": [{"id": 70}]}
        if url == ARTICLE:
            return article
        return {
            "item": {
                "id_str": query["id"],
                "modules": {"module_dynamic": {"desc": {"text": "转发抽奖送礼物"}}},
            }
        }

    wire = Wire(pages)
    source = PublicSources(wire)
    page = source.scan(kind="collection", target="7", cursor="", limit=1)
    assert page.items[0].dyn_id == "11"
    page2 = source.scan(kind="collection", target="7", cursor=page.cursor, limit=1)
    assert page2.items[0].dyn_id == "12" and page2.cursor == "article:2:::0"
    article["content"] = "https://t.bilibili.com/13 " + article["content"]
    assert (
        source.scan(kind="collection", target="7", cursor=page.cursor, limit=1).items[0].dyn_id
        == "13"
    )


def test_collection_latest_resumes_without_consuming_history(repos, gateway, clock, account_id):
    feed = repos.feeds.add(account_id, "collection", "1", "合集")
    calls = []

    def scan(**kwargs):
        calls.append(kwargs["cursor"])
        cursor = "article:1:70:hash:10" if not kwargs["cursor"] else "article:2:::0"
        return FeedSlice((FeedItem("12", "1", "转发抽奖送礼物"),), cursor, False)

    gateway.scan = scan
    scanner = ScanFeeds(gateway=gateway, feeds=repos.feeds, campaigns=repos.campaigns, clock=clock)
    scanner.run(account_id, feed_id=feed)
    scanner.run(account_id, feed_id=feed)
    scanner.run(account_id, feed_id=feed)
    # Resume unfinished pages immediately, then reuse the completed shared update.
    assert calls == ["", "article:1:70:hash:10"]


def test_topic_url_and_collection_uid_validation():
    assert clean("topic", "https://www.bilibili.com/v/topic/detail?topic_id=1040028") == (
        "topic",
        "1040028",
    )
    assert clean("collection", "https://space.bilibili.com/123/upload/opus") == (
        "collection",
        "123",
    )
    for url in ("https://evil.test/v/topic/detail?topic_id=123", "１２３", "0"):
        with pytest.raises(FeedRejected):
            clean("topic", url)


def test_opus_summary_is_readable_for_rules_and_model():
    assert (
        text_of(
            {
                "modules": {
                    "module_dynamic": {"major": {"opus": {"summary": {"text": "转发抽奖送礼物"}}}}
                }
            }
        )
        == "转发抽奖送礼物"
    )


def test_risk_response_cools_down_without_repeated_requests():
    from tobili.domain.errors import Fault, RemoteError

    calls = []

    def blocked(url, query):
        calls.append(url)
        raise RemoteError(Fault.REMOTE_BUSINESS, "limited", code=412)

    wire = Wire(blocked)
    for _ in range(2):
        with pytest.raises(RemoteError) as error:
            PublicSources(wire).scan(kind="topic", target="12", cursor="", limit=2)
        assert error.value.fault == Fault.RISK
    assert len(calls) == 1


def test_failed_collection_retains_cursor(repos, gateway, clock, account_id):
    feed = repos.feeds.add(account_id, "collection", "7", "合集")
    repos.feeds.save_cursor(feed, "article:1:70:hash:10", now=clock.now())

    def fail(**kwargs):
        raise ValueError("offline")

    gateway.scan = fail
    scanner = ScanFeeds(gateway=gateway, feeds=repos.feeds, campaigns=repos.campaigns, clock=clock)
    assert scanner.run(account_id, history=True, feed_id=feed).problems
    assert repos.feeds.list_for(account_id)[0]["cursor"] == "article:1:70:hash:10"


def test_opus_arrays_extract_body_and_author():
    from tobili.adapters.bili.parser import to_feed_item
    from tobili.adapters.bili.public_sources import opus_item

    raw = {
        "id_str": "123",
        "modules": [
            {"module_author": {"mid": 9, "name": "作者", "pub_ts": "1790498510"}},
            {"module_title": {"text": "抽奖"}},
            {
                "module_content": {
                    "paragraphs": [{"text": {"nodes": [{"word": {"words": "转发送礼物"}}]}}]
                }
            },
        ],
    }
    item = to_feed_item(opus_item(raw))
    assert item.up_uid == "9" and "转发送礼物" in item.text and item.published_at


def test_navigation_links_not_activities():
    assert activity_links(
        {
            "content": '<a href="https://t.bilibili.com/1">往期合集</a><a href="https://t.bilibili.com/2">奖品</a>'
        }
    ) == ["2"]


def test_partial_collection_failure_returns_success_and_resume_cursor():
    from tobili.domain.errors import Fault, RemoteError

    article = {"content": "https://t.bilibili.com/1 https://t.bilibili.com/2"}

    def pages(url, query):
        if url == ARTICLES:
            return {"articles": [{"id": 7}]}
        if url == ARTICLE:
            return article
        if query["id"] == "2":
            raise RemoteError(Fault.NETWORK, "offline")
        return {
            "item": {"id_str": "1", "modules": {"module_dynamic": {"desc": {"text": "转发抽奖"}}}}
        }

    page = PublicSources(Wire(pages)).scan(kind="collection", target="7", cursor="", limit=2)
    assert [x.dyn_id for x in page.items] == ["1"] and page.cursor.endswith(":1") and page.problem


def test_persistent_cache_and_cooldown_survive_restart(repos, account_id):
    from tobili.adapters.sqlite.source_cache import SourceCache
    from tobili.domain.errors import Fault, RemoteError

    first = Wire(lambda *args: {"topic_card_list": {"items": [], "has_more": False}})
    first.source_cache = SourceCache(repos.db, account_id)
    PublicSources(first).scan(kind="topic", target="1", cursor="", limit=1)
    second = Wire(lambda *args: (_ for _ in ()).throw(AssertionError("network called")))
    second.source_cache = SourceCache(repos.db, account_id)
    PublicSources(second).scan(kind="topic", target="1", cursor="", limit=1)
    assert not second.calls

    def blocked(*args):
        raise RemoteError(Fault.RISK, "risk", code=412)

    first.pages = blocked
    with pytest.raises(RemoteError):
        PublicSources(first).scan(kind="topic", target="2", cursor="", limit=1)
    with pytest.raises(RemoteError):
        PublicSources(second).scan(kind="topic", target="2", cursor="", limit=1)
    assert not second.calls
