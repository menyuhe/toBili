"""Account-isolated WebView2 processes. Secrets never cross the HTTP/JS boundary."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

_guard = threading.RLock()
_sessions = {}


def checked_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or not (host == "bilibili.com" or host.endswith(".bilibili.com"))
    ):
        raise ValueError("账号窗口仅打开 B 站 HTTPS 页面")
    return url


def status_path(home, ticket):
    if not re.fullmatch(r"[a-f0-9]{32}", ticket):
        raise ValueError("窗口标识无效")
    return Path(home) / "browser-status" / (ticket + ".json")


def write_status(home, ticket, state, note=""):
    path = status_path(home, ticket)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"ticket": ticket, "state": state, "note": note}, ensure_ascii=False),
        encoding="utf-8",
    )
    temp.replace(path)


def read_status(home, ticket):
    try:
        return json.loads(status_path(home, ticket).read_text("utf-8"))
    except FileNotFoundError:
        return {"ticket": ticket, "state": "starting", "note": ""}


def command(home, owner, url, ticket):
    args = [
        "--account-browser",
        str(owner),
        "--home",
        str(Path(home).resolve()),
        "--target",
        checked_url(url),
        "--browser-ticket",
        ticket,
    ]
    return (
        [sys.executable, *args]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "tobili.desktop", *args]
    )


def launch(home, owner, url):
    checked_url(url)
    if sys.platform != "win32":
        raise ValueError("当前版本的账号专属窗口需要 Windows WebView2")
    key = (str(Path(home).resolve()), owner)
    with _guard:
        prior = _sessions.get(key)
        if prior and prior[0].poll() is None and read_status(home, prior[1])["state"] != "error":
            ticket = prior[1]
            path = status_path(home, ticket).with_suffix(".request")
            temp = path.with_suffix(".request.tmp")
            temp.write_text(json.dumps({"url": url, "request": uuid.uuid4().hex}), encoding="utf-8")
            temp.replace(path)
            return ticket
        ticket = uuid.uuid4().hex
        write_status(home, ticket, "starting")
        process = subprocess.Popen(
            command(home, owner, url, ticket),
            env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _sessions[key] = (process, ticket)
        return ticket


def close_for(home, owner):
    with _guard:
        session = _sessions.pop((str(Path(home).resolve()), owner), None)
        if session and session[0].poll() is None:
            session[0].terminate()


def cookie_pairs(raw, uid):
    pairs = {}
    for piece in raw.split(";"):
        name, sep, value = piece.strip().partition("=")
        if sep and re.fullmatch(r"[A-Za-z0-9_\-]+", name):
            pairs[name] = value
    if pairs.get("DedeUserID") != str(uid) or not pairs.get("SESSDATA"):
        raise ValueError("登录凭据与所选账号不一致，请重新登录这个账号")
    return pairs


def install_cookies(manager, pairs):
    manager.DeleteAllCookies()
    for name, value in pairs.items():
        cookie = manager.CreateCookie(name, value, ".bilibili.com", "/")
        cookie.IsSecure = True
        cookie.IsHttpOnly = name == "SESSDATA"
        manager.AddOrUpdateCookie(cookie)


def run(home, owner, url, ticket):
    checked_url(url)
    status_path(home, ticket)
    if sys.platform != "win32":
        raise ValueError("需要 Windows WebView2")
    import webview

    from ..container import Container

    box = None
    try:
        box = Container(home)
        profile = box.repos.accounts.by_id(owner)
        if not profile:
            raise ValueError("这个账号已不存在")
        pairs = cookie_pairs(box.cookie(owner), profile.uid)
        # Read-only identity check before any authenticated page is loaded.
        actual = box.gateway_for(owner).profile()
        if actual.uid != profile.uid:
            raise ValueError("B 站返回的账号与所选账号不一致，请重新登录")
        title = f"{profile.nickname} · B 站 · toBili"
    except Exception as exc:
        from ..runtime.redact import safe_exception

        write_status(home, ticket, "error", safe_exception(exc))
        return 1
    finally:
        if box:
            box.close()
    # Each account owns a separate process and private profile, even when two
    # windows are open concurrently. No localhost origin or js_api in this window.
    profile_path = Path(home) / "account-browser" / str(owner) / ticket
    from .shell import bundle_dir

    bundle = bundle_dir()
    icon = next(iter(bundle.glob("*.ico")), None) if bundle else None
    import ctypes
    from importlib.util import find_spec

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "toBili.Pro.Desktop" if find_spec("tobili.private_pack") else "toBili.Desktop"
    )
    win = webview.create_window(
        title,
        html='<html><body style="font:16px sans-serif;padding:32px">正在打开账号窗口…</body></html>',
        width=1120,
        height=820,
        min_size=(800, 600),
        text_select=True,
    )
    closed = threading.Event()
    win.events.closed += closed.set
    ready = threading.Event()

    def initialize():
        if ready.is_set():
            return
        ready.set()
        try:
            from System import Action

            form = win.native

            def setup():
                core = form.webview.CoreWebView2
                install_cookies(core.CookieManager, pairs)

                def navigate(_sender, args):
                    try:
                        checked_url(str(args.Uri))
                    except ValueError:
                        args.Cancel = True

                def popup(_sender, args):
                    args.Handled = True
                    with suppress(ValueError):
                        core.Navigate(checked_url(str(args.Uri)))

                # Disable the pywebview default handler that opens system browser.
                core.NewWindowRequested -= form.browser.on_new_window_request
                core.NewWindowRequested += popup
                core.NavigationStarting += navigate
                core.Settings.AreDevToolsEnabled = False
                core.Navigate(url)

            form.Invoke(Action(setup))
            write_status(home, ticket, "ready")
            pairs.clear()
        except Exception as exc:
            from ..runtime.redact import safe_exception

            write_status(home, ticket, "error", safe_exception(exc))
            win.destroy()

    win.events.loaded += initialize

    def requests():
        seen = ""
        check_at = 0
        path = status_path(home, ticket).with_suffix(".request")
        while not closed.wait(0.5):
            # Logout or account removal must close the independent session too.
            try:
                if time.monotonic() >= check_at:
                    import sqlite3

                    with sqlite3.connect(
                        f"file:{Path(home) / 'tobili.db'}?mode=ro", uri=True
                    ) as db:
                        credential = db.execute(
                            "SELECT credential_ref FROM account WHERE id=?", (owner,)
                        ).fetchone()
                    if not credential or not credential[0]:
                        win.destroy()
                        return
                    check_at = time.monotonic() + 5
                if not ready.is_set() or not path.exists():
                    continue
                request = json.loads(path.read_text("utf-8"))
                if request["request"] != seen:
                    seen = request["request"]
                    win.load_url(checked_url(request["url"]))
                    win.restore()
                    win.show()
            except (OSError, ValueError):
                continue

    try:
        webview.start(
            requests,
            gui="edgechromium",
            private_mode=True,
            storage_path=str(profile_path.resolve()),
            icon=str(icon) if icon else None,
        )
        if read_status(home, ticket)["state"] != "error":
            write_status(home, ticket, "closed")
        return 0
    except Exception as exc:
        from ..runtime.redact import safe_exception

        write_status(home, ticket, "error", safe_exception(exc))
        return 1
