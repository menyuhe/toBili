from types import SimpleNamespace

from tobili.adapters.bili.prober import BiliProber, _reserve_state
from tobili.domain.action import ActionKind
from tobili.domain.probe import ProbeState


def prober(answer):
    calls = []
    reader = SimpleNamespace(
        profile=lambda: SimpleNamespace(uid="123"),
        _reply_page=lambda **kw: calls.append(kw) or answer,
    )
    return BiliProber(None, reader), calls


def test_comment_probe_uses_signed_reader_and_zero_is_known_empty():
    probe, calls = prober({"code": 0, "data": {"replies": [], "cursor": {"all_count": 0}}})
    assert (
        probe.did(ActionKind.COMMENT, dyn_id="100", payload={"rid": "200", "comment_type": "11"})
        is ProbeState.ABSENT
    )
    assert calls[0]["rid"] == "200" and calls[0]["comment_type"] == 11 and calls[0]["resign"]


def test_partial_comments_do_not_authorize_duplicate_post():
    probe, _ = prober(
        {"code": 0, "data": {"replies": [{"mid": "99"}], "cursor": {"all_count": 900}}}
    )
    assert probe.did(ActionKind.COMMENT, dyn_id="100", payload={}) is ProbeState.UNKNOWN
    assert "900" in probe.last_reason


def test_existing_comment_is_recognized_even_with_partial_list():
    probe, _ = prober(
        {"code": 0, "data": {"replies": [{"mid": "123"}], "cursor": {"all_count": 900}}}
    )
    assert probe.did(ActionKind.COMMENT, dyn_id="100", payload={}) is ProbeState.PRESENT


def test_missing_reply_response_is_explained_without_writing():
    probe, _ = prober(None)
    assert probe.did(ActionKind.COMMENT, dyn_id="100", payload={}) is ProbeState.UNKNOWN
    assert "签名评论查询" in probe.last_reason


def test_empty_personal_feed_requires_explicit_end_marker():
    for data, expected in [
        ({"items": [], "has_more": 0}, ProbeState.ABSENT),
        ({"items": []}, ProbeState.UNKNOWN),
    ]:
        probe, _ = prober(None)
        probe._transport = SimpleNamespace(
            fetch=lambda *a, data=data, **k: {"code": 0, "data": data}
        )
        assert probe.did(ActionKind.SHARE, dyn_id="1", payload={}) is expected


def test_pinned_old_post_cannot_hide_actual_repost_later_in_page():
    data = {
        "items": [
            {"modules": {"module_author": {"pub_ts": 1}, "module_tag": {"text": "置顶"}}},
            {"orig": {"id_str": "1"}, "modules": {"module_author": {"pub_ts": 100}}},
        ],
        "has_more": 0,
    }
    probe, _ = prober(None)
    probe._transport = SimpleNamespace(fetch=lambda *a, **k: {"code": 0, "data": data})
    assert (
        probe.did(ActionKind.SHARE, dyn_id="1", payload={"published_at": "50"})
        is ProbeState.PRESENT
    )


def test_reservation_reads_account_button_not_event_state():
    def item(reserve):
        return {"modules": {"module_dynamic": {"additional": {"reserve": reserve}}}}

    assert _reserve_state(item({"button": {"status": 1}, "state": 2})) is False
    assert _reserve_state(item({"button": {"status": 2}, "state": 1})) is True
    assert _reserve_state(item({"state": 1})) is None
    assert _reserve_state(item({"button": {"status": 9}})) is None
