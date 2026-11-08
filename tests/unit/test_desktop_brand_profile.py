import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from tobili.desktop import window


def test_native_window_keeps_profile_across_restarts(tmp_path, monkeypatch):
    starts, windows, stopped = [], [], []
    fake = SimpleNamespace(
        create_window=lambda *a, **k: windows.append((a, k)), start=lambda **kw: starts.append(kw)
    )
    monkeypatch.setitem(sys.modules, "webview", fake)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    icon = bundle / "toBili.ico"
    Image.new("RGBA", (32, 32), "pink").save(icon)
    monkeypatch.setattr("tobili.desktop.shell.bundle_dir", lambda: bundle)
    shell = SimpleNamespace(paths=SimpleNamespace(home=tmp_path), stop=lambda: stopped.append(True))
    window.run("http://127.0.0.1:8811", shell=shell)
    marker = Path(starts[0]["storage_path"]) / "profile-marker"
    marker.write_text("kept", encoding="utf-8")
    window.run("http://127.0.0.1:8811", home=tmp_path)
    assert starts[0] == starts[1]
    assert starts[0]["private_mode"] is False
    assert starts[0]["icon"] == str(icon)
    assert marker.read_text() == "kept"
    assert len(stopped) == 1


def test_packaged_icon_comes_from_build_source_not_imported_ui(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "build_desktop_test", root / "tools/build_desktop.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    public = tmp_path / "ui/public"
    public.mkdir(parents=True)
    Image.new("RGBA", (256, 256), (255, 80, 140, 255)).save(public / "icon.png")
    stale = tmp_path / "ui/dist"
    stale.mkdir()
    Image.new("RGBA", (128, 128), "teal").save(stale / "icon.png")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    with Image.open(module.make_icon(tmp_path / "out/app.ico")) as icon:
        assert icon.getpixel((128, 128)) == (255, 80, 140, 255)
        assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= icon.ico.sizes()
