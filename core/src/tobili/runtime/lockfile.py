"""跨进程独占锁。

`lane.py` 的 `threading.Lock` 只管得住同一个进程。而现实用法是 `tobili serve` 常驻 +
`tobili daemon` 定时跑 —— 两个进程各自持有一份 `Lanes`，彼此看不见，于是会同时判定
"这条转发还没做过"，各写一次。B 站对重复转发没有幂等保护，一次就少一次评奖资格。

这里用操作系统的文件锁补上进程边界。选它而不是"pid 文件 + 心跳"的理由只有一个：
**进程怎么退出（包括被任务管理器强杀）锁都由内核释放**，不会留下需要人工判断
"这个文件里的 pid 还活着吗"的残留。

文件内容写的是 pid，纯粹为了冲突时能说清楚是谁占着，不参与判定。
**pid 从第 1 个字节开始写，第 0 字节只用来加锁** —— Windows 的锁会挡住别人读这段区间，
写进锁区间就等于"谁都读不到谁占着"，冲突时的措辞就退化成"有个进程"。
"""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

# 锁区间长度。锁第 0 字节即可：锁的粒度是"同一文件同一区间"，不必锁满。
_LOCKED = 1
_PID_AT = 1

if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        # Windows 的锁是"从当前文件位置起 n 字节"，所以每次都得先回到 0；
        # 锁在 EOF 之后的区间是允许的，不会因为文件刚好空着就失败。
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, _LOCKED)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        with suppress(OSError):
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, _LOCKED)

    # msvcrt.locking 只认二进制句柄，混进文本模式会把 \r\n 翻译和锁区域搅在一起。
    _FLAGS = os.O_RDWR | os.O_CREAT | os.O_BINARY

else:
    import fcntl

    _FLAGS = os.O_RDWR | os.O_CREAT

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        with suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)


class FileLock:
    """一个文件的独占锁。拿不到就立刻返回 False，不排队 —— 这里的语义是"有人在跑"。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> bool:
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, _FLAGS, 0o600)
        if not _try_lock(fd):
            os.close(fd)
            return False
        self._fd = fd
        self._stamp()
        return True

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            # 先抹掉 pid 再放手：留一个已经没人持有的 pid 只会误导下一次冲突的措辞。
            with suppress(OSError):
                os.ftruncate(fd, 0)
            _unlock(fd)
        finally:
            # 关掉 fd 本身也会释放锁，_unlock 只是让意图显式。
            os.close(fd)

    def holder(self) -> int | None:
        """文件里记的 pid。读到脏数据就当没有 —— 它只用于措辞，不用于判定。"""
        try:
            with self.path.open("rb") as fh:
                # 从锁区间之后读：第 0 字节被锁着，从那里读会被系统拒绝。
                fh.seek(_PID_AT)
                text = fh.read(16).decode("ascii", errors="ignore").strip()
            return int(text) if text else None
        except (OSError, ValueError):
            return None

    def _stamp(self) -> None:
        if self._fd is None:
            return
        payload = str(os.getpid()).encode("ascii")
        os.lseek(self._fd, _PID_AT, os.SEEK_SET)
        os.write(self._fd, payload)
        os.ftruncate(self._fd, _PID_AT + len(payload))

    def __enter__(self) -> FileLock:
        if not self.acquire():
            raise OSError(f"{self.path} 已被占用")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
