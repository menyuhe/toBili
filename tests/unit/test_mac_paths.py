import sys
from pathlib import Path

from tobili import config


def test_mac_keeps_data_outside_application_bundle(tmp_path, monkeypatch):
    monkeypatch.delenv("TOBILI_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(config, "is_frozen", lambda: True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "executable", "/Applications/toBili.app/Contents/MacOS/toBili")
    assert config.default_home() == tmp_path / "Library/Application Support" / config.edition_name()
    monkeypatch.setenv("TOBILI_HOME", str(tmp_path / "custom"))
    assert config.default_home() == tmp_path / "custom"
