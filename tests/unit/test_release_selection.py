import importlib.util
from pathlib import Path


def checker():
    spec = importlib.util.spec_from_file_location(
        "release_check", Path(__file__).parents[2] / "tools/release_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_selection_excludes_runtime_and_rejects_private_code(tmp_path):
    module = checker()
    for name in (
        "core/src/tobili/main.py",
        "ui/dist/index.html",
        "data/session.bin",
        "ui/node_modules/a.js",
    ):
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("fixture", encoding="utf-8")
    files, issues = module.inspect(tmp_path)
    assert [p.relative_to(tmp_path).as_posix() for p in files] == ["core/src/tobili/main.py"]
    assert not issues
    (tmp_path / "core/src/tobili/private_pack.py").write_text("private", encoding="utf-8")
    assert any("private implementation" in issue for issue in module.inspect(tmp_path)[1])


def test_source_selection_reports_secrets_without_echoing_them(tmp_path):
    (tmp_path / "README.md").write_text("sk-" + "z" * 40, encoding="utf-8")
    _, issues = checker().inspect(tmp_path)
    assert issues == ["README.md: access-token"]
