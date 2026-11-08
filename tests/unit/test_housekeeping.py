"""历史清理。窗口是 30 天事件 / 90 天已看记录。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tobili.adapters.sqlite import Database, Repositories
from tobili.usecases.housekeeping import EVENT_DAYS, INBOX_DAYS, Housekeeping


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _repos(tmp_path: Path) -> Repositories:
    db = Database(tmp_path / "t.db")
    db.migrate()
    return Repositories(db)


def test_events_older_than_the_window_go(tmp_path: Path) -> None:
    repos = _repos(tmp_path)
    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    repos.events.append(
        seq=1, topic="probe", payload="{}", at=now - timedelta(days=EVENT_DAYS + 10)
    )
    repos.events.append(seq=2, topic="probe", payload="{}", at=now - timedelta(days=1))

    swept = Housekeeping(events=repos.events, inbox=repos.inbox, clock=FixedClock(now)).run()

    assert swept.events == 1
    left = [row["seq"] for row in repos.events.recent(limit=10)]
    # 新的那条必须还在：清理只该删旧的，删错方向等于把"运行记录"清空。
    assert left == [2]


def test_events_inside_the_window_stay(tmp_path: Path) -> None:
    repos = _repos(tmp_path)
    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    repos.events.append(seq=1, topic="probe", payload="{}", at=now - timedelta(days=EVENT_DAYS - 1))

    swept = Housekeeping(events=repos.events, inbox=repos.inbox, clock=FixedClock(now)).run()

    assert swept.events == 0


def test_inbox_seen_has_a_longer_window(tmp_path: Path) -> None:
    repos = _repos(tmp_path)
    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    assert INBOX_DAYS > EVENT_DAYS

    with repos.db.write() as conn:
        conn.execute(
            "INSERT INTO account (id, bili_uid, created_at) VALUES (1, '1', '2026-01-01T00:00:00+00:00')"
        )
        conn.executemany(
            "INSERT INTO inbox_seen (account_id, item_key, seen_at) VALUES (1, ?, ?)",
            [
                ("old", (now - timedelta(days=INBOX_DAYS + 1)).isoformat()),
                ("fresh", (now - timedelta(days=1)).isoformat()),
            ],
        )

    swept = Housekeeping(events=repos.events, inbox=repos.inbox, clock=FixedClock(now)).run()

    assert swept.inbox == 1
    assert repos.inbox.seen(1) == {"fresh"}


def test_nothing_to_do_is_not_an_error(tmp_path: Path) -> None:
    repos = _repos(tmp_path)
    swept = Housekeeping(
        events=repos.events, inbox=repos.inbox, clock=FixedClock(datetime.now(UTC))
    ).run()
    assert swept.total == 0
