"""Export a clean, self-contained Mac build kit using an explicit source allowlist."""
from __future__ import annotations

import hashlib
from pathlib import Path
import stat
import zipfile

from build_macos import ROOT, ui_reference_files, validate_config, verify_ui_reference

KIT_NAME = "toBili-macOS-BuildKit"
FILES = (
    "desktop.py", "scripts/build-windows.ps1", "scripts/desktop.spec", "upstream/packaging/windows/binggo.ico", "desktop_platform.py", "desktop_instance.py", "scripts/desktop-macos.spec",
    "scripts/build-macos.command", "scripts/build_macos.py", "scripts/package_macos_source.py",
    "requirements-desktop.txt", "README.md", "LICENSE",
    "pytest.ini", ".github/workflows/build-macos.yml",
    "upstream/README.md", "upstream/requirements.txt", "upstream/config/sources.yaml",
    "upstream/docs/multi-account.md",
    "upstream/config/cookies.txt.example", "upstream/config/llm.env.example",
    "upstream/packaging/assets/app-icon.png", "upstream/packaging/icon_raster.py",
    "upstream/packaging/macos/generate_icns.py", "upstream/web/static/favicon.svg",
)


def source_files(root: Path = ROOT) -> list[Path]:
    files = [root / name for name in FILES]
    for folder in ("upstream/tests", "upstream/scripts", "upstream/docs", "tests", "docs", "scripts"):
        files.extend(p for p in (root / folder).rglob("*")
                     if p.is_file() and not {"__pycache__", "node_modules", ".pytest_cache"}.intersection(p.relative_to(root).parts)
                     and (p.suffix in {".png", ".webp", ".py", ".js", ".mjs", ".cjs", ".json", ".html", ".css", ".svg", ".md", ".txt", ".yaml", ".yml", ".sh", ".service"}
                          or p.name in {"Dockerfile", "Caddyfile", "ToBili"}))
    for folder in ("upstream/src", "upstream/web"):
        files.extend(p for p in (root / folder).rglob("*.py")
                     if not {"__pycache__", "node_modules", ".venv"}.intersection(p.relative_to(root).parts))
    for folder in ("upstream/web/frontend", "upstream/web/static/dist"):
        files.extend(p for p in (root / folder).rglob("*")
                     if p.is_file() and not {"node_modules", "__pycache__", "test-results", "playwright-report"}.intersection(p.relative_to(root).parts)
                     and p.suffix in {".ts", ".json", ".html", ".css", ".js", ".svg", ".png", ".woff", ".woff2", ".md"})
    files.extend(p for p in (root / "upstream/web/static/article-emotes").glob("*") if p.is_file())
    if (root / "界面截图/manifest.json").is_file():
        files.extend(ui_reference_files(root))
    return sorted(set(files))


def write_archive(archive: Path, files: list[Path], root: Path = ROOT) -> None:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as out:
        for path in files:
            relative = path.relative_to(root)
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f"构建文件缺失或为链接：{relative}")
            name = f"{KIT_NAME}/{relative.as_posix()}"
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if path.suffix == ".command" else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            content = path.read_bytes()
            if path.suffix in {".command", ".py", ".spec", ".yml", ".txt"}:
                content = content.replace(b"\r\n", b"\n")
            out.writestr(info, content)
    with zipfile.ZipFile(archive) as check:
        if check.testzip():
            raise RuntimeError("ZIP 校验失败")


def main() -> int:
    validate_config()
    # Iteration kits omit historical screenshots, which are not build inputs.
    if not (ROOT / "upstream/web/static/dist/index.html").is_file():
        raise RuntimeError("请先编译前端。")
    archive = ROOT / "dist" / f"{KIT_NAME}.zip"
    archive.parent.mkdir(exist_ok=True)
    files = [p for p in source_files() if "界面截图" not in p.relative_to(ROOT).parts]
    write_archive(archive, files)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"完成：{archive}\n文件数：{len(files)}\nSHA256：{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
