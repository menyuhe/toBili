"""本机服务与单实例管理。界面由 desktop.window 或托盘入口打开。

账号与服务锁按数据目录隔离；已有实例通过 desktop.json 找到地址。
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from ..config import resolve
from ..runtime.lockfile import FileLock

DEFAULT_PORT = 8811
_TITLE = "toBili"
_START_TIMEOUT = 20.0


def bundle_dir() -> Path | None:
    """前端构建产物在哪。

    冻结后 PyInstaller 把 `--add-data` 解到 `sys._MEIPASS`；开发态按仓库布局往上找。
    找不到就返回 None —— 接口照样能用，只是界面打不开，日志里会说清楚。
    """
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        candidate = Path(frozen) / "ui" / "dist"
        return candidate if (candidate / "index.html").is_file() else None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "ui" / "dist"
        if (candidate / "index.html").is_file():
            return candidate
    return None


def _busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def free_port(preferred: int = DEFAULT_PORT) -> int:
    """优先用固定端口（地址稳定，用户收藏夹里那条链接不会失效），占了就换一个。"""
    if not _busy(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def say(text: str) -> None:
    """说一句话给用户看。

    `--windowed` 打包后 `sys.stdout` / `sys.stderr` 都是 None，直接 print 会抛
    AttributeError —— 那种"程序闪一下就没了、什么也没留下"的现象多半就是这么来的。
    """
    stream = sys.stderr or sys.__stderr__ or sys.stdout or sys.__stdout__
    if stream is not None:
        print(text, file=stream)


def open_ui(url: str) -> None:
    """用系统默认浏览器打开。失败不抛：地址还在日志和托盘提示里。"""
    try:
        webbrowser.open(url)
    except Exception:
        say(f"打不开浏览器，请手动访问：{url}")


class Shell:
    def __init__(self, *, home: str | None = None, port: int | None = None) -> None:
        self.paths = resolve(home)
        self.port = port or free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        self._server = None  # type: ignore[assignment]
        self._thread: threading.Thread | None = None
        self._lock = FileLock(self.paths.lanes / "desktop.lock")
        self._address = self.paths.home / "desktop.json"

    # ---- 起停 ----
    def start(self) -> str:
        import uvicorn

        from ..api.app import create_app

        app = create_app(home=str(self.paths.home), bundle=bundle_dir())
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="tobili-api", daemon=True)
        self._thread.start()

        deadline = time.monotonic() + _START_TIMEOUT
        while time.monotonic() < deadline:
            if self._server.started:
                self._remember()
                return self.url
            if not self._thread.is_alive():
                raise RuntimeError("服务没能启动，详情见 data/logs/desktop.log")
            time.sleep(0.05)
        raise RuntimeError("服务启动超时，详情见 data/logs/desktop.log")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._lock.release()
        with contextlib.suppress(OSError):
            self._address.unlink()

    # ---- 单实例 ----
    def claim(self) -> bool:
        return self._lock.acquire()

    def existing_url(self) -> str:
        """另一个实例记下的地址。读不到就退回本实例会用的端口。

        退到 `self.port` 而不是 `DEFAULT_PORT`：默认端口被别人占着时，
        `self.port` 已经换过一个，这时指回 8811 会把用户送到一个不存在的服务上。
        """
        try:
            data = json.loads(self._address.read_text(encoding="utf-8"))
            port = int(data.get("port") or self.port)
        except (OSError, ValueError, TypeError):
            port = self.port
        return f"http://127.0.0.1:{port}/"

    def _remember(self) -> None:
        payload = {"url": self.url, "port": self.port, "pid": os.getpid()}
        # 记不下来只影响"第二次双击能不能直接打开界面"，不影响服务本身。
        with contextlib.suppress(OSError):
            self._address.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
