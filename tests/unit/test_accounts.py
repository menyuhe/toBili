"""账号档案落库。

等级这类字段不能只在登录那一次的内存里正确 —— 登录时读到的是真值，重启后从库里
再读却变成 0，这种"只在某个时刻对"的字段最难发现。
"""

from __future__ import annotations

import sqlite3

from tobili.adapters.sqlite import Database, Repositories
from tobili.ports.models import AccountProfile


def test_level_survives_roundtrip(repos: Repositories) -> None:
    account_id = repos.accounts.upsert(
        AccountProfile(uid="1001", nickname="玖仟", level=6), "test-session"
    )
    current = repos.accounts.current()
    assert current is not None
    row_id, profile = current
    assert row_id == account_id
    assert profile.level == 6
    assert profile.nickname == "玖仟"


def test_level_updates_on_relogin(repos: Repositories) -> None:
    repos.accounts.upsert(AccountProfile(uid="1001", nickname="玖仟", level=3), "ref")
    repos.accounts.upsert(AccountProfile(uid="1001", nickname="玖仟", level=6), "ref")
    current = repos.accounts.current()
    assert current is not None
    assert current[1].level == 6


def test_migrate_adds_missing_column(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """老库的 account 表没有 level 列，migrate 要补上。"""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE account (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            bili_uid       TEXT NOT NULL UNIQUE,
            nickname       TEXT NOT NULL DEFAULT '',
            avatar         TEXT NOT NULL DEFAULT '',
            credential_ref TEXT NOT NULL DEFAULT '',
            created_at     TEXT NOT NULL,
            disabled       INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.commit()
    conn.close()

    database = Database(path)
    database.migrate()
    columns = {row["name"] for row in database.connect().execute("PRAGMA table_info(account)")}
    database.close()

    assert "level" in columns
