"""SQLite 接入。

三件事：每线程一条连接、写操作串行、时间统一用 UTC ISO8601。
中间那条不是为了性能，是为了让 `BEGIN IMMEDIATE` 真的能挡住并发写 ——
默认的延迟事务在"读后写"之间会让两个线程都以为自己拿到了写权限。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path

from ...domain.errors import SchemaTooNew
from .schema import ADDED_COLUMNS, DDL, SCHEMA_VERSION


def stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def parse_stamp(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class Database:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_gate = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def connect(self) -> sqlite3.Connection:
        existing = getattr(self._local, "conn", None)
        if existing is not None:
            return existing
        conn = sqlite3.connect(
            self._path,
            timeout=20.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=NORMAL")
        self._local.conn = conn
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        yield self.connect()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._write_gate:
            conn = self.connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def migrate(self) -> int:
        conn = self.connect()
        with self._write_gate:
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            # 只进不退。旧版程序打开新版库时，DDL 是 IF NOT EXISTS、补列是"缺就补"，
            # 于是它不会报错，只会安静地读不到新表新列 —— 用户看到的是"数据没了"。
            if current > SCHEMA_VERSION:
                raise SchemaTooNew(current, SCHEMA_VERSION)
            conn.executescript(DDL)
            self._fill_columns(conn)
            with self.write():
                if int(conn.execute("PRAGMA user_version").fetchone()[0]) < 15:
                    from .shared_feeds import migrate

                    migrate(conn)
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        return SCHEMA_VERSION

    def _fill_columns(self, conn: sqlite3.Connection) -> None:
        """给老库补新列。幂等：列已存在就跳过。"""
        for table, column, decl in ADDED_COLUMNS:
            present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def version(self) -> int:
        row = self.connect().execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0

    def snapshot(self, folder: Path | str, *, keep: int = 20) -> Path:
        """把当前库整份拷一份到 `folder`，返回新文件路径。

        用 SQLite 自己的 backup API 而不是 `shutil.copy`：库是 WAL 模式，直接拷主文件
        会丢掉还在 -wal 里没落盘的部分，拿到的是一份"看起来正常、其实少了几条"的库 ——
        这种备份比没有备份更糟，因为它会让人以为有据可查。

        只保留最近 `keep` 份，且只删自己生成的文件（`tobili-*.db`）。
        """
        target_dir = Path(folder)
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp_now = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = target_dir / f"tobili-{stamp_now}.db"
        index = 1
        while target.exists():
            target = target_dir / f"tobili-{stamp_now}-{index}.db"
            index += 1

        dest = sqlite3.connect(target)
        try:
            self.connect().backup(dest)
        finally:
            dest.close()
        self._trim_snapshots(target_dir, keep)
        return target

    @staticmethod
    def _trim_snapshots(folder: Path, keep: int) -> None:
        shots = sorted(folder.glob("tobili-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in shots[max(1, keep) :]:
            with suppress(OSError):
                old.unlink()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
