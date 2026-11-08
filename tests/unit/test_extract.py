"""文本提取。抽奖文案千奇百怪，这里只保证"不瞎猜"。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tobili.domain.campaign import CampaignKind
from tobili.domain.extract import (
    confidence_of,
    deadline_from,
    kind_from,
    looks_like_campaign,
    reward_from,
)

NOW = datetime(2026, 11, 4, 20, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("下周五晚上8点开奖", (2026, 11, 13, 20, 0)),
        ("本周五下午三点半开奖", (2026, 11, 6, 15, 30)),
        ("下下星期一 18:30 截止", (2026, 11, 16, 18, 30)),
        ("明晚八点开奖", (2026, 11, 9, 20, 0)),
        ("三天后中午12点开奖", (2026, 11, 11, 12, 0)),
        ("本周日 24:00 截止", (2026, 11, 9, 0, 0)),
        ("开奖时间：下周五20:00，参与截止：明天18:00", (2026, 11, 9, 18, 0)),
    ],
)
def test_relative_deadlines_resolve_against_publication_calendar(text, expected):
    published = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    assert deadline_from(text, now=published) == datetime(*expected, tzinfo=UTC)


def test_relative_date_crosses_year_and_ambiguous_week_is_not_today():
    assert deadline_from("下周一晚上8点开奖", now=datetime(2026, 12, 31, tzinfo=UTC)) == datetime(
        2027, 1, 4, 20, tzinfo=UTC
    )
    assert deadline_from("本周开奖", now=NOW) is None
    assert deadline_from("下周左右开奖", now=NOW) is None
    assert deadline_from("明天开奖", now=NOW, allow_relative=False) is None
    assert deadline_from("明天25:00开奖", now=NOW) is None


def test_kind_detects_share() -> None:
    assert kind_from("关注并转发本条动态，抽 3 人送大会员") is CampaignKind.SHARE


def test_kind_detects_comment() -> None:
    assert kind_from("评论区留言，抽一位送周边") is CampaignKind.COMMENT


def test_kind_detects_reserve() -> None:
    assert kind_from("点击预约，开售提醒不迷路") is CampaignKind.RESERVE


def test_kind_unknown_when_no_signal() -> None:
    assert kind_from("今天天气不错") is CampaignKind.UNKNOWN


def test_deadline_with_year() -> None:
    moment = deadline_from("2026年12月31日 23:59 截止", now=NOW)
    assert moment == datetime(2026, 12, 31, 23, 59, tzinfo=UTC)


def test_deadline_without_year_rolls_forward() -> None:
    """当前 11 月，说"1月5日"必然是明年。"""
    moment = deadline_from("1月5日 23:59 截止", now=NOW)
    assert moment is not None and moment.year == 2027


def test_deadline_ignores_dates_without_hint() -> None:
    """正文里出现日期但没有截止类提示词时，不该被当成截止时间。"""
    assert deadline_from("我在 2026年10月1日 去了漫展", now=NOW) is None


def test_deadline_relative_word() -> None:
    moment = deadline_from("明天 20:00 开奖", now=NOW)
    assert moment is not None and moment.day == 5


def test_deadline_prefers_explicit_label_over_previous_issue_and_not_drawn() -> None:
    text = "11月份第2轮抽奖，10月4期、11月1期还没开奖，欢迎同期参与！\n开奖日期 2026-11-20 20:00"
    assert deadline_from(text, now=NOW) == datetime(2026, 11, 20, 20, 0, tzinfo=UTC)
    assert deadline_from("10月4期、11月1期还没开奖", now=NOW) is None
    assert deadline_from("11月7日上新，11月20日 20:00 开奖", now=NOW) == datetime(
        2026, 11, 20, 20, 0, tzinfo=UTC
    )
    assert deadline_from("活动时间：即日起-9月22日\n开奖时间：2026年9月23日", now=NOW) == datetime(
        2026, 9, 23, 23, 59, tzinfo=UTC
    )


def test_deadline_chinese_clock_and_invisible_spacing() -> None:
    assert deadline_from("开奖时间：2026年11月\u200b18日晚上8点", now=NOW) == datetime(
        2026, 11, 18, 20, 0, tzinfo=UTC
    )
    assert deadline_from("开奖时间：2026.12.1（晚上19:30）", now=NOW) == datetime(
        2026, 12, 1, 19, 30, tzinfo=UTC
    )
    assert deadline_from("2026-11-30 24:00 截止", now=NOW) == datetime(
        2026, 12, 1, 0, 0, tzinfo=UTC
    )


def test_confidence_rewards_more_evidence() -> None:
    text = "转发抽奖：关注并转发，1月5日 23:59 截止，送出 3 份大会员"
    deadline = deadline_from(text, now=NOW)
    assert confidence_of(text, CampaignKind.SHARE, deadline) >= 0.85
    assert confidence_of(text, CampaignKind.UNKNOWN, deadline) == 0.0


def test_confidence_never_full_marks() -> None:
    text = "转发抽奖 关注 转发 点赞 @好友 送出 礼物 1月5日 23:59 截止"
    deadline = deadline_from(text, now=NOW)
    assert confidence_of(text, CampaignKind.SHARE, deadline) <= 0.95


def test_reward_extraction() -> None:
    assert "大会员" in reward_from("送出 大会员年卡")


def test_looks_like_campaign_filters_noise() -> None:
    assert looks_like_campaign("转发抽奖，送 3 份周边") is True
    assert looks_like_campaign("早安") is False
    assert looks_like_campaign("抽") is False
