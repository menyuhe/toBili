"""每账号一条串行道。

旧式写法用一把全局锁把"抢票"和"抽奖"串起来，问题在于锁是进程内的、跨账号共享的，
而且只能靠人记住"这两个操作要加同一把锁"。这里换成结构性的约束：**账号即通道**。
同一账号的写操作排在同一条道上，不同账号互不影响，天然不需要记得加锁。

**这条道跨进程也成立**，只要给它一个目录：`Lanes(paths.lanes)` 会在 `exclusive()`
里额外抓一把文件锁（见 `lockfile.py`）。`serve` 和 `daemon` 同时开的时候，谁先抓到
谁跑，另一个立刻拿到 `BusyAccount` 而不是排进队列 —— 因为这里要防的是"重复写"，
不是"写慢了"。不传目录则退化成纯进程内锁，测试和单进程用法都省事。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .lockfile import FileLock


class Lanes:
    def __init__(self, root: Path | str | None = None) -> None:
        self._guard = threading.Lock()
        self._gates: dict[int, threading.Lock] = {}
        self._root = Path(root) if root is not None else None

    def _gate(self, account_id: int) -> threading.Lock:
        with self._guard:
            gate = self._gates.get(account_id)
            if gate is None:
                gate = threading.Lock()
                self._gates[account_id] = gate
            return gate

    def _path(self, account_id: int) -> Path:
        assert self._root is not None
        # 文件名不用账号名或 uid：那个含用户数据，而锁文件可能被随手拷进 issue。
        return self._root / f"account-{account_id}.lock"

    @contextmanager
    def exclusive(self, account_id: int) -> Iterator[None]:
        gate = self._gate(account_id)
        if not gate.acquire(blocking=False):
            raise BusyAccount(f"账号 {account_id} 正在执行其他任务")
        lock: FileLock | None = None
        try:
            if self._root is not None:
                lock = FileLock(self._path(account_id))
                if not lock.acquire():
                    raise BusyAccount(self._busy_by_other(account_id, lock))
            yield
        finally:
            if lock is not None:
                lock.release()
            gate.release()

    def is_free(self, account_id: int) -> bool:
        gate = self._gate(account_id)
        if not gate.acquire(blocking=False):
            return False
        try:
            if self._root is None:
                return True
            probe = FileLock(self._path(account_id))
            if not probe.acquire():
                return False
            probe.release()
            return True
        finally:
            gate.release()

    def _busy_by_other(self, account_id: int, lock: FileLock) -> str:
        pid = lock.holder()
        who = f"另一个进程（pid {pid}）" if pid else "另一个进程"
        # 说清"谁在跑"很重要：只报"忙"会让人以为是自己的界面卡住了，然后去点第二次。
        return f"账号 {account_id} 正被{who}占用，请等它跑完"


class BusyAccount(RuntimeError):
    """同一账号已有任务在跑。调用方应当提示"请稍候"，而不是排队等待。"""
