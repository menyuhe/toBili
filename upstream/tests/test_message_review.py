import time
import pytest
from src import accounts, message_review as review
from src.message_api import GET_SESSIONS_URL, FETCH_SESSION_MSGS_URL


class Messages:
    def __init__(self):
        self.messages = []
        self.at = []
        self.unread = 0
        self.at_unread = 0
        self.fail = ""
        self.page_size = 2
        self.calls = []

    def request_json(self, url, params=None, **kwargs):
        params = params or {}
        self.calls.append((url, params))
        if self.fail and self.fail in url:
            return {"code": -101, "message": "expired"}
        if "single_unread" in url:
            data = {"follow_unread": self.unread, "unfollow_unread": 0}
        elif "msgfeed/unread" in url:
            data = {"at": self.at_unread}
        elif url == GET_SESSIONS_URL:
            data = {"session_list": [{"talker_id": 9, "unread_count": self.unread,
                                      "session_type": 1, "session_ts": 12345678}], "has_more": 0}
        elif url == FETCH_SESSION_MSGS_URL:
            end = int(params.get("end_seqno") or 999999)
            rows = [r for r in self.messages if r["msg_seqno"] < end]
            data = {"messages": rows[:self.page_size], "has_more": int(len(rows) > self.page_size)}
        elif url.endswith("/at"):
            end = int(params.get("id") or 999999)
            rows = [r for r in self.at if r["id"] < end]
            page = rows[:self.page_size]
            data = {"items": page, "cursor": {"is_end": len(rows) <= self.page_size,
                    "id": page[-1]["id"] if page else 0, "time": int(time.time())}}
        else:
            raise AssertionError(url)
        return {"code": 0, "data": data}


def dm(seq, source=7, sender=9):
    return {"msg_key": str(seq), "msg_seqno": seq, "sender_uid": sender,
            "msg_source": source, "timestamp": int(time.time()), "content": '{"content":"消息正文"}'}


def at(seq):
    return {"id": seq, "reply_time": int(time.time()), "user": {"mid": 9},
            "item": {"source_content": "@中奖通知"}}


@pytest.fixture(autouse=True)
def isolated(isolated_home):
    pass


def test_initial_unread_excludes_only_platform_auto_reply_and_own_messages():
    client = Messages()
    client.messages = [dm(5, sender=101), dm(4), dm(3, source=8), dm(2, source=9), dm(1)]
    client.unread = 3
    result = review.check_messages("101", client)
    assert result["pending"] == 1
    assert result["items"][0]["id"] == "4"
    assert accounts.read_state("101", "message_review")["baseline"]["dm:9"] == 5


def test_seen_on_phone_and_unchanged_unread_still_find_new_and_deduplicate():
    client = Messages()
    client.messages = [dm(1, source=8)]
    assert review.check_messages("101", client)["pending"] == 0
    client.messages = [dm(3), dm(2), dm(1, source=8)]
    client.at = [at(2), at(1)]
    assert review.check_messages("101", client)["pending"] == 4
    assert review.check_messages("101", client)["pending"] == 4
    assert review.check_messages("202", Messages())["pending"] == 0
    review.resolve_message("101", "dm:9:3", "irrelevant")
    assert review.check_messages("101", client)["pending"] == 3


def test_partial_failure_does_not_advance_any_cursor_and_blocks():
    client = Messages()
    client.messages = [dm(1)]
    review.check_messages("101", client)
    baseline = accounts.read_state("101", "message_review")["baseline"]
    client.messages = [dm(2), dm(1)]
    client.fail = "/x/msgfeed/at"
    result = review.check_messages("101", client)
    assert result["blocked"] and result["error"]
    assert accounts.read_state("101", "message_review")["baseline"] == baseline
    client.fail = ""
    assert review.check_messages("101", client)["pending"] == 1


def test_many_pages_and_new_at_does_not_reclassify_old_read_messages():
    client = Messages()
    client.at = [at(i) for i in range(10, 0, -1)]
    client.at_unread = 1
    assert review.check_messages("101", client)["pending"] == 1
    client.at = [at(i) for i in range(17, 0, -1)]
    client.at_unread = 0
    assert review.check_messages("101", client)["pending"] == 8


def test_missing_private_channel_is_not_zero_unread():
    client = Messages()
    client.fail = "single_unread"
    assert review.check_messages("101", client)["blocked"]
    assert not accounts.read_state("101", "message_review").get("baseline")


def test_confirming_message_does_not_confirm_later_arrivals():
    client = Messages()
    client.messages, client.unread = [dm(1)], 1
    review.check_messages("101", client)
    client.messages = [dm(2), dm(1)]
    review.check_messages("101", client)
    review.resolve_message("101", "dm:9:1", "irrelevant")
    assert review.review_status("101")["pending"] == 1
    with pytest.raises(ValueError):
        review.resolve_message("202", "dm:9:2", "irrelevant")


def test_cleanup_gate_blocks_writes_then_requires_fresh_check(monkeypatch):
    from tests.test_maintenance import FakeApi, seed, UID
    from src import maintenance
    seed()
    client = Messages()
    client.messages, client.unread = [dm(1)], 1
    api = FakeApi()
    api.client = client
    monkeypatch.setattr(maintenance.time, "time", lambda: 2_000_000_000)
    monkeypatch.setattr(maintenance, "safe_enqueue", lambda *a, **k: None)
    result = maintenance.run_maintenance(UID, clean=True, api=api)
    assert not result["ok"]
    assert not api.deleted and not api.unfollowed
    review.resolve_message(UID, "dm:9:1", "irrelevant")
    client.messages = [dm(2), dm(1)]
    maintenance.run_maintenance(UID, clean=True, api=api)
    assert not api.deleted and not api.unfollowed
    review.resolve_message(UID, "dm:9:2", "irrelevant")
    result = maintenance.run_maintenance(UID, clean=True, api=api)
    assert result["ok"]
    assert api.deleted == ["810"] and api.unfollowed == ["900"]


def test_message_win_overrides_conflicting_official_loss_and_protects(monkeypatch):
    from tests.test_maintenance import seed, UID
    from src import maintenance
    seed()
    client = Messages()
    client.messages, client.unread = [dm(1)], 1
    review.check_messages(UID, client)
    review.resolve_message(UID, "dm:9:1", "won", "10")
    result = maintenance.get_outcome(UID, "10")
    assert result["outcome"] == "won" and result["keep"]
    assert "冲突" in result["reason"]


def test_background_clean_stays_paused_after_messages_are_resolved(monkeypatch):
    from tests.test_maintenance import FakeApi, seed, UID
    from src import maintenance
    monkeypatch.setattr(maintenance.time, "time", lambda: 2_000_000_000)
    monkeypatch.setattr(maintenance, "safe_enqueue", lambda *a, **k: None)
    seed()
    api = FakeApi()
    api.client.messages, api.client.unread = [dm(1)], 1
    maintenance.run_maintenance(UID, clean=True, api=api, manual_clean=False)
    review.resolve_message(UID, "dm:9:1", "irrelevant")
    maintenance.run_maintenance(UID, clean=True, api=api, manual_clean=False)
    assert not api.deleted and not api.unfollowed
    maintenance.run_maintenance(UID, clean=True, api=api, manual_clean=True)
    assert api.deleted == ["810"]


def test_transient_error_recovers_without_persistent_message_hold():
    client = Messages()
    client.fail = "single_unread"
    assert review.check_messages("101", client)["blocked"]
    client.fail = ""
    assert not review.check_messages("101", client)["blocked"]


def test_unchanged_session_watermark_skips_body_fetch(monkeypatch):
    client = Messages()
    client.messages = [dm(1)]
    review.check_messages("101", client)
    original = client.request_json
    def request(url, **kwargs):
        result = original(url, **kwargs)
        if url == GET_SESSIONS_URL:
            result["data"]["session_list"][0]["last_msg"] = {"msg_seqno": 1}
        return result
    monkeypatch.setattr(client, "request_json", request)
    client.calls.clear()
    assert not review.check_messages("101", client)["blocked"]
    assert not any(url == FETCH_SESSION_MSGS_URL for url, params in client.calls)


def test_transient_recovery_preserves_real_message_hold():
    client = Messages()
    client.messages, client.unread = [dm(1)], 1
    assert review.check_messages("101", client)["pending"] == 1
    client.fail = "single_unread"
    assert review.check_messages("101", client)["error"]
    review.resolve_message("101", "dm:9:1", "irrelevant")
    client.fail = ""
    result = review.check_messages("101", client)
    assert result["blocked"] and result["cleanup_hold"]
    assert not review.release_cleanup_hold("101")["blocked"]
