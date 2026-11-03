"""toBili desktop: native Windows/macOS window around the local application."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
import traceback
from urllib.parse import parse_qs

from desktop_platform import default_home, gui_backend, show_startup_error

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else ROOT
UPSTREAM = ROOT if getattr(sys, "frozen", False) else ROOT / "upstream"
sys.path.insert(0, str(UPSTREAM))
TITLE = "toBili"
SMOKE = "--smoke-test" in sys.argv
if SMOKE:
    os.environ["BINGGO_DISABLE_BACKGROUND"] = "1"
os.environ.setdefault("BINGGO_HOME", str(default_home(APP_DIR, smoke=SMOKE)))
HOME = Path(os.environ["BINGGO_HOME"])
_instance = None


class DesktopGuard:
    """Allow only this desktop session to reach the local API."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        from starlette.requests import Request
        from starlette.responses import JSONResponse, RedirectResponse

        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        host = request.headers.get("host", "")
        if host.split(":")[0] != "127.0.0.1":
            return await JSONResponse({"detail": "Invalid host"}, status_code=403)(scope, receive, send)
        origin = request.headers.get("origin")
        if origin and origin != f"http://{host}":
            return await JSONResponse({"detail": "Invalid origin"}, status_code=403)(scope, receive, send)
        if request.url.path == "/desktop/start":
            supplied = parse_qs(scope.get("query_string", b"").decode()).get("token", [""])[0]
            if secrets.compare_digest(supplied, self.token):
                response = RedirectResponse("/")
                response.set_cookie("binggo_desktop", self.token, httponly=True, samesite="strict")
                response.headers["Cache-Control"] = "no-store"
                response.headers["Referrer-Policy"] = "no-referrer"
                return await response(scope, receive, send)
        if not secrets.compare_digest(request.cookies.get("binggo_desktop", ""), self.token):
            return await JSONResponse({"detail": "请从桌面软件打开"}, status_code=403)(scope, receive, send)
        return await self.app(scope, receive, send)


def single_instance() -> bool:
    global _instance
    if sys.platform != "win32" or SMOKE:
        return True
    from desktop_instance import DesktopInstance
    _instance = DesktopInstance()
    return _instance.acquire()


def main() -> int:
    from src.dashboard_server import _ensure_stdio
    _ensure_stdio()
    HOME.mkdir(parents=True, exist_ok=True)
    if not single_instance():
        return 0
    import uvicorn
    import webview
    from web.app import app
    from web.auto_scheduler import auto_scheduler
    from web.job_runner import runner

    token = secrets.token_urlsafe(32)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0 if SMOKE else 18787))
    except OSError:
        listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(DesktopGuard(app, token), log_level="warning", access_log=False,
                                         loop="asyncio", timeout_graceful_shutdown=3))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("本地服务启动失败，请查看用户数据/data/logs。")
        time.sleep(0.1)

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    window = webview.create_window(TITLE, url + "/desktop/start?token=" + token,
                                   width=1320, height=880, min_size=(960, 640), text_select=True,
                                   background_color="#f8f7f4", hidden=SMOKE)

    def on_closing():
        if runner.any_running():
            window.create_confirmation_dialog("任务正在运行", "请先等待当前任务完成，或在任务日志中取消，再退出软件。")
            return False
        for scheduler in auto_scheduler.items.values():
            scheduler.stop(reason="桌面软件关闭")
        return True

    if not SMOKE:
        window.events.closing += on_closing
    result = {"ok": False}

    def smoke_test():
        import httpx
        try:
            with httpx.Client(base_url=url, trust_env=False, follow_redirects=True) as client:
                assert client.get("/api/summary").status_code == 403
                assert client.get("/desktop/start", params={"token": token}).status_code == 200
                assert client.get("/api/summary", headers={"Origin": "https://example.com"}).status_code == 403
                summary = client.get("/api/summary")
                assert summary.status_code == 200
                expected_sources = len(summary.json()["sources"])
                assert client.get("/api/activities").status_code == 200
                assert client.get("/api/accounts").status_code == 200
                assert client.get("/api/settings").status_code == 200
                cloud_status = client.get("/api/cloud/status")
                assert cloud_status.status_code == 200 and cloud_status.json()["connected"] is False
                notification_settings = client.get("/api/notifications/settings")
                assert notification_settings.status_code == 200
                assert "webhook" not in notification_settings.json()
                assert client.get("/api/notifications/history").status_code == 200
                assert client.get("/api/collections").status_code == 200
                assert client.get("/api/tickets/tasks").status_code == 401
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    state = window.evaluate_js("({title:document.title, ready:!!document.querySelector('#section-sources') && !!document.querySelector('#account-add') && !!document.querySelector('#account-list'), links:document.querySelectorAll('#source-grid a[href*=\"space.bilibili.com\"]').length})")
                    if state and state.get("ready") and state.get("links", 0) == expected_sources:
                        break
                    time.sleep(0.25)
                assert state and state.get("ready") and state.get("links", 0) == expected_sources, state
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    window.evaluate_js("document.querySelector('[data-section=\"sources\"]').click()")
                    time.sleep(0.6)
                    if window.evaluate_js("document.querySelector('#section-sources').classList.contains('active')"):
                        break
                assert window.evaluate_js("document.querySelector('#section-sources').classList.contains('active')")
                assert window.evaluate_js("!!document.querySelector('#notification-settings') && !!document.querySelector('[data-maintenance-view=cleanup]')")
                assert window.evaluate_js("!!document.querySelector('#section-tickets #ticket-project-form') && !!document.querySelector('#collection-add')")
                result.update(ok=True, native_window=state, summary_api="passed", activities_api="passed",
                              session_guard="passed", source_navigation="passed", notification_api="passed", maintenance_ui="passed", account_module="passed")
        except Exception:
            result["error"] = traceback.format_exc()
        finally:
            (HOME / "smoke-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            window.destroy()

    try:
        if _instance:
            _instance.watch_window(TITLE)
        webview.start(smoke_test if SMOKE else None, gui=gui_backend(), private_mode=False,
                      storage_path=str(HOME / "webview"), debug=False)
    finally:
        for scheduler in auto_scheduler.items.values():
            scheduler.stop(reason="桌面软件关闭")
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        if _instance:
            _instance.close()
    return 0 if not SMOKE or result["ok"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        HOME.mkdir(parents=True, exist_ok=True)
        log = HOME / "desktop-error.log"
        log.write_text(traceback.format_exc(), encoding="utf-8")
        if not SMOKE:
            show_startup_error(TITLE, log)
        raise SystemExit(1)
