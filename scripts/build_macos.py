"""Native macOS builder for the locally customized toBili desktop."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def ui_reference_files(root: Path = ROOT) -> list[Path]:
    folder = root / "界面截图"
    if not (folder / "manifest.json").exists():
        return []
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    files = [folder / name for name in ("index.html", "README.txt", "manifest.json")]
    for item in manifest["images"]:
        path = folder / item["path"]
        if not path.resolve().is_relative_to(folder.resolve()) or path.suffix.lower() != ".png":
            raise RuntimeError("界面截图清单中的路径无效。")
        if path.is_symlink():
            raise RuntimeError("界面截图不能使用符号链接。")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
                raise RuntimeError(f"界面截图校验失败：{item['path']}")
        files.append(path)
    return files


def verify_ui_reference(root: Path = ROOT) -> None:
    if not (root / "界面截图/manifest.json").exists():
        return
    ui_reference_files(root)
    manifest = json.loads((root / "界面截图/manifest.json").read_text(encoding="utf-8"))
    frontend = root / "upstream/web/static/dist"
    for name, expected in manifest["frontend"].items():
        path = frontend / name
        if not path.resolve().is_relative_to(frontend.resolve()):
            raise RuntimeError("前端资源清单中的路径无效。")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise RuntimeError(f"前端资源与截图基准不一致：{name}")


def run(*args: str, cwd: Path = ROOT) -> None:
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def validate_config() -> None:
    config = ROOT / "upstream/config"
    cookies = (config / "cookies.txt.example").read_text(encoding="utf-8")
    if any(line.strip() and not line.lstrip().startswith("#") for line in cookies.splitlines()):
        raise RuntimeError("Cookie 模板包含实际内容，停止打包。")
    template = (config / "llm.env.example").read_text(encoding="utf-8")
    if "LLM_API_KEY=your_api_key_here" not in template.splitlines():
        raise RuntimeError("API Key 模板不是占位符，停止打包。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild-frontend", action="store_true", help="用 Node.js 20+ 重新编译前端")
    parser.add_argument("--smoke-test", action="store_true", help="构建后启动原生窗口并执行桌面自检")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.exit(1, "macOS .app/.dmg 必须在 Mac 上构建；当前系统不是 macOS。\n")
    if sys.version_info < (3, 12):
        parser.exit(1, "需要 Python 3.12+。\n")
    arch = platform.machine()
    if arch not in {"arm64", "x86_64"}:
        parser.exit(1, f"不支持的 Python 架构：{arch}\n")
    validate_config()
    verify_ui_reference()
    run(sys.executable, "-m", "pip", "install", "-r", "requirements-desktop.txt")
    if args.rebuild_frontend:
        frontend = ROOT / "upstream/web/frontend"
        run("npm", "ci", cwd=frontend)
        run("npm", "run", "build", cwd=frontend)
    if not (ROOT / "upstream/web/static/dist/index.html").is_file():
        parser.exit(1, "缺少已编译前端，请加 --rebuild-frontend（需要 Node.js 20+）。\n")
    run(sys.executable, "upstream/packaging/macos/generate_icns.py")
    native_dist = ROOT / "build-output/macos" / arch
    run(sys.executable, "-m", "PyInstaller", "scripts/desktop-macos.spec", "--noconfirm", "--clean",
        "--distpath", str(native_dist), "--workpath", str(ROOT / "build/macos" / arch))
    app = native_dist / "toBili.app"
    executable = app / "Contents/MacOS/toBili"
    run("lipo", "-verify_arch", arch, str(executable))
    run("codesign", "--verify", "--deep", "--strict", str(app))
    if args.smoke_test:
        with tempfile.TemporaryDirectory(prefix="tobili-smoke-") as home:
            env = dict(os.environ, BINGGO_HOME=home, BINGGO_DISABLE_BACKGROUND="1")
            subprocess.run([str(executable), "--smoke-test"], env=env, timeout=150, check=True)
            result = json.loads((Path(home) / "smoke-result.json").read_text(encoding="utf-8"))
            if not result.get("ok"):
                raise RuntimeError(f"桌面自检失败：{result}")
            print("桌面自检通过。", flush=True)
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tobili-macos-") as temporary:
        stage = Path(temporary)
        # ditto preserves bundle symlinks, executable permissions and metadata.
        run("ditto", str(app), str(stage / "toBili.app"))
        for name in ("README.md", "LICENSE"):
            shutil.copy2(ROOT / name, stage / name)
        shutil.copytree(ROOT / "docs", stage / "docs")
        for source in ui_reference_files():
            destination = stage / source.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        portable = stage / "toBiliPortable.command"
        portable.write_text('#!/bin/bash\nset -euo pipefail\ncd "$(dirname "$0")"\n'
                            'export BINGGO_PORTABLE=1\nexec "./toBili.app/Contents/MacOS/toBili"\n',
                            encoding="utf-8", newline="\n")
        portable.chmod(0o755)
        zip_path = dist / f"toBili-macOS-{arch}.zip"
        run("ditto", "-c", "-k", "--sequesterRsrc", str(stage), str(zip_path))
        (stage / "Applications").symlink_to("/Applications")
        dmg_path = dist / f"toBili-macOS-{arch}.dmg"
        run("hdiutil", "create", "-volname", "toBili", "-srcfolder", str(stage),
            "-ov", "-format", "UDZO", str(dmg_path))
        run("hdiutil", "verify", str(dmg_path))
    checksums = dist / f"toBili-macOS-{arch}-SHA256.txt"
    lines = []
    for path in (zip_path, dmg_path):
        with path.open("rb") as stream:
            lines.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {path.name}\n")
    checksums.write_text("".join(lines), encoding="utf-8")
    print(f"完成：\n{zip_path}\n{dmg_path}\n{checksums}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
