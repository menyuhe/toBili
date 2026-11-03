from unittest.mock import Mock, patch

from src.lottery_actions import DynamicContext, execute_full_participation
from web.activity_service import participate_step_budget


def test_skipped_actions_do_not_sleep_and_lookup_is_visible():
    context = DynamicContext("123", 456, "url", "123", 17, True, True, True, True, True, True)
    events = []
    with patch("src.lottery_actions.build_dynamic_context", return_value=context), patch(
        "src.lottery_actions.require_login", return_value=("csrf", 1)
    ), patch("src.maintenance.begin_link", return_value=None), patch(
        "src.maintenance.record_repost", side_effect=lambda *args: events.append("lookup")
    ), patch("src.lottery_actions.cancellable_sleep") as sleep:
        actions, _ = execute_full_participation(Mock(), dynamic_id="123", action_text="test",
            on_step=lambda step, total, message, name: events.append((step, total, name)))
    assert events == [(1, 6, "like"), (2, 6, "follow"), (3, 6, "favorite"),
                      (4, 6, "repost"), (5, 6, "repost_lookup"), "lookup", (6, 6, "comment")]
    sleep.assert_not_called()
    assert len(actions) == 5 and all(a.ok for a in actions)
    assert participate_step_budget("互动抽奖") == 6
    assert participate_step_budget("预约抽奖") == 2
