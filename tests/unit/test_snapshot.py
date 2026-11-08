"""执行前快照。

关键在于"WAL 里的东西也要在"。用 `shutil.copy` 拷主文件会得到一份看起来正常、
其实少了几条记录的库 —— 那种备份比没有更糟，因为它让人以为有据可查。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tobili.adapters.sqlite import Database
from tobili.adapters.sqlite.schema import SCHEMA_VERSION


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "tobili.db")
    db.migrate()
    return db


def test_snapshot_contains_rows_still_in_the_wal(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO account (bili_uid, created_at) VALUES ('100000001', '2026-11-06T00:00:00+00:00')"
        )

    shot = db.snapshot(tmp_path / "backups")

    assert shot.is_file()
    conn = sqlite3.connect(shot)
    try:
        assert conn.execute("SELECT count(*) FROM account").fetchone()[0] == 1
    finally:
        conn.close()


def test_snapshot_is_a_readable_database(tmp_path: Path) -> None:
    db = _db(tmp_path)
    shot = db.snapshot(tmp_path / "backups")
    conn = sqlite3.connect(shot)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        conn.close()


def test_snapshots_do_not_collide_within_a_second(tmp_path: Path) -> None:
    db = _db(tmp_path)
    folder = tmp_path / "backups"
    first = db.snapshot(folder)
    second = db.snapshot(folder)
    assert first != second
    assert first.is_file()
    assert second.is_file()


def test_old_snapshots_are_trimmed(tmp_path: Path) -> None:
    db = _db(tmp_path)
    folder = tmp_path / "backups"
    for _ in range(5):
        db.snapshot(folder, keep=2)
    left = list(folder.glob("tobili-*.db"))
    assert len(left) == 2


def test_trim_only_touches_its_own_files(tmp_path: Path) -> None:
    # data/backups 里可能有人自己放的东西，清旧快照不许把它们一起删了。
    db = _db(tmp_path)
    folder = tmp_path / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    mine = folder / "我的记录.db"
    mine.write_text("别删我", encoding="utf-8")

    for _ in range(4):
        db.snapshot(folder, keep=1)

    assert mine.is_file()
    assert len(list(folder.glob("tobili-*.db"))) == 1
