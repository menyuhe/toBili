"""内嵌窗口。

用 pywebview（Windows 上就是 WebView2），不自己拉一份 Chromium 内核：
程序里跑的是本机 127.0.0.1 上的界面，用系统自带的渲染器就够了，而且它跟着
系统一起打补丁。代价是 Windows 上要装过 WebView2 运行时 —— Win10 近几年的
版本和 Win11 都自带，缺了会在这里明确报出来，而不是弹一个白窗口。

窗口和浏览器模式的差别不只是"好不好看"：

- 关掉窗口 = 退出程序。浏览器模式下关标签页，服务还在后台跑，人以为关了其实没关。
- 没有地址栏，切不到别的站点，也就不会有人在窗口里点进钓鱼页。
- 单实例直接激活已有窗口，而不是再开一个标签页去改同一份数据。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .shell import Shell, say

_TITLE = "toBili"
_WIDTH = 1380
_HEIGHT = 920


def available() -> bool:
    """能不能起窗口。缺 WebView2 运行时或没装 pywebview 都算不能。"""
    try:
        import webview  # noqa: F401
    except Exception:
        return False
    return True


def run(
    url: str, *, title: str | None = None, shell: Shell | None = None, home: Path | None = None
) -> int:
    """起一个窗口并阻塞到它被关掉。

    窗口关掉之后要停掉本进程里的服务；如果这个进程只是"第二份双击"，
    服务不在自己手里（`shell is None`），那就只关窗口，不去动别人的进程。
    """
    try:
        import webview
    except Exception as exc:  # pragma: no cover - 依赖缺失时才会走到
        say(f"内嵌窗口不可用（{exc}），改用系统浏览器")
        from .shell import open_ui

        open_ui(url)
        return 1

    from ..config import resolve
    from .shell import bundle_dir

    private = (Path(__file__).resolve().parents[1] / "private_pack.py").is_file()
    # Frozen Python modules may not have a physical .py file.
    if getattr(sys, "frozen", False):
        from importlib.util import find_spec

        private = find_spec("tobili.private_pack") is not None
    title = title or ("toBili Pro" if private else _TITLE)
    data = shell.paths.home if shell else resolve(home).home
    profile = data / "webview"
    profile.mkdir(parents=True, exist_ok=True)
    bundle = bundle_dir()
    icon = next(iter(bundle.glob("*.ico")), None) if bundle else None
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "toBili.Pro.Desktop" if private else "toBili.Desktop"
        )
    say(f"窗口地址：{url}")
    try:
        webview.create_window(
            title,
            url,
            width=_WIDTH,
            height=_HEIGHT,
            min_size=(1040, 700),
            # 让用户能选中正文里的字：这一页有很多"复制文案"的场景。
            text_select=True,
        )
        webview.start(
            private_mode=False,
            storage_path=str(profile.resolve()),
            icon=str(icon) if icon else None,
        )
    except Exception as exc:
        logging.exception("窗口启动失败")
        # 最可能是没装 WebView2。这条信息必须说清楚，否则用户只看到"白窗口"。
        if sys.platform == "win32":
            say(
                f"窗口打不开：{exc}\n"
                "多半是缺少 WebView2 运行时。装一次即可：\n"
                "https://developer.microsoft.com/microsoft-edge/webview2/"
            )
        raise
    finally:
        if shell is not None:
            shell.stop()
    return 0
