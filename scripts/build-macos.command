#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "请将此目录拷贝到 Mac 后再运行。"
    exit 1
fi
PYTHON="${TOBILI_PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "请先安装 python.org 提供的 Python 3.12 或更新版本。"
    exit 1
fi
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 12), "需要 Python 3.12+"'
if [[ ! -x .venv-macos/bin/python ]]; then
    "$PYTHON" -m venv .venv-macos
fi
.venv-macos/bin/python scripts/build_macos.py "$@"
echo "构建完成，安装文件位于本目录 dist 文件夹。"
