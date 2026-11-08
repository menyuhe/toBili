from datetime import UTC, datetime
from types import SimpleNamespace

from tobili.adapters.bili.feeds import _slice
from tobili.ports.models import FeedItem, FeedSlice
from tobili.usecases.scan_feeds import ScanFeeds
from tobili.usecases.scan_model import ScanModel, facts_for


def test_relative_dates_use_original_publication_in_china_timezone_and_cache(repos):
    import json

    calls = []
    text = "转发抽奖，奖品画册，下周五晚上八点开奖"

    def chat(messages, **kwargs):
        calls.append(json.loads(messages[-1]["content"]))
        return json.dumps({"deadline": "下周五晚上八点开奖", "reward": "画册"})

    model = ScanModel(SimpleNamespace(chat=chat, describe=lambda: {}), repos.db)
    # UTC Sunday evening is already Monday in China.
    published = datetime(2026, 11, 8, 17, tzinfo=UTC)
    item = FeedItem(dyn_id="123", up_uid="1", text=text, published_at=published)
    _, deadline, _ = facts_for(item, datetime(2026, 12, 1, tzinfo=UTC), model)
    assert deadline.isoformat() == "2026-11-20T20:00:00+08:00"
    assert calls[0]["published_at"] == "2026-11-09T01:00:00+08:00"
    assert facts_for(item, datetime(2026, 12, 30, tzinfo=UTC), model)[1] == deadline
    assert len(calls) == 1
    another = FeedItem(
        dyn_id="124", up_uid="1", text=text, published_at=datetime(2026, 11, 7, 17, tzinfo=UTC)
    )
    assert facts_for(another, datetime(2026, 12, 1, tzinfo=UTC), model)[1].day == 13
    assert len(calls) == 2
    assert facts_for(FeedItem(dyn_id="125", up_uid="1", text=text), published, None)[1] is None


def test_partial_collection_commits_success_and_resumes_failure(repos, gateway, clock, account_id):
    import json

    repos.feeds.add(account_id, "collection", "12", "合集")
    seen = []
    item = FeedItem(dyn_id="771", up_uid="12", text="转发抽奖，送一本画册")

    def scan(**kw):
        seen.append(kw["cursor"])
        if len(seen) == 1:
            return FeedSlice((item,), "article:1:88:digest:1", False, "读取冷却")
        return FeedSlice((), "article:2:::0", False)

    gateway.scan = scan
    scanner = ScanFeeds(gateway=gateway, feeds=repos.feeds, campaigns=repos.campaigns, clock=clock)
    assert scanner.run(account_id).problems
    assert repos.campaigns.by_dyn("771") is not None
    feed = repos.feeds.list_for(account_id)[0]
    assert feed["error"] == "读取冷却"
    assert json.loads(feed["cursor"][3:])["latest"] == "article:1:88:digest:1"
    assert scanner.run(account_id).ok
    feed = repos.feeds.list_for(account_id)[0]
    assert not feed["error"] and not json.loads(feed["cursor"][3:])["latest"]
    assert seen == ["", "article:1:88:digest:1"]


def test_model_evidence_cache_fallback_and_latest_scan(repos, gateway, clock, account_id):
    calls = []

    def chat(*args, **kwargs):
        calls.append(1)
        return '{"reward":"画册","deadline":"凭空编的时间","action":"转发"}'

    model = ScanModel(SimpleNamespace(chat=chat, describe=lambda: {"model": "fixture"}), repos.db)
    item = FeedItem(dyn_id="77", up_uid="12", text="转发抽奖，奖品为画册，数量两本")
    _, deadline, reward = facts_for(item, datetime.now(UTC), model)
    assert reward == "画册" and deadline is None
    assert facts_for(item, datetime.now(UTC), model)[2] == "画册" and len(calls) == 1

    def unavailable(*args, **kwargs):
        raise OSError("offline")

    model.llm.chat = unavailable
    assert model.read("另一篇抽奖正文") == {} and model.problem
    assert model.read("重试") == {}
    seen = []

    def scan(**kw):
        seen.append(kw["cursor"])
        return FeedSlice(items=(item,), cursor="next", exhausted=False)

    gateway.scan = scan
    repos.feeds.add(account_id, "space", "12", "测试")
    scanner = ScanFeeds(gateway=gateway, feeds=repos.feeds, campaigns=repos.campaigns, clock=clock)
    scanner.run(account_id)
    scanner.run(account_id)
    scanner.run(account_id, history=True)
    assert seen == ["", "", "next"]


def test_short_page_and_sliced_page_preserve_upstream_offset():
    page = {
        "data": {
            "items": [{"id_str": str(i)} for i in range(1, 5)],
            "offset": "later",
            "has_more": True,
        }
    }
    part = _slice(page, limit=2)
    assert part.cursor == "page:2:" and not part.exhausted
    rest = _slice(page, limit=2, start=2)
    assert rest.cursor == "later" and not rest.exhausted


def test_configured_http_model_extracts_only_evidence_and_never_raises_write_confidence(
    repos, gateway, clock, account_id
):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from tobili.adapters.llm.openai import OpenAICompatible

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            content = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert (
                self.path == "/v1/chat/completions"
                and self.headers["Authorization"] == "Bearer test"
            )
            assert "画册" in content["messages"][-1]["content"]
            result = {
                "reward": "画册",
                "kind": "comment",
                "action": "写下你的答案",
                "deadline": "不存在的时间",
            }
            data = json.dumps({"choices": [{"message": {"content": json.dumps(result)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            llm = OpenAICompatible(
                base_url=f"http://127.0.0.1:{server.server_port}/v1", model="test", api_key="test"
            )
            model = ScanModel(llm, repos.db)
            gateway.feed_items = [
                FeedItem(dyn_id="999", up_uid="11", text="写下你的答案，有机会赢取奖品画册")
            ]
            repos.feeds.add(account_id, "space", "11", "测试")
            scanner = ScanFeeds(
                gateway=gateway,
                feeds=repos.feeds,
                campaigns=repos.campaigns,
                clock=clock,
                model=model,
            )
            assert scanner.run(account_id).matched == 1
            _, card = repos.campaigns.by_dyn("999")
            assert (
                card.reward == "画册"
                and card.kind.value == "comment"
                and card.deadline is None
                and card.confidence == 0
            )
        finally:
            server.shutdown()
            worker.join()
