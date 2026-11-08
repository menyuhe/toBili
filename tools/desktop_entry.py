"""打包成单文件 exe 时的入口脚本。

单独一个文件而不是直接拿包当入口：PyInstaller 需要一个"脚本"作为分析起点，
而且 `freeze_support()` 必须在最开始调用 —— 打包后子进程会用同一个 exe 重新
执行这个文件，少了它就会出现"开了好几个副本、每个都在跑同一轮作业"。
"""

from __future__ import annotations

import multiprocessing
import sys

from tobili.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
