# -*- mode: python ; coding: utf-8 -*-
"""Build the current toBili desktop, including its Cocoa WebKit window."""
from pathlib import Path
import re
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

if sys.platform != "darwin":
    raise SystemExit("macOS .app 必须在 Mac 上构建，请运行 build-macos.command。")

root = Path(SPECPATH).parent
upstream = root / "upstream"
datas = [
    (str(upstream / "web/static/dist"), "web/static/dist"),
    (str(upstream / "web/static/favicon.svg"), "web/static"),
    (str(upstream / "config/sources.yaml"), "config"),
    (str(upstream / "config/cookies.txt.example"), "config"),
    (str(upstream / "config/llm.env.example"), "config"),
] + collect_data_files("webview", subdir="js")
hidden = collect_submodules("src") + collect_submodules("web") + collect_submodules("uvicorn")
hidden += ["webview.platforms.cocoa", "AppKit", "Foundation", "WebKit", "objc", "PyObjCTools.AppHelper"]
version = re.search(r'^__version__\s*=\s*[\"\']([^\"\']+)',
                    (upstream / "src/app_paths.py").read_text(encoding="utf-8"), re.MULTILINE).group(1)
icon = str(upstream / "packaging/macos/binggo.icns")

a = Analysis([str(root / "desktop.py")], pathex=[str(upstream), str(root)],
             datas=datas, hiddenimports=hidden,
             excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter", "webview.platforms.winforms"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="toBili", console=False,
          icon=icon, argv_emulation=False, codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, name="toBili")
app = BUNDLE(coll, name="toBili.app", icon=icon, bundle_identifier="com.tobili.desktop",
             info_plist={
                 "CFBundleName": "toBili",
                 "CFBundleDisplayName": "toBili",
                 "CFBundleShortVersionString": version,
                 "CFBundleVersion": version,
                 "NSHighResolutionCapable": True,
                 "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
             })
