"""Exercise actual macOS Keychain, isolated WebKit cookies and the frozen app."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def native_checks(home):
    import AppKit
    import Foundation as ns
    import Security as sec

    from tobili.adapters.vault import FileVault
    from tobili.adapters.vault.keychain import KeychainCodec
    from tobili.desktop.mac_browser import make_webview, set_cookies

    vault = FileVault(home / "vault")
    try:
        vault.write("test", "synthetic-only-secret")
        assert vault.secure and vault.read("test") == "synthetic-only-secret"
        assert b"synthetic-only-secret" not in (vault.root / "test.bin").read_bytes()
    finally:
        sec.SecItemDelete(
            {
                sec.kSecClass: sec.kSecClassGenericPassword,
                sec.kSecAttrService: "cn.4wdo.tobili.vault",
                sec.kSecAttrAccount: KeychainCodec(vault.root).account,
            }
        )
    AppKit.NSApplication.sharedApplication()
    views = [make_webview(ns.NSMakeRect(0, 0, 400, 300)) for _ in range(2)]
    done = []
    for index, view in enumerate(views):
        set_cookies(
            view,
            {"SESSDATA": "fake-" + str(index), "DedeUserID": str(index + 100)},
            lambda: done.append(True),
        )

    def wait(predicate):
        until = time.monotonic() + 30
        while not predicate() and time.monotonic() < until:
            ns.NSRunLoop.currentRunLoop().runUntilDate_(
                ns.NSDate.dateWithTimeIntervalSinceNow_(0.05)
            )
        assert predicate(), "WebKit callback timed out"

    wait(lambda: len(done) == 2)
    cookies = {}
    for index, view in enumerate(views):
        view.configuration().websiteDataStore().httpCookieStore().getAllCookies_(
            lambda rows, index=index: cookies.update(
                {index: {str(c.name()): str(c.value()) for c in rows}}
            )
        )
    wait(lambda: len(cookies) == 2)
    assert cookies[0]["SESSDATA"] == "fake-0" and cookies[1]["SESSDATA"] == "fake-1"
    assert all(not v.configuration().websiteDataStore().isPersistent() for v in views)
    return dict(keychain=True, isolated_account_cookies=True)


def main():
    import httpx

    root = Path(__file__).resolve().parents[1]
    name = "toBili-Pro" if (root / "core/src/tobili/private_pack.py").is_file() else "toBili"
    binary = root / "dist" / (name + ".app") / "Contents/MacOS" / name
    with tempfile.TemporaryDirectory(prefix="tobili-mac-smoke-") as folder:
        home = Path(folder)
        result = native_checks(home)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        process = subprocess.Popen(
            [str(binary), "--tray", "--home", str(home / "app"), "--port", str(port)],
            env=os.environ.copy(),
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=2, trust_env=False
            ) as client:
                for _ in range(100):
                    try:
                        if client.get("/api/health").is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    assert process.poll() is None, "Application exited"
                    time.sleep(0.3)
                else:
                    raise AssertionError("Application startup timed out")
                assert client.get("/").is_success
                schema = client.get("/openapi.json").json()["paths"]
                assert ("/api/public-publisher" in schema) == (name == "toBili-Pro")
                result.update(frozen_application=True, edition_boundary=True)
        finally:
            process.terminate()
            process.wait(timeout=20)
        print(json.dumps(result))


if __name__ == "__main__":
    if sys.platform != "darwin":
        raise SystemExit("Run on macOS")
    main()
