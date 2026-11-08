"""开机自启。

用注册表 `HKCU\\...\\Run` 而不是往"启动"文件夹里塞快捷方式：一是不用生成 .lnk
（那要 COM 或者额外的库），二是用户能在任务管理器的"启动"页里看到并关掉它 ——
一个自己偷偷启动的程序，用户找不到开关，这是比"不能自启"更大的问题。

只写当前用户，不碰 HKLM：这个程序不需要管理员权限，也不该要。
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_KEY = "toBili"


def _command(exe: Path | None = None) -> str:
    """开机时该执行的命令行。

    冻结后 `sys.executable` 就是 exe 本身；开发态要用 `-m` 拉起来，
    否则注册表里会记下一个"python.exe"却不知道跑什么。
    """
    if getattr(sys, "frozen", False) or exe is not None:
        target = exe or Path(sys.executable)
        return f'"{target}" --tray'
    return f'"{sys.executable}" -m tobili.desktop --tray'


def is_enabled(name: str | None = None) -> bool:
    from ..config import edition_name

    name = name or edition_name()
    if sys.platform == "darwin":
        from .mac_autostart import enabled

        return enabled(name)
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN) as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return False
    return bool(value)


def set_enabled(on: bool, *, name: str | None = None, exe: Path | None = None) -> None:
    """开或关。`name` 可换，方便测试用一次性键名，不去动用户真正的启动项。"""
    from ..config import edition_name

    name = name or edition_name()
    if sys.platform == "darwin":
        from .mac_autostart import configure

        return configure(on, name, exe)
    if sys.platform != "win32":
        raise OSError("开机自启只在 Windows 上实现")
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, _command(exe))
        else:
            # 本来就没开。关一个已经关掉的东西不该报错。
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, name)
