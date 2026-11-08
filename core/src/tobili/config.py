"""路径与运行参数。

只有一个原则：**数据目录永远在可写位置，且不进仓库**。打包成单文件后，
`sys.executable` 所在目录才是"用户的目录"，临时解包目录不是。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP = "toBili"
_ENV_HOME = "TOBILI_HOME"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def default_home() -> Path:
    override = os.environ.get(_ENV_HOME, "").strip()
    if override:
        return Path(override).expanduser()
    if is_frozen():
        # Never silently adopt a parent folder's database (possibly another edition).
        # Developer data can still be selected explicitly with --home / TOBILI_HOME.
        return Path(sys.executable).resolve().parent / "data"
    return Path.cwd() / "data"


@dataclass(frozen=True, slots=True)
class Paths:
    home: Path
    vault: Path
    database: Path
    logs: Path
    lanes: Path
    backups: Path

    def ensure(self) -> Paths:
        for folder in (self.home, self.vault, self.logs, self.lanes, self.backups):
            folder.mkdir(parents=True, exist_ok=True)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def guide(self) -> Path:
        """一份可供人工检查的 JSON 导出（不含凭据）。"""
        return self.home / "account.json"


def resolve(home: Path | str | None = None) -> Paths:
    root = Path(home).expanduser() if home else default_home()
    return Paths(
        home=root,
        vault=root / "vault",
        database=root / "tobili.db",
        logs=root / "logs",
        # 跨进程锁：serve 和 daemon 靠这里的文件锁互相看见。
        lanes=root / "lanes",
        # 不可恢复操作（清理执行）前的库快照。
        backups=root / "backups",
    ).ensure()
