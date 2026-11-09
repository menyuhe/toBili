from types import SimpleNamespace

import pytest

from tobili.adapters.bili.history_probe import ShareHistory
from tobili.adapters.bili.prober import BiliProber
from tobili.domain.action import ActionKind
from tobili.domain.errors import Fault, RemoteError
from tobili.domain.probe import ProbeState as S


def row(number, original="", pinned=False):
    return dict(
        id_str=str(number),
        orig={"id_str": original} if original else {},
        modules=dict(
            module_author=dict(mid=123, pub_ts=number),
            module_tag={"text": "置顶"} if pinned else {},
        ),
    )


def wire(pages):
    calls = []

    def fetch(*a, **kw):
        offset = kw["query"]["offset"]
        calls.append(offset)
        return {"code": 0, "data": pages[offset]}

    return SimpleNamespace(fetch=fetch), calls


def test_share_scan_continues_past_three_pages_and_refreshes_head():
    pages = {
        str(n) if n else "": dict(items=[row(100 - n)], has_more=1, offset=str(n + 1))
        for n in range(6)
    }
    pages["5"]["has_more"] = 0
    history = ShareHistory()
    transport, calls = wire(pages)
    assert history.check(transport, "123", "target", 1)[0] is S.UNKNOWN
    assert calls == ["", "1", "2"]
    assert history.check(transport, "123", "target", 1)[0] is S.UNKNOWN
    assert calls[-3:] == ["", "3", "4"]
    assert history.check(transport, "123", "target", 1)[0] is S.ABSENT
    assert calls[-2:] == ["", "5"]
    # A new manual repost after the prior absence must be seen.
    pages[""]["items"].insert(0, row(101, "target"))
    assert history.check(transport, "123", "target", 1)[0] is S.PRESENT


def test_new_head_stitches_to_old_history_without_restarting_all_pages():
    pages = {
        "": dict(items=[row(100), row(99)], offset="2", has_more=1),
        "2": dict(items=[row(98), row(97)], offset="3", has_more=1),
        "3": dict(items=[row(96), row(95)], offset="4", has_more=1),
        "4": dict(items=[row(94, "target")], offset="", has_more=0),
    }
    transport, calls = wire(pages)
    history = ShareHistory()
    assert history.check(transport, "123", "target", 1)[0] is S.UNKNOWN
    pages[""]["items"] = [row(102), row(101), row(100)]
    assert history.check(transport, "123", "target", 1)[0] is S.PRESENT
    assert calls[-2:] == ["", "4"]


def test_late_repost_in_same_page_beats_early_time_boundary():
    transport, _ = wire({"": dict(items=[row(1), row(100, "target")], has_more=0)})
    assert ShareHistory().check(transport, "123", "target", 50)[0] is S.PRESENT


@pytest.mark.parametrize(
    "page",
    [
        dict(items=[], has_more=1, offset=""),
        dict(items=[None], has_more=0),
        dict(
            items=[{"id_str": "a", "modules": {"module_author": {"mid": 999, "pub_ts": 1}}}],
            has_more=0,
        ),
        dict(items=[row(100)], has_more=1, offset=""),
    ],
)
def test_incomplete_or_wrong_owner_never_proves_absence(page):
    transport, _ = wire({"": page})
    assert ShareHistory().check(transport, "123", "target", 50)[0] is S.UNKNOWN


def test_repeated_cursor_is_not_absence():
    transport, _ = wire(
        {
            "": dict(items=[row(100)], has_more=1, offset="1"),
            "1": dict(items=[row(99)], has_more=1, offset="1"),
        }
    )
    assert ShareHistory().check(transport, "123", "target", 1)[0] is S.UNKNOWN


def test_comment_continues_pagination_to_find_own_root():
    calls = []

    def replies(**kw):
        cursor = kw["cursor"]
        calls.append(cursor)
        return dict(
            data=dict(
                replies=[dict(rpid=cursor + 1, mid="123" if cursor == 4 else "99")],
                cursor=dict(all_count=900, next=cursor + 1),
            )
        )

    probe = BiliProber(
        None, SimpleNamespace(profile=lambda: SimpleNamespace(uid="123"), _reply_page=replies)
    )
    assert probe.did(ActionKind.COMMENT, dyn_id="1", payload={}) is S.UNKNOWN
    assert calls == [0, 1, 2]
    assert probe.did(ActionKind.COMMENT, dyn_id="1", payload={}) is S.PRESENT
    assert calls[-3:] == [0, 3, 4]


def test_cached_comment_pages_cannot_prove_absence():
    def replies(**kw):
        cursor = kw["cursor"]
        return dict(
            data=dict(
                replies=[dict(rpid=cursor + 1, mid="99")], cursor=dict(all_count=5, next=cursor + 1)
            )
        )

    probe = BiliProber(
        None, SimpleNamespace(profile=lambda: SimpleNamespace(uid="123"), _reply_page=replies)
    )
    assert probe.did(ActionKind.COMMENT, dyn_id="1", payload={}) is S.UNKNOWN
    assert probe.did(ActionKind.COMMENT, dyn_id="1", payload={}) is S.UNKNOWN


def test_feed_risk_propagates_instead_of_becoming_absence():
    transport = SimpleNamespace(fetch=lambda *a, **k: {"code": -412, "message": "risk"})
    with pytest.raises(RemoteError):
        ShareHistory().check(transport, "123", "target", 1)


def test_comment_transport_risk_is_not_swallowed():
    from tobili.adapters.bili.reader import DynamicReader

    def fetch(*a, **kw):
        raise RemoteError(Fault.RISK, "HTTP 412", code=412)

    reader = DynamicReader(SimpleNamespace(fetch=fetch))
    reader._signer = SimpleNamespace(sign=lambda query, **kw: query)
    with pytest.raises(RemoteError):
        reader._reply_page(
            rid="1", comment_type=17, cursor=0, page_size=20, referer="", resign=True, strict=True
        )


def test_known_share_in_one_account_does_not_apply_to_another():
    first, _ = wire({"": dict(items=[row(100, "target")], has_more=0)})
    second, _ = wire({"": dict(items=[], has_more=0)})
    assert ShareHistory().check(first, "123", "target", 1)[0] is S.PRESENT
    assert ShareHistory().check(second, "456", "target", 1)[0] is S.ABSENT


def test_comment_opaque_cursor_finds_own_comment_on_later_page():
    calls = []
    pages = {
        0: ("page-A", "99", "a"),
        "page-A": ("page-B", "99", "b"),
        "page-B": ("page-C", "99", "c"),
        "page-C": ("page-D", "123", "d"),
    }

    def replies(**kw):
        assert kw["mode"] == 2
        cur = kw["cursor"]
        calls.append(cur)
        n, mid, rpid = pages[cur]
        return {
            "data": {
                "replies": [{"rpid": rpid, "mid": mid}],
                "cursor": {
                    "all_count": 100,
                    "next": 2,
                    "is_end": False,
                    "pagination_reply": {"next_offset": n},
                },
            }
        }

    probe = BiliProber(
        None, SimpleNamespace(profile=lambda: SimpleNamespace(uid="123"), _reply_page=replies)
    )
    assert probe.did(ActionKind.COMMENT, dyn_id="1", payload={}) is S.UNKNOWN
    assert calls == [0, "page-A", "page-B"]
    assert probe.did(ActionKind.COMMENT, dyn_id="1", payload={}) is S.PRESENT
    assert calls[-2:] == [0, "page-C"]


def test_opaque_cursor_goes_in_signed_pagination_json():
    import json

    from tobili.adapters.bili.reader import DynamicReader

    queries = []
    reader = DynamicReader(
        SimpleNamespace(
            fetch=lambda *a, **kw: queries.append(kw["query"]) or {"code": 0, "data": {}}
        )
    )
    reader._signer = SimpleNamespace(sign=lambda q, **kw: q)
    reader._reply_page(
        rid="1", comment_type=17, cursor="A+/==", page_size=20, referer="", resign=True, mode=2
    )
    assert "next" not in queries[0]
    assert queries[0]["mode"] == 2
    assert json.loads(queries[0]["pagination_str"]) == {"offset": "A+/=="}


def test_partial_end_and_repeated_opaque_cursor_never_prove_absence():
    for end in (False, True):

        def replies(end=end, **kw):
            return {
                "data": {
                    "replies": [{"rpid": "1", "mid": "99"}],
                    "cursor": {
                        "all_count": 100,
                        "is_end": end,
                        "pagination_reply": {"next_offset": "same"},
                    },
                }
            }

        probe = BiliProber(
            None, SimpleNamespace(profile=lambda: SimpleNamespace(uid="123"), _reply_page=replies)
        )
        assert probe.did(ActionKind.COMMENT, dyn_id="1", payload={}) is S.UNKNOWN
