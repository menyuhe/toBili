"""Outgoing wire contracts: never turn a repost or reply into an original post."""

import pytest

from tobili.adapters.bili import endpoints
from tobili.adapters.bili.writer import BiliWriter
from tobili.domain.action import Action, ActionKind
from tobili.domain.errors import AmbiguousWrite


class Wire:
    csrf = "test-csrf"

    def __init__(self):
        self.calls = []

    def submit_json(self, url, body, **kwargs):
        self.calls.append((url, body, kwargs))
        return {"code": 0, "data": {"dyn_id_str": "987654321"}}

    def submit(self, url, body, **kwargs):
        self.calls.append((url, body, kwargs))
        return {"code": 0, "data": {"rpid_str": "98765"}}

    def fetch(self, url, **kwargs):
        assert url == endpoints.DYN_DETAIL
        assert kwargs["query"]["id"] == "987654321"
        return {
            "code": 0,
            "data": {
                "item": {
                    "id_str": "987654321",
                    "type": "DYNAMIC_TYPE_FORWARD",
                    "orig": {"id_str": "123456789"},
                }
            },
        }


def test_repost_is_attached_to_original_and_not_text_creation():
    wire = Wire()
    receipt = BiliWriter(wire).perform(Action(ActionKind.SHARE, "123456789", {"text": "希望抽到"}))
    assert receipt.ok and receipt.remote_id == "987654321"
    assert len(wire.calls) == 1
    url, body, options = wire.calls[0]
    assert url == endpoints.PUBLISH_DYN
    assert body["dyn_req"]["scene"] == 4
    assert body["web_repost_src"] == {"dyn_id_str": "123456789"}
    assert "dyn_id_str" not in body
    assert body["dyn_req"]["content"]["contents"][0]["raw_text"] == "希望抽到"
    assert options["query"]["csrf"] == wire.csrf


@pytest.mark.parametrize("target", ["", "bad", "１２３"])
def test_invalid_original_never_posts(target):
    wire = Wire()
    assert not BiliWriter(wire).perform(Action(ActionKind.SHARE, target, {"text": "你好"})).ok
    assert wire.calls == []


@pytest.mark.parametrize("reply_type", [1, 11, 17])
def test_comment_only_uses_reply_api(reply_type):
    wire = Wire()
    result = BiliWriter(wire).perform(
        Action(
            ActionKind.COMMENT,
            "123",
            {"text": "希望抽到", "rid": "456", "comment_type": str(reply_type)},
        )
    )
    assert result.ok
    url, form, _ = wire.calls[0]
    assert url == endpoints.ADD_REPLY
    assert form["oid"] == "456" and form["type"] == reply_type
    assert "dyn_req" not in form
    assert len(wire.calls) == 1


def test_ambiguous_repost_does_not_retry_or_fallback():
    class TimeoutWire(Wire):
        def submit_json(self, *args, **kwargs):
            super().submit_json(*args, **kwargs)
            raise AmbiguousWrite("timeout")

    wire = TimeoutWire()
    result = BiliWriter(wire).perform(Action(ActionKind.SHARE, "123", {"text": "你好"}))
    assert result.ambiguous and not result.ok
    assert len(wire.calls) == 1


@pytest.mark.parametrize(
    "item",
    [
        {"id_str": "987654321", "type": "DYNAMIC_TYPE_WORD"},
        {"id_str": "987654321", "type": "DYNAMIC_TYPE_FORWARD", "orig": {"id_str": "111"}},
        {},
    ],
)
def test_create_success_is_not_repost_success_without_original(item):
    class Unverified(Wire):
        def fetch(self, *args, **kwargs):
            return {"code": 0, "data": {"item": item}}

    wire = Unverified()
    result = BiliWriter(wire).perform(Action(ActionKind.SHARE, "123456789", {"text": "希望抽到"}))
    assert result.ambiguous and not result.ok
    assert result.remote_id == "987654321"
    assert len(wire.calls) == 1
