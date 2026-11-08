"""库升级。

`ADDED_COLUMNS` 是"老库能不能升上来"的唯一保障，而它此前一条测试都没有 ——
本机那个库是从 v5 升上来的，碰巧对了，但没有任何东西保证下次也对。
所以这里造一个真实的旧库（缺列、缺表、带着数据），升一遍，逐项核对。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tobili.adapters.sqlite.db import Database
from tobili.adapters.sqlite.schema import ADDED_COLUMNS, SCHEMA_VERSION
from tobili.domain.errors import SchemaTooNew

# v5 时代的表结构：没有 account.level，没有 verdict.source/note，
# 没有 tidy_item.target/reason，没有 tidy_plan.note/finished_at，也没有 inbox_seen。
_OLD = """
CREATE TABLE account (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    bili_uid       TEXT    NOT NULL UNIQUE,
    nickname       TEXT    NOT NULL DEFAULT '',
    avatar         TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL,
    disabled       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE campaign (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dyn_id        TEXT    NOT NULL UNIQUE,
    up_uid        TEXT    NOT NULL,
    up_name       TEXT    NOT NULL DEFAULT '',
    title         TEXT    NOT NULL DEFAULT '',
    reward        TEXT    NOT NULL DEFAULT '',
    kind          TEXT    NOT NULL DEFAULT 'unknown',
    deadline_at   TEXT,
    confidence    REAL    NOT NULL DEFAULT 0,
    source_ref    TEXT    NOT NULL DEFAULT '',
    raw_ref       TEXT    NOT NULL DEFAULT '',
    first_seen_at TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
CREATE TABLE verdict (
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    account_id  INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    outcome     TEXT    NOT NULL,
    decided_at  TEXT    NOT NULL,
    evidence    TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (campaign_id, account_id)
);
CREATE TABLE tidy_plan (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    state        TEXT    NOT NULL DEFAULT 'draft',
    created_at   TEXT    NOT NULL,
    previewed_at TEXT,
    confirmed_at TEXT
);
CREATE TABLE tidy_item (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id     INTEGER NOT NULL REFERENCES tidy_plan(id) ON DELETE CASCADE,
    campaign_id INTEGER NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    action      TEXT    NOT NULL,
    snapshot    TEXT    NOT NULL DEFAULT '',
    state       TEXT    NOT NULL DEFAULT 'pending',
    error_class TEXT    NOT NULL DEFAULT '',
    executed_at TEXT
);
PRAGMA user_version=5;
"""


def _old_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_OLD)
    conn.execute(
        "INSERT INTO account (bili_uid, nickname, created_at) VALUES ('100000001', '老号', '2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO campaign (dyn_id, up_uid, title, first_seen_at, updated_at) "
        "VALUES ('d1', '1', '抽奖', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO verdict (campaign_id, account_id, outcome, decided_at) VALUES (1, 1, 'won', '2026-01-02T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO tidy_plan (account_id, state, created_at) VALUES (1, 'done', '2026-01-03T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO tidy_item (plan_id, campaign_id, action, state) VALUES (1, 1, 'repost', 'done')"
    )
    conn.commit()
    conn.close()


def _columns(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_old_database_gains_every_added_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _old_database(path)

    assert "level" not in _columns(path, "account")

    assert Database(path).migrate() == SCHEMA_VERSION

    for table, column, _decl in ADDED_COLUMNS:
        assert column in _columns(path, table), f"{table}.{column} 没补上"


def test_old_database_gains_new_tables(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _old_database(path)
    Database(path).migrate()

    conn = sqlite3.connect(path)
    try:
        names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()

    assert "inbox_seen" in names
    assert "pool_item" in names
    assert version == SCHEMA_VERSION


def test_old_rows_survive_and_get_sane_defaults(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _old_database(path)
    Database(path).migrate()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute("SELECT count(*) FROM account").fetchone()[0] == 1
        verdict = conn.execute("SELECT outcome, source FROM verdict").fetchone()
        # 老结论一律算自动核验：`source` 的默认值必须让"人工改判"这条规则从升级那一刻
        # 就成立，否则升级后第一轮扫描会把用户手改的结论盖掉。
        assert verdict["outcome"] == "won"
        assert verdict["source"] == "auto"
        item = conn.execute("SELECT target FROM tidy_item").fetchone()
        assert item["target"] == ""
    finally:
        conn.close()


def test_migrate_is_repeatable(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _old_database(path)
    Database(path).migrate()
    # 每次都新建实例，模拟反复启动 —— 补列必须幂等。
    for _ in range(3):
        assert Database(path).migrate() == SCHEMA_VERSION


def test_newer_database_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    Database(path).migrate()

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=99")
    conn.commit()
    conn.close()

    with pytest.raises(SchemaTooNew) as caught:
        Database(path).migrate()
    assert caught.value.found == 99
    assert caught.value.expected == SCHEMA_VERSION
    # 报错要说人话：用户看到的第一句应当是"库比程序新"，不是"sqlite 出错"。
    assert "更新的版本" in str(caught.value)


def test_newer_database_is_left_untouched(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    Database(path).migrate()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=99")
    conn.commit()
    conn.close()

    with pytest.raises(SchemaTooNew):
        Database(path).migrate()

    conn = sqlite3.connect(path)
    try:
        # 拒绝启动时绝不能顺手把版本号改回去 —— 那等于把"更旧的程序"伪装成正常状态。
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 99
    finally:
        conn.close()


def test_cli_explains_a_newer_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TOBILI_HOME", str(tmp_path))
    Database(tmp_path / "tobili.db").migrate()
    conn = sqlite3.connect(tmp_path / "tobili.db")
    conn.execute("PRAGMA user_version=99")
    conn.commit()
    conn.close()

    from tobili.cli import main

    assert main(["whoami"]) == 4
    out = capsys.readouterr().out
    assert "更新的版本" in out
    assert "Traceback" not in out
