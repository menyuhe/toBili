"""Regression coverage for retirement of the legacy batch participation action."""
import pytest
from web.actions import run_action
from web.activity_service import participate_step_budget
from web.schemas.jobs import ALLOWED_JOB_ACTIONS


def test_retired_action_cannot_execute():
    assert "participate_triple" not in ALLOWED_JOB_ACTIONS
    with pytest.raises(ValueError, match="未知操作"):
        run_action("participate_triple")


def test_participate_step_budget():
    assert participate_step_budget("预约抽奖") == 2
    assert participate_step_budget("互动抽奖") == 6
