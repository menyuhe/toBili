"""北京时间。

库里存 UTC（`stamp()` 会 `astimezone(UTC)`），但**所有给人看的日期和时刻都是
北京时间**：B 站公告的开奖时间、"今天/明天"的筛选、文章里那句"开奖：11月11日
20:00"，说的都是同一件事。

少转这一步不会报错，只会让文章里的开奖时间整整早 8 小时 —— 发出去之后没人会
去核对，中奖的人按错的时间守着，是最难被发现的那种错。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")


def beijing(moment: datetime | None) -> datetime | None:
    """转成北京时间。

    naive 的当作 UTC —— 库里不该存 naive，但万一有，让它偏 8 小时总比按服务器的
    本地时区随机偏要好。
    """
    if moment is None:
        return None
    aware = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
    return aware.astimezone(BEIJING)


def beijing_day(moment: datetime | None) -> str:
    """北京时间的日期，`2026-11-11`。日期分桶、"今天/明天"筛选都按它算。"""
    local = beijing(moment)
    return local.date().isoformat() if local else ""
