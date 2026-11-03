from pathlib import Path
import hashlib
import json
import subprocess
import sys
import zipfile

import pytest

import desktop_platform
import package_macos_source as packaging
from build_macos import ui_reference_files, verify_ui_reference


def test_mac_data_is_outside_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop_platform.sys, "platform", "darwin")
    monkeypatch.delenv("BINGGO_PORTABLE", raising=False)
    monkeypatch.setattr(desktop_platform.Path, "home", lambda: tmp_path)
    app_dir = tmp_path / "Applications/toBili.app/Contents/MacOS"
    assert desktop_platform.default_home(app_dir) == tmp_path / "Library/Application Support/toBili"
    monkeypatch.setenv("BINGGO_PORTABLE", "1")
    assert desktop_platform.default_home(app_dir) == tmp_path / "Applications/toBili-data"
    assert desktop_platform.gui_backend() == "cocoa"


def test_windows_paths_and_renderer_preserved(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop_platform.sys, "platform", "win32")
    monkeypatch.setenv("BINGGO_PORTABLE", "1")
    assert desktop_platform.default_home(tmp_path) == tmp_path / "user-data"
    assert desktop_platform.default_home(tmp_path, smoke=True) == tmp_path / "smoke-data"
    assert desktop_platform.gui_backend() == "edgechromium"


def test_source_export_excludes_runtime_canaries(tmp_path):
    for relative in packaging.FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder", encoding="utf-8")
    safe_files = ("upstream/src/main.py", "upstream/web/app.py", "upstream/web/static/dist/index.html",
                  "upstream/web/frontend/src/main.ts")
    private_files = ("upstream/config/cookies.txt", "upstream/config/llm.env", "upstream/config/notifications.json",
                     "upstream/data/binggo.db", "用户数据/config/cookies.txt",
                     "upstream/web/frontend/node_modules/test/index.js", "upstream/web/__pycache__/app.pyc")
    for relative in safe_files + private_files:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("canary", encoding="utf-8")
    selected = {path.relative_to(tmp_path).as_posix() for path in packaging.source_files(tmp_path)}
    assert set(safe_files) <= selected
    assert not set(private_files) & selected


def test_command_zip_keeps_unix_permissions_and_line_endings(tmp_path):
    command = tmp_path / "build-macos.command"
    command.write_bytes(b"#!/bin/bash\r\necho build\r\n")
    archive = tmp_path / "kit.zip"
    packaging.write_archive(archive, [command], tmp_path)
    with zipfile.ZipFile(archive) as kit:
        info = kit.infolist()[0]
        assert info.create_system == 3
        assert (info.external_attr >> 16) & 0o777 == 0o755
        assert b"\r" not in kit.read(info)


def test_windows_build_rejected_before_installing_dependencies():
    if sys.platform == "darwin":
        return
    result = subprocess.run([sys.executable, str(Path(packaging.ROOT) / "scripts/build_macos.py")],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 1
    assert "pip" not in result.stdout


def test_ui_reference_detects_changed_screenshots_and_frontend(tmp_path):
    folder = tmp_path / "界面截图"
    folder.mkdir()
    screenshot = folder / "overview.png"
    screenshot.write_bytes(b"original screenshot")
    frontend = tmp_path / "upstream/web/static/dist/index.html"
    frontend.parent.mkdir(parents=True)
    frontend.write_bytes(b"same frontend")
    for name in ("index.html", "README.txt"):
        (folder / name).write_text("reference", encoding="utf-8")
    manifest = {"images": [{"path": "overview.png", "sha256": hashlib.sha256(screenshot.read_bytes()).hexdigest()}],
                "frontend": {"index.html": hashlib.sha256(frontend.read_bytes()).hexdigest()}}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    verify_ui_reference(tmp_path)
    assert screenshot in packaging.source_files(tmp_path)
    frontend.write_bytes(b"different frontend")
    with pytest.raises(RuntimeError, match="前端资源"):
        verify_ui_reference(tmp_path)
    screenshot.write_bytes(b"different screenshot")
    with pytest.raises(RuntimeError, match="界面截图校验失败"):
        ui_reference_files(tmp_path)
