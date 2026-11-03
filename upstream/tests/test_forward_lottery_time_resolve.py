from __future__ import annotations

from datetime import datetime

import pytest

from src.lottery_enricher import _lottery_time_from_llm_parse
from src.lottery_time import BEIJING_TZ, LOTTERY_TIME_DISPLAY_FMT, SIX_MONTHS_SECONDS


def test_lottery_time_from_llm_parse_uses_display_string() -> None:
    display = "2026-07-08 00:00"
    unix, conditions = _lottery_time_from_llm_parse({"lottery_time": display})
    expected = int(
        datetime.strptime(display, LOTTERY_TIME_DISPLAY_FMT)
        .replace(tzinfo=BEIJING_TZ)
        .timestamp()
    )
    assert unix == expected
    assert conditions["lottery_time_text"] == display
    assert "lottery_time_inferred" not in conditions


def test_lottery_time_from_llm_parse_rejects_chinese_text() -> None:
    unix, conditions = _lottery_time_from_llm_parse({"lottery_time": "2026年7月8日"})
    assert unix is None
    assert not conditions.get("lottery_time_inferred")


def test_lottery_time_from_llm_parse_unknown_when_null() -> None:
    unix, conditions = _lottery_time_from_llm_parse({"lottery_time": None})
    assert unix is None
    assert conditions == {}


@pytest.mark.parametrize("deadline", [None, "", "开奖时间待定", "2026-10-20 12:30", "2026-11-31 20:00", "2026-02-29 20:00"])
def test_source_pipeline_persists_forward_lotteries_with_unknown_deadline(
    monkeypatch, isolated_home, deadline,
) -> None:
    from src.pipeline.classify_step import ClassifyOutcome
    from src.pipeline.refresh_all_pipeline import run_new_links_pipeline

    ids = ["1224962472871460885", "1224962472871460886"]
    saved = []
    monkeypatch.setattr("src.bilibili_client.BilibiliClient._warmup", lambda self: None)
    detail = {"modules": {"module_stat": {"forward": {"count": 3}}}}
    monkeypatch.setattr(
        "src.pipeline.refresh_all_pipeline.classify_new_link",
        lambda _client, did: ClassifyOutcome(
            did, "转发抽奖", False,
            classify_content="转发这条动态参加抽奖，送出桌面礼盒一份，请关注开奖通知。",
            detail_item=detail,
        ),
    )
    monkeypatch.setattr(
        "src.lottery_enricher.parse_forward_content",
        lambda did, _content: {
            "is_lottery": True, "prize_description": "桌面礼盒", "winner_count": 1,
            "lottery_time": deadline if did == ids[0] else "2026-10-21 12:30",
        },
    )
    monkeypatch.setattr(
        "src.pipeline.refresh_all_pipeline.append_activities",
        lambda rows: saved.extend(rows) or len(rows),
    )
    result = run_new_links_pipeline(
        [f"https://www.bilibili.com/opus/{did}" for did in ids], workers=2,
    )
    assert result.ok and result.persisted_count == 2
    rows = {row["dynamic_id"]: row for row in saved}
    expected, _ = _lottery_time_from_llm_parse({"lottery_time": deadline})
    assert rows[ids[0]]["lottery_time"] == expected
    assert rows[ids[1]]["lottery_time"] is not None
    assert rows[ids[0]]["prizes"][0]["description"] == "桌面礼盒"


@pytest.mark.parametrize("display", [
    "2026-11-31 20:00", "2026-02-29 20:00", "2026-04-31 12:00",
    "2026-11-00 20:00", "2026-13-01 20:00", "2026-11-01 24:00",
    "2026-11-01 20:60", "0000-01-01 00:00",
])
def test_invalid_calendar_date_stays_unknown(display):
    from src.forward_parser import _coerce_lottery_time
    from src.lottery_time import is_standard_lottery_time_display, lottery_time_text

    assert not is_standard_lottery_time_display(display)
    assert _coerce_lottery_time(display) is None
    # Cached/legacy parser results must also be safe at enrichment time.
    assert _lottery_time_from_llm_parse({"lottery_time": display}) == (None, {})
    assert lottery_time_text({"conditions": {"lottery_time_text": display}}) == ""


@pytest.mark.parametrize("display", ["2026-10-31 23:59", "2026-11-01 00:00", "2028-02-29 20:00"])
def test_valid_calendar_boundaries_preserved(display):
    from src.forward_parser import _coerce_lottery_time
    assert _coerce_lottery_time(display) == display
    stamp, conditions = _lottery_time_from_llm_parse({"lottery_time": display})
    assert datetime.fromtimestamp(stamp, BEIJING_TZ).strftime(LOTTERY_TIME_DISPLAY_FMT) == display
    assert conditions == {"lottery_time_text": display}
