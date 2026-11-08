"""清理计划的状态机与删除前的归属校验。

状态机要回答的唯一问题是："现在能不能动手"。答案是只有 confirmed / running 能，
而 previewed 永远不能 —— 看过不等于同意。

归属校验要回答的是："要删的这条，真的是我转发的这条活动吗"。这两个问题都不能靠
调用方的自觉，所以都写成了纯函数。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tobili.domain.tidy import (
    IllegalTidy,
    PlanState,
    ShareRef,
    TidyAction,
    advance,
    executable,
    is_closed,
    may_delete_share,
)

MOMENT = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def test_draft_must_be_previewed_before_confirm() -> None:
    with pytest.raises(IllegalTidy):
        advance(PlanState.DRAFT, PlanState.CONFIRMED)


def test_previewed_can_be_confirmed() -> None:
    assert advance(PlanState.PREVIEWED, PlanState.CONFIRMED) is PlanState.CONFIRMED


def test_confirmed_can_run() -> None:
    assert advance(PlanState.CONFIRMED, PlanState.RUNNING) is PlanState.RUNNING


def test_finished_plans_are_frozen() -> None:
    with pytest.raises(IllegalTidy):
        advance(PlanState.DONE, PlanState.RUNNING)
    with pytest.raises(IllegalTidy):
        advance(PlanState.ABORTED, PlanState.CONFIRMED)


def test_only_confirmed_and_running_are_executable() -> None:
    assert executable(PlanState.CONFIRMED) is True
    assert executable(PlanState.RUNNING) is True
    for state in (
        PlanState.DRAFT,
        PlanState.PREVIEWED,
        PlanState.DONE,
        PlanState.ABORTED,
    ):
        assert executable(state) is False


def test_closed_states() -> None:
    assert is_closed(PlanState.DONE) is True
    assert is_closed(PlanState.ABORTED) is True
    assert is_closed(PlanState.RUNNING) is False


# ---- 归属校验 ----
def _ref(**overrides: object) -> ShareRef:
    base = {
        "dyn_id": "mine-1",
        "author_uid": "1001",
        "orig_dyn_id": "activity-1",
        "published_at": MOMENT,
    }
    base.update(overrides)
    return ShareRef(**base)  # type: ignore[arg-type]


def test_own_share_of_the_target_is_deletable() -> None:
    verdict = may_delete_share(_ref(), me="1001", campaign_dyn_id="activity-1")
    assert verdict.allowed is True
    assert verdict.action is TidyAction.DROP_SHARE


def test_missing_share_is_refused() -> None:
    verdict = may_delete_share(None, me="1001", campaign_dyn_id="activity-1")
    assert verdict.allowed is False
    assert "没找到" in verdict.reason


def test_someone_elses_dynamic_is_refused() -> None:
    """别人发的动态绝不碰。这一条是"别删错东西"的最后一道闸。"""
    verdict = may_delete_share(_ref(author_uid="2002"), me="1001", campaign_dyn_id="activity-1")
    assert verdict.allowed is False
    assert "不是本人" in verdict.reason


def test_share_of_another_activity_is_refused() -> None:
    verdict = may_delete_share(
        _ref(orig_dyn_id="activity-2"), me="1001", campaign_dyn_id="activity-1"
    )
    assert verdict.allowed is False
    assert "不是目标活动" in verdict.reason


def test_unknown_identity_is_refused() -> None:
    verdict = may_delete_share(_ref(), me="", campaign_dyn_id="activity-1")
    assert verdict.allowed is False


def test_empty_campaign_id_is_refused() -> None:
    verdict = may_delete_share(_ref(), me="1001", campaign_dyn_id="")
    assert verdict.allowed is False
