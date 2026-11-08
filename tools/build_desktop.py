"""打一个单文件 exe。

    python tools/build_desktop.py

产物在 `dist/toBili.exe`，双击就能用。整个过程只有三步：画图标、跑 PyInstaller、
把路径打出来 —— 中间任何一步需要人工干预都说明这个脚本没写好。

几个刻意的选择：

- **`--onefile`**：用户要的是"一个文件，双击就跑"，不是"一个文件夹别乱动"。
  代价是每次启动要解压到临时目录（一两秒），值得。
- **`--windowed`**：不要黑框。代价是 stdout/stderr 为空，所以日志必须落文件
  （`desktop/_log_to` 里已经处理）。
- **`--add-data ui/dist`**：界面是构建产物，不打包进去就得让用户自己装 node。
- **`--collect-submodules uvicorn`**：uvicorn 按字符串动态加载协议/loop 实现，
  PyInstaller 的静态分析看不到，漏了就是运行时 ImportError。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "toBili"
DIST = ROOT / "dist"
WORK = ROOT / "build" / "pyinstaller"


def make_icon(target: Path) -> Path:
    """Read this build's source logo, never an imported package's stale ui/dist."""
    from PIL import Image

    image = Image.open(ROOT / "ui/public/icon.png").convert("RGBA")
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return target


def main() -> int:
    if sys.platform == "darwin":
        from build_macos import main as build_mac

        build_mac()
        return 0
    bundle = ROOT / "ui" / "dist"
    if not (bundle / "index.html").is_file():
        print("找不到 ui/dist —— 先在 ui/ 里跑一次 npm run build。", file=sys.stderr)
        return 2

    # 每次在新的工作目录打包，成功后只替换目标程序；不触碰同级用户数据。
    WORK.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="release-", dir=WORK))
    output = stage / "output"
    private = (ROOT / "core/src/tobili/private_pack.py").is_file()
    name = "toBili-Pro" if private else NAME
    icon = make_icon(stage / f"{name}.ico")
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        name,
        "--paths",
        str(ROOT / "core/src"),
        *(["--add-data", f"{ROOT / 'core/src/tobili/assets'};tobili/assets"] if private else []),
        "--icon",
        str(icon),
        "--add-data",
        f"{icon}{';' if sys.platform == 'win32' else ':'}ui/dist",
        # 绝对路径：加 --specpath 之后相对路径会按 spec 所在目录解析，踩过一次。
        "--add-data",
        f"{bundle}{';' if sys.platform == 'win32' else ':'}ui/dist",
        "--collect-submodules",
        "tobili",
        "--collect-submodules",
        "uvicorn",
        "--hidden-import",
        "pystray._win32",
        # 内嵌窗口。pywebview 带 JS 资源和平台后端，PyInstaller 不会自己收集；
        # 少了这些的表现是"窗口起不来、控制台一片空白"。
        "--collect-all",
        "webview",
        "--hidden-import",
        "webview.platforms.edgechromium",
        "--hidden-import",
        "clr_loader",
        "--hidden-import",
        "pythonnet",
        "--distpath",
        str(output),
        "--workpath",
        str(stage),
        "--specpath",
        str(stage),
        str(ROOT / "tools" / "desktop_entry.py"),
    ]

    print("运行：", " ".join(args[2:]))
    result = subprocess.run(
        args, cwd=ROOT, check=False, env={**os.environ, "PYTHONPATH": str(ROOT / "core/src")}
    )
    if result.returncode != 0:
        print("打包失败。", file=sys.stderr)
        return result.returncode

    exe = DIST / (name + (".exe" if sys.platform == "win32" else ""))
    DIST.mkdir(exist_ok=True)
    os.replace(output / exe.name, exe)

    print(f"\n完成：{exe}  ({exe.stat().st_size / 1024 / 1024:.1f} MB)")
    print("双击即可；数据在 exe 同级的 data/ 里。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
