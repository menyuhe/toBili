from web.actions import (
    DS_HANDLERS,
    REFRESH_ALL_TOTAL,
    _format_subprogress_message,
    _pipeline_substep_index,
    _make_refresh_all_pipeline_progress,
)


def test_format_subprogress_message_strips_duplicate_suffix() -> None:
    assert _format_subprogress_message("分类进度 (30/236)", 30, 236) == "分类进度 (30/236)"
    assert _format_subprogress_message("分类进度 (30/236) (30/236)", 30, 236) == "分类进度 (30/236)"


def test_format_subprogress_message_appends_once() -> None:
    assert _format_subprogress_message("分类进度", 30, 236) == "分类进度 (30/236)"


def test_pipeline_substep_index_from_message() -> None:
    assert _pipeline_substep_index("分类进度 (30/236)") == 1
    assert _pipeline_substep_index("正在分类 236 条新链接…") == 1
    assert _pipeline_substep_index("详情进度 (3/10)") == 2
    assert _pipeline_substep_index("正在拉取 10 条活动详情…") == 2
    assert _pipeline_substep_index("正在写入活动库…") == 3


def test_refresh_all_total_matches_handlers() -> None:
    assert REFRESH_ALL_TOTAL == len(DS_HANDLERS) + 3


def test_single_source_pipeline_keeps_four_step_budget() -> None:
    events = []
    progress = _make_refresh_all_pipeline_progress(lambda **event: events.append(event), ds_count=1)
    progress(8, 15, "分类进度")
    progress(2, 10, "详情进度")
    progress(1, 1, "正在写入活动库…")
    assert [(event["step"], event["total"]) for event in events] == [(2, 4), (3, 4), (4, 4)]
