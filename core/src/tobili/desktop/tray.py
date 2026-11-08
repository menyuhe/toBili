"""托盘图标。

pystray 在 Windows 上要求 `run()` 跑在主线程，所以顺序是固定的：
服务在后台线程起来，托盘占住主线程，退出菜单一按就停服务。

没有 pystray（或没有图形环境）时退回"只起服务 + 打印地址"，不让程序直接崩 ——
命令行和 CI 里也会走到这条路。
"""

from __future__ import annotations

import contextlib
import threading
from typing import Any

from .autostart import is_enabled, set_enabled
from .shell import _TITLE, Shell, open_ui, say


def _image(size: int = 64) -> Any:
    """Load the original brand asset shared by UI, executable and tray."""
    from PIL import Image

    from .shell import bundle_dir

    bundle = bundle_dir()
    path = (
        bundle / "icon.png"
        if bundle
        else __import__("pathlib").Path(__file__).resolve().parents[4] / "ui/public/icon.png"
    )
    with Image.open(path) as source:
        return source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)


def _build(shell: Shell) -> Any:
    import pystray

    def toggle_autostart(icon: Any, _item: Any) -> None:
        with contextlib.suppress(OSError):
            set_enabled(not is_enabled())
        icon.update_menu()

    return pystray.Icon(
        "tobili",
        _image(),
        f"{_TITLE} · {shell.url}",
        pystray.Menu(
            pystray.MenuItem("打开界面", lambda *_: open_ui(shell.url), default=True),
            pystray.MenuItem("开机自启", toggle_autostart, checked=lambda _item: is_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda icon, _item: icon.stop()),
        ),
    )


def run(shell: Shell, *, stop: threading.Event | None = None) -> None:
    """占住主线程直到用户退出。没有托盘就退化成"一直跑到被结束"。"""
    try:
        icon = _build(shell)
    except Exception as exc:
        say(f"（托盘不可用：{exc}）界面地址：{shell.url}")
        with contextlib.suppress(KeyboardInterrupt):
            (stop or threading.Event()).wait()
        return

    if stop is not None:
        # Ctrl+C 或者外部要求退出时，也要把托盘一起收掉，否则窗口关不掉。
        threading.Thread(target=lambda: (stop.wait(), icon.stop()), daemon=True).start()
    icon.run()
