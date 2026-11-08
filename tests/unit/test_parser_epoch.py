"""时间戳解析。

这一条是被真实接口打出来的：动态列表接口把 `pub_ts` 返回成字符串，详情接口返回
整数。只认整数的时候，发布时间恒为空，于是两件事静默失效 —— 开奖核验永远等不到
观察期结束（结论永远是"未定"），转发探测永远翻不到时间边界（永远是"不知道"）。
两者都不报错，只是永远得出同一个答案。所以这里按两种类型都测一遍。
"""

from __future__ import annotations

from datetime import UTC, datetime

from tobili.adapters.bili.parser import author_of, epoch_of


def _item(pub_ts: object) -> dict:
    return {
        "id_str": "dyn-1",
        "type": "DYNAMIC_TYPE_WORD",
        "modules": {"module_author": {"mid": "9001", "name": "某人", "pub_ts": pub_ts}},
    }


def test_int_timestamp_is_read() -> None:
    assert epoch_of(1762300000) == 1762300000.0


def test_string_timestamp_is_read() -> None:
    """实测动态列表接口就是这种形状。"""
    assert epoch_of("1793769466") == 1793769466.0


def test_junk_is_not_a_timestamp() -> None:
    for value in (None, "", "  ", "abc", 0, -5, True, [], {}):
        assert epoch_of(value) is None


def test_author_time_parsed_from_string() -> None:
    uid, name, published = author_of(_item("1793769466"))
    assert uid == "9001"
    assert name == "某人"
    assert published == datetime.fromtimestamp(1793769466, tz=UTC)


def test_author_time_parsed_from_int() -> None:
    _uid, _name, published = author_of(_item(1793769466))
    assert published == datetime.fromtimestamp(1793769466, tz=UTC)


def test_missing_time_is_none_not_zero() -> None:
    """拿不到时间要如实给 None。用 0 顶上会让"观察期已过"凭空成立。"""
    assert author_of(_item(""))[2] is None
