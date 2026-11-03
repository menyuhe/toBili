from __future__ import annotations

from unittest.mock import Mock, patch

from src.lottery_actions import ActionResult, favorite_supported, _opus_favorite_status, favorite_dynamic
from src.participation_log import participation_succeeded


def test_favorite_supported_when_module_stat_has_favorite() -> None:
    item = {
        "modules": {
            "module_stat": {
                "like": {"status": False},
                "favorite": {"status": False},
            }
        }
    }
    assert favorite_supported(item) is True


def test_favorite_not_supported_when_module_stat_missing_favorite() -> None:
    item = {
        "modules": {
            "module_stat": {
                "like": {"status": True},
                "forward": {"count": 1},
                "comment": {"count": 1},
            }
        }
    }
    assert favorite_supported(item) is False


def test_null_favorite_on_forward_dynamic_is_not_a_favorite_entry() -> None:
    item = {"type": "DYNAMIC_TYPE_FORWARD", "modules": {"module_stat": {
        "favorite": None, "coin": None, "forward": {"count": 1332}, "comment": {"count": 1002},
    }}}
    with patch("src.lottery_actions.fetch_opus_detail_item", return_value=None):
        assert favorite_supported(item, client=object(), dynamic_id="1245527961677856802") is False


def test_null_dynamic_favorite_can_still_use_valid_opus_entry() -> None:
    item = {"modules": {"module_stat": {"favorite": None, "forward": {}, "comment": {}}}}
    opus = {"modules": {"module_stat": {"favorite": {"status": False}}}}
    with patch("src.lottery_actions.fetch_opus_detail_item", return_value=opus):
        assert favorite_supported(item, client=object(), dynamic_id="123") is True


def test_favorite_verification_distinguishes_missing_from_false() -> None:
    client = Mock()
    for favorite, expected in [(None, None), ({}, None), ({"status": False}, False), ({"status": True}, True)]:
        client.get_json.return_value = {"code": 0, "data": {"item": {"modules": {"module_stat": {"favorite": favorite}}}}}
        assert _opus_favorite_status(client, dynamic_id="123", referer="test") is expected


def test_unconfirmed_favorite_does_not_show_success_message_zero_as_error() -> None:
    client = Mock()
    client.post_json.return_value = {"code": 0, "message": "0"}
    with patch("src.lottery_actions.is_favorited", return_value=False), patch("src.lottery_actions.cancellable_sleep"):
        result = favorite_dynamic(client, dynamic_id="123", csrf="test", referer="test")
    assert not result.ok
    assert "未确认收藏状态" in result.detail


def test_favorite_supported_when_module_stat_incomplete() -> None:
    item = {
        "modules": {
            "module_stat": {
                "like": {"status": True},
            }
        }
    }
    assert favorite_supported(item) is True


def test_favorite_supported_when_module_stat_missing() -> None:
    assert favorite_supported({"modules": {}}) is True


def test_favorite_supported_uses_opus_detail_when_dynamic_stat_lacks_favorite() -> None:
    dynamic_item = {
        "modules": {
            "module_stat": {
                "like": {"status": True},
                "forward": {"count": 1},
                "comment": {"count": 1},
            }
        }
    }
    opus_item = {
        "modules": {
            "module_stat": {
                "like": {"status": True},
                "forward": {"count": 1},
                "comment": {"count": 1},
                "favorite": {"status": False},
            }
        }
    }
    with patch("src.lottery_actions.fetch_opus_detail_item", return_value=opus_item):
        assert favorite_supported(dynamic_item, client=object(), dynamic_id="123") is True


def test_favorite_not_supported_when_opus_detail_also_lacks_favorite() -> None:
    item = {
        "modules": {
            "module_stat": {
                "like": {"status": True},
                "forward": {"count": 1},
                "comment": {"count": 1},
            }
        }
    }
    with patch("src.lottery_actions.fetch_opus_detail_item", return_value=item):
        assert favorite_supported(item, client=object(), dynamic_id="123") is False


def test_forward_lottery_succeeds_when_favorite_unavailable_skipped() -> None:
    actions = [
        ActionResult("like", True, ""),
        ActionResult("follow", True, ""),
        ActionResult("favorite", True, "无收藏入口，跳过"),
        ActionResult("repost", True, ""),
        ActionResult("comment", True, ""),
    ]
    assert participation_succeeded(actions, lottery_type="转发抽奖") is True


def test_interact_lottery_succeeds_when_favorite_unavailable_skipped() -> None:
    actions = [
        ActionResult("like", True, ""),
        ActionResult("follow", True, ""),
        ActionResult("favorite", True, "无收藏入口，跳过"),
        ActionResult("repost", True, ""),
        ActionResult("comment", False, "code=12078 关注UP主7天以上的人可发评论"),
    ]
    assert participation_succeeded(actions, lottery_type="互动抽奖") is True
