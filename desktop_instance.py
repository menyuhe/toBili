"""Windows single instance and activation, independent of executable name/path."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import threading
import time


class DesktopInstance:
    def __init__(self, name: str = "Local\\toBiliLitePreviewDesktop"):
        self.name = name
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for method, args, result in (
            ("CreateMutexW", [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ("CreateEventW", [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ("OpenEventW", [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ("SetEvent", [wintypes.HANDLE], wintypes.BOOL),
            ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(self.kernel, method)
            function.argtypes, function.restype = args, result
        self.mutex = None
        self.event = None
        self.stop = threading.Event()
        self.thread = None

    def acquire(self) -> bool:
        ctypes.set_last_error(0)
        self.mutex = self.kernel.CreateMutexW(None, False, self.name)
        if not self.mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            self.kernel.CloseHandle(self.mutex)
            self.mutex = None
            # The first process may still be creating its activation event.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                event = self.kernel.OpenEventW(0x0002, False, self.name + "-activate")
                if event:
                    try:
                        if not self.kernel.SetEvent(event):
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        self.kernel.CloseHandle(event)
                    return False
                time.sleep(0.05)
            raise RuntimeError("toBili 已在启动，但暂未响应。请稍候再试。")
        self.event = self.kernel.CreateEventW(None, False, False, self.name + "-activate")
        if not self.event:
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())
        return True

    def wait_activation(self, milliseconds: int = 250) -> bool:
        return self.kernel.WaitForSingleObject(self.event, milliseconds) == 0

    def watch_window(self, title: str) -> None:
        """Run independently of WebView loaded/shown events, including hidden launches."""
        def watch():
            pending = True  # Explicitly display the initial window as well.
            while not self.stop.is_set():
                pending = self.wait_activation() or pending
                if pending and restore_own_window(title):
                    pending = False
        self.thread = threading.Thread(target=watch, name="desktop-activation", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
        for name in ("event", "mutex"):
            handle = getattr(self, name)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self, name, None)


def restore_own_window(title: str) -> bool:
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    for method, args, result in (
        ("EnumWindows", [callback_type, wintypes.LPARAM], wintypes.BOOL),
        ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        ("GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("ShowWindowAsync", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        ("SetForegroundWindow", [wintypes.HWND], wintypes.BOOL),
        ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
        ("IsIconic", [wintypes.HWND], wintypes.BOOL),
    ):
        function = getattr(user, method)
        function.argtypes, function.restype = args, result
    found = []

    @callback_type
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            text = ctypes.create_unicode_buffer(512)
            user.GetWindowTextW(hwnd, text, len(text))
            if text.value == title:
                found.append(hwnd)
                return False
        return True

    user.EnumWindows(visit, 0)
    if not found:
        return False
    hwnd = found[0]
    user.ShowWindowAsync(hwnd, 9 if user.IsIconic(hwnd) else 5)
    user.SetForegroundWindow(hwnd)
    # An async show may not have been processed yet. Retry until actually visible.
    return bool(user.IsWindowVisible(hwnd) and not user.IsIconic(hwnd))
