"""Platform choices shared by the Windows and macOS desktop launchers."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def default_home(app_dir: Path, *, smoke: bool = False) -> Path:
    if smoke:
        return app_dir / "smoke-data"
    if sys.platform == "darwin":
        if os.environ.get("BINGGO_PORTABLE", "").strip().lower() in {"1", "true", "yes"}:
            if app_dir.name == "MacOS" and app_dir.parent.name == "Contents":
                return app_dir.parents[2] / "toBili-Lite-Preview-data"
            return app_dir / "toBili-Lite-Preview-data"
        return Path.home() / "Library" / "Application Support" / "toBili-Lite-Preview"
    return app_dir / "user-data"


def gui_backend() -> str | None:
    return {"win32": "edgechromium", "darwin": "cocoa"}.get(sys.platform)


def show_startup_error(title: str, log: Path) -> None:
    message = f"启动失败。\n错误详情：{log}"
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, message + "\n\n请确认已安装 Microsoft Edge WebView2 Runtime。", title, 0x10
        )
    elif sys.platform == "darwin":
        try:
            from AppKit import NSAlert, NSAlertStyleCritical
            alert = NSAlert.alloc().init()
            alert.setAlertStyle_(NSAlertStyleCritical)
            alert.setMessageText_(title)
            alert.setInformativeText_(message)
            alert.addButtonWithTitle_("好")
            alert.runModal()
        except Exception:
            # The original traceback remains available even if Cocoa cannot load.
            print(message, file=sys.stderr)
    else:
        print(message, file=sys.stderr)
