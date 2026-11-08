"""Account-only WKWebView with a separate, ephemeral cookie store per window."""

import json
import sqlite3
import time
from pathlib import Path


def make_webview(rect):
    import WebKit

    config = WebKit.WKWebViewConfiguration.alloc().init()
    config.setWebsiteDataStore_(WebKit.WKWebsiteDataStore.nonPersistentDataStore())
    return WebKit.WKWebView.alloc().initWithFrame_configuration_(rect, config)


def set_cookies(view, pairs, complete):
    import Foundation as ns

    store = view.configuration().websiteDataStore().httpCookieStore()
    remaining = [len(pairs)]

    def saved():
        remaining[0] -= 1
        if not remaining[0]:
            complete()

    if not pairs:
        complete()
        return
    for name, value in pairs.items():
        properties = {
            ns.NSHTTPCookieName: name,
            ns.NSHTTPCookieValue: value,
            ns.NSHTTPCookieDomain: ".bilibili.com",
            ns.NSHTTPCookiePath: "/",
            ns.NSHTTPCookieSecure: "TRUE",
        }
        if name == "SESSDATA":
            properties["HttpOnly"] = "TRUE"
        cookie = ns.NSHTTPCookie.cookieWithProperties_(properties)
        if cookie is None:
            raise ValueError("账号 Cookie 无效")
        store.setCookie_completionHandler_(cookie, saved)


def run_window(home, owner, url, ticket, title, pairs):
    import AppKit as appkit
    import Foundation as ns
    from PyObjCTools import AppHelper

    from .account_browser import checked_url, read_status, status_path, write_status

    app = appkit.NSApplication.sharedApplication()
    app.setActivationPolicy_(appkit.NSApplicationActivationPolicyRegular)
    rect = ns.NSMakeRect(0, 0, 1120, 820)
    mask = (
        appkit.NSWindowStyleMaskTitled
        | appkit.NSWindowStyleMaskClosable
        | appkit.NSWindowStyleMaskMiniaturizable
        | appkit.NSWindowStyleMaskResizable
    )
    window = appkit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, mask, appkit.NSBackingStoreBuffered, False
    )
    window.setReleasedWhenClosed_(False)
    window.setTitle_(title)
    window.setMinSize_(ns.NSMakeSize(800, 600))
    view = make_webview(rect)
    window.setContentView_(view)
    closed = [False]

    class Delegate(appkit.NSObject):
        def windowWillClose_(self, notification):
            closed[0] = True
            AppHelper.stopEventLoop()

        def webView_decidePolicyForNavigationAction_decisionHandler_(
            self, webview, action, handler
        ):
            target = action.targetFrame()
            try:
                if target is None or target.isMainFrame():
                    checked_url(str(action.request().URL().absoluteString()))
                handler(1)
            except ValueError:
                handler(0)

        def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
            self, webview, config, action, features
        ):
            try:
                checked_url(str(action.request().URL().absoluteString()))
                webview.loadRequest_(action.request())
            except ValueError:
                pass
            return None

    delegate = Delegate.alloc().init()
    window.setDelegate_(delegate)
    view.setNavigationDelegate_(delegate)
    view.setUIDelegate_(delegate)
    request_file = status_path(home, ticket).with_suffix(".request")
    last_request = [""]
    last_check = [0.0]

    def navigate(target):
        request = ns.NSURLRequest.requestWithURL_(ns.NSURL.URLWithString_(checked_url(target)))
        view.loadRequest_(request)

    def ready():
        navigate(url)
        pairs.clear()
        write_status(home, ticket, "ready")

    def poll():
        if closed[0]:
            return
        try:
            if time.monotonic() >= last_check[0]:
                with sqlite3.connect(f"file:{Path(home) / 'tobili.db'}?mode=ro", uri=True) as db:
                    row = db.execute(
                        "SELECT credential_ref FROM account WHERE id=?", (owner,)
                    ).fetchone()
                if not row or not row[0]:
                    window.close()
                    return
                last_check[0] = time.monotonic() + 5
            if request_file.exists():
                data = json.loads(request_file.read_text("utf-8"))
                if data["request"] != last_request[0]:
                    navigate(data["url"])
                    last_request[0] = data["request"]
                    window.makeKeyAndOrderFront_(None)
                    app.activateIgnoringOtherApps_(True)
        except (OSError, ValueError):
            pass
        AppHelper.callLater(0.5, poll)

    try:
        window.center()
        window.makeKeyAndOrderFront_(None)
        app.activateIgnoringOtherApps_(True)
        set_cookies(view, pairs, ready)
        AppHelper.callLater(0.5, poll)
        AppHelper.runEventLoop()
        if read_status(home, ticket)["state"] != "error":
            write_status(home, ticket, "closed")
        return 0
    except Exception as exc:
        from ..runtime.redact import safe_exception

        write_status(home, ticket, "error", safe_exception(exc))
        return 1
