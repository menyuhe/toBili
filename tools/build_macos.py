"""Build a native macOS application and a portable ZIP on the target architecture."""

import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != "darwin":
        raise SystemExit("macOS 安装包必须在 Mac 或 macOS 构建机上生成")
    from PIL import Image
    from release_version import validate_bundle

    version = validate_bundle(ROOT)

    private = (ROOT / "core/src/tobili/private_pack.py").is_file()
    name = "toBili-Pro" if private else "toBili"
    bundle_id = "cn.4wdo.tobili.pro" if private else "cn.4wdo.tobili.desktop"
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    work = ROOT / "build/macos"
    work.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="package-", dir=work))
    icon = stage / (name + ".icns")
    Image.open(ROOT / "ui/public/icon.png").convert("RGBA").resize((1024, 1024)).save(icon)
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        name,
        "--osx-bundle-identifier",
        bundle_id,
        "--icon",
        str(icon),
        "--paths",
        str(ROOT / "core/src"),
        "--add-data",
        f"{ROOT / 'ui/dist'}:ui/dist",
        "--collect-submodules",
        "tobili",
        "--collect-submodules",
        "uvicorn",
        "--collect-all",
        "webview",
        "--hidden-import",
        "webview.platforms.cocoa",
        "--hidden-import",
        "pystray._darwin",
        "--hidden-import",
        "Security",
        "--hidden-import",
        "cryptography.fernet",
        "--distpath",
        str(stage / "output"),
        "--workpath",
        str(stage / "work"),
        "--specpath",
        str(stage),
        str(ROOT / "tools/desktop_entry.py"),
    ]
    if private:
        args[args.index("--distpath") : args.index("--distpath")] = [
            "--add-data",
            f"{ROOT / 'core/src/tobili/assets'}:tobili/assets",
        ]
    subprocess.run(
        args, cwd=ROOT, check=True, env={**os.environ, "PYTHONPATH": str(ROOT / "core/src")}
    )
    app = stage / "output" / (name + ".app")
    info = app / "Contents/Info.plist"
    value = plistlib.loads(info.read_bytes())
    value.update(
        CFBundleDisplayName=name.replace("-", " "),
        CFBundleShortVersionString=version,
        CFBundleVersion=version,
        NSHighResolutionCapable=True,
        LSMinimumSystemVersion="15.0",
        NSAppTransportSecurity={"NSAllowsLocalNetworking": True},
    )
    info.write_bytes(plistlib.dumps(value))
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    target = dist / app.name
    if target.exists():
        assert target.resolve().parent == dist.resolve()
        shutil.rmtree(target)
    shutil.move(app, target)
    archive = dist / f"{name}-macOS-{platform.machine()}.zip"
    if archive.exists():
        archive.unlink()
    subprocess.run(
        ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(target), str(archive)],
        check=True,
    )
    print(archive)


if __name__ == "__main__":
    main()
