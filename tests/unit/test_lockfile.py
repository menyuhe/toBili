"""跨进程锁。这里要证明的是"两个进程真的互相看得见"，所以有一条用真子进程跑。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tobili.runtime.lane import BusyAccount, Lanes
from tobili.runtime.lockfile import FileLock

_CHILD = """
import os, sys
from tobili.runtime.lockfile import FileLock

lock = FileLock(sys.argv[1])
if not lock.acquire():
    print("busy", flush=True)
    raise SystemExit(3)
print("locked", os.getpid(), flush=True)
sys.stdin.readline()
lock.release()
print("released", flush=True)
"""


def test_lock_is_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "a.lock"
    first = FileLock(path)
    assert first.acquire()
    try:
        assert not FileLock(path).acquire()
    finally:
        first.release()
    # 释放之后别人能拿到 —— 这条防的是"锁没还回去"，比拿不到更隐蔽。
    assert FileLock(path).acquire()


def test_release_is_idempotent(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "a.lock")
    assert lock.acquire()
    lock.release()
    lock.release()


def test_holder_is_the_pid_that_wrote_it(tmp_path: Path) -> None:
    import os

    lock = FileLock(tmp_path / "a.lock")
    assert lock.acquire()
    try:
        assert lock.holder() == os.getpid()
    finally:
        lock.release()


def test_lanes_without_root_stays_in_process(tmp_path: Path) -> None:
    lanes = Lanes()
    with lanes.exclusive(1):
        assert not lanes.is_free(1)
    assert lanes.is_free(1)


def test_two_lanes_on_the_same_root_cannot_both_hold(tmp_path: Path) -> None:
    # serve 与 daemon 就是两份 Lanes 指向同一个 data 目录。
    serve = Lanes(tmp_path)
    daemon = Lanes(tmp_path)
    with serve.exclusive(7), pytest.raises(BusyAccount) as caught, daemon.exclusive(7):
        pass
    assert "pid" in str(caught.value)
    with daemon.exclusive(7):
        pass


def test_different_accounts_do_not_block_each_other(tmp_path: Path) -> None:
    lanes = Lanes(tmp_path)
    with lanes.exclusive(1), lanes.exclusive(2):
        assert not lanes.is_free(1)


def test_another_process_holding_the_lane_is_visible(tmp_path: Path) -> None:
    path = tmp_path / "account-3.lock"
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        line = child.stdout.readline().split()
        assert line[:1] == ["locked"]
        # 用子进程自报的 pid，不用 Popen.pid：venv 里的 python.exe 是个中继，
        # 两者不是一回事（这台机器上实测差了几百号）。
        child_pid = int(line[1])

        lanes = Lanes(tmp_path)
        with pytest.raises(BusyAccount) as caught, lanes.exclusive(3):
            pass
        # 报错里要带上对方 pid，否则用户只知道"忙"，不知道是谁在忙。
        assert str(child_pid) in str(caught.value)
        assert not lanes.is_free(3)
    finally:
        if child.stdin is not None:
            child.stdin.write("go\n")
            child.stdin.flush()
        child.wait(timeout=10)
        if child.poll() is None:
            child.kill()

    # 子进程退出之后锁必须自动松开 —— 这正是选文件锁而不是 pid 文件的理由。
    with Lanes(tmp_path).exclusive(3):
        pass
