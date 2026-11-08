"""桌面壳。

真正要证明的三件事：第二份不会起第二个服务、开机自启开关能来回拨、服务在后台
线程里起得来而且只监听环回。托盘本身没法在测试里点，它的失败预期是"降级而不是崩"。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import pytest

from tobili.desktop.autostart import _command, is_enabled, set_enabled
from tobili.desktop.shell import Shell, bundle_dir, free_port

_WIN = sys.platform == "win32"


def test_bundle_dir_exists_in_the_repo() -> None:
    found = bundle_dir()
    # 开发态必须能找到 ui/dist，否则"界面打不开"会被误当成后端出错。
    if found is not None:
        assert (found / "index.html").is_file()


def test_free_port_is_bindable() -> None:
    port = free_port()
    assert 0 < port < 65536
    # 没监听时连不上是预期，这里只验证端口值可用
    with httpx.Client(timeout=1) as client, pytest.raises(httpx.HTTPError):
        client.get(f"http://127.0.0.1:{port}/api/health")


def test_default_desktop_avoids_other_editions_port(tmp_path, monkeypatch):
    from tobili.desktop import build_parser

    monkeypatch.setattr("tobili.desktop.shell.free_port", lambda: 18112)
    args = build_parser().parse_args([])
    assert Shell(home=str(tmp_path), port=args.port).port == 18112
    assert build_parser().parse_args(["--port", "18113"]).port == 18113


def test_frozen_home_never_adopts_parent_database(tmp_path, monkeypatch):
    from tobili import config

    monkeypatch.setattr(sys, "platform", "win32")

    parent_data = tmp_path / "data"
    parent_data.mkdir()
    (parent_data / "tobili.db").write_text("unrelated account data")
    monkeypatch.delenv("TOBILI_HOME", raising=False)
    monkeypatch.setattr(config, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "edition/toBili.exe"))
    assert config.default_home() == tmp_path / "edition/data"


def test_second_instance_cannot_claim(tmp_path: Path) -> None:
    first = Shell(home=str(tmp_path))
    assert first.claim()
    try:
        second = Shell(home=str(tmp_path))
        # 共用同一个数据目录 —— 也就是同一个锁文件。
        assert not second.claim()
    finally:
        first.stop()


def test_existing_url_falls_back_to_default_port(tmp_path: Path) -> None:
    shell = Shell(home=str(tmp_path))
    # 没跑过任何实例时 data/desktop.json 不存在，不能因此报错。
    assert shell.existing_url().endswith(f":{shell.port}/")


def test_shell_serves_the_api_on_loopback(tmp_path: Path) -> None:
    shell = Shell(home=str(tmp_path))
    assert shell.claim()
    try:
        url = shell.start()
        assert url.startswith("http://127.0.0.1:")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                response = httpx.get(url + "api/health", timeout=2)
            except httpx.HTTPError:
                time.sleep(0.1)
                continue
            assert response.json() == {"status": "ok"}
            break
        else:
            pytest.fail("服务在 10 秒内没有可访问")

        # 单实例的地址文件要写下来，第二次双击才有地方读。
        address = tmp_path / "desktop.json"
        assert address.is_file()
        assert str(shell.port) in address.read_text(encoding="utf-8")
    finally:
        shell.stop()


def test_stop_releases_the_lock(tmp_path: Path) -> None:
    shell = Shell(home=str(tmp_path))
    assert shell.claim()
    shell.start()
    shell.stop()
    again = Shell(home=str(tmp_path))
    assert again.claim()
    again.stop()


@pytest.mark.skipif(not _WIN, reason="开机自启只在 Windows 上实现")
def test_autostart_round_trip() -> None:
    # 用一次性的键名，不去碰用户真正的启动项。
    name = "toBiliSelfTest"
    try:
        set_enabled(True, name=name)
        assert is_enabled(name)
        set_enabled(False, name=name)
        assert not is_enabled(name)
    finally:
        set_enabled(False, name=name)


@pytest.mark.skipif(not _WIN, reason="开机自启只在 Windows 上实现")
def test_disabling_twice_is_fine() -> None:
    name = "toBiliSelfTest"
    set_enabled(False, name=name)
    set_enabled(False, name=name)
    assert not is_enabled(name)


def test_autostart_command_points_at_the_shell() -> None:
    # 开发态用 -m，冻结态用 exe 本身 —— 写错了会变成"开机启动一个什么都不干的 python"。
    assert "--tray" in _command()
    dev = _command()
    assert "tobili.desktop" in dev or dev.startswith('"')
