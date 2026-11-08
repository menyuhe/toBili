"""解析器：转发动态的归一化。

这一条如果错了，整个关注流都是废的：抽奖号整页都是转发，正文一律是"转发动态"，
看起来像"扫了 40 条，一条活动都没有"。
"""

from __future__ import annotations

from tobili.adapters.bili.parser import is_repost, subject_of, to_detail, to_feed_item


def _item(dyn_id: str, text: str, *, uid: str = "3003", name: str = "转发的人") -> dict:
    return {
        "id_str": dyn_id,
        "type": "DYNAMIC_TYPE_WORD",
        "modules": {
            "module_author": {"mid": uid, "name": name, "pub_ts": 1762300000},
            "module_dynamic": {"desc": {"text": text}},
        },
    }


def _repost(dyn_id: str, orig: dict) -> dict:
    return {
        "id_str": dyn_id,
        "type": "DYNAMIC_TYPE_FORWARD",
        "modules": {
            "module_author": {"mid": "4004", "name": "转发者", "pub_ts": 1762300500},
            "module_dynamic": {"desc": {"text": "转发动态"}},
        },
        "orig": orig,
    }


def test_plain_dynamic_is_untouched() -> None:
    item = _item("dyn-1", "转发抽奖，送出大会员")
    assert is_repost(item) is False
    assert subject_of(item) is item

    parsed = to_feed_item(item)
    assert parsed is not None
    assert parsed.dyn_id == "dyn-1"
    assert parsed.up_uid == "3003"
    assert parsed.text == "转发抽奖，送出大会员"


def test_repost_points_at_original() -> None:
    original = _item(
        "dyn-orig", "转发抽奖！1月5日截止，送出 3 份大会员", uid="2002", name="抽奖官方"
    )
    item = _repost("dyn-repost", original)
    assert is_repost(item) is True

    parsed = to_feed_item(item)
    assert parsed is not None
    # 活动主体是原动态，不是"某人的转发动作"。
    assert parsed.dyn_id == "dyn-orig"
    assert parsed.up_uid == "2002"
    assert parsed.up_name == "抽奖官方"
    assert "转发抽奖" in parsed.text


def test_repost_without_orig_keeps_itself() -> None:
    """上游没给 orig（删了原动态、或者字段改名），不能凭空造一个空活动。"""
    item = {
        "id_str": "dyn-x",
        "type": "DYNAMIC_TYPE_FORWARD",
        "modules": {
            "module_author": {"mid": "4004", "name": "转发者", "pub_ts": 1762300500},
            "module_dynamic": {"desc": {"text": "转发动态"}},
        },
    }
    assert subject_of(item) is item
    parsed = to_feed_item(item)
    assert parsed is not None
    assert parsed.dyn_id == "dyn-x"


def test_detail_of_repost_targets_original() -> None:
    original = _item("dyn-orig", "转发抽奖，送出大会员", uid="2002", name="抽奖官方")
    detail = to_detail(_repost("dyn-repost", original))

    # 参与要转的是原动态，否则会转出一条"转发的转发"。
    assert detail.dyn_id == "dyn-orig"
    assert detail.rid == "dyn-orig"
    assert detail.up_uid == "2002"


def test_two_reposts_of_same_campaign_collapse_to_one() -> None:
    original = _item("dyn-orig", "转发抽奖，送出大会员")
    a = to_feed_item(_repost("dyn-a", original))
    b = to_feed_item(_repost("dyn-b", original))
    assert a is not None and b is not None
    assert a.dyn_id == b.dyn_id
