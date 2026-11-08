"""`python -m tobili.desktop` 的入口。开机自启写的就是这一条命令。"""

from __future__ import annotations

from . import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
