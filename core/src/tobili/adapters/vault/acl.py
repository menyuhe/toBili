"""文件权限：收紧，然后**验证**。

这里踩过两个坑，都在注释里留了记号：

一、`/inheritance:r` 与 `/grant:r` 分两条命令执行时，中间存在一个 DACL 为空的
    瞬间。第二条一旦失败（杀毒软件锁文件、域账户解析不到、超时），文件就对所有人
    关闭 —— **包括自己**。所以合成一条命令，让 icacls 一次写好。
二、`icacls` 返回 0 只代表命令跑了。写完必须回读 ACL，再亲手打开一次文件：
    能打开才算活着。

身份优先用 SID。账户名在域环境、Microsoft 账户、重名场景下都可能解析不到，
SID 不会。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# 这些主体出现在 ACL 里就意味着"别人也能读"。留一个都算没收紧。
_WIDE = re.compile(r"(BUILTIN\\Users|Everyone|Authenticated Users|INTERACTIVE)", re.IGNORECASE)


def _account() -> str:
    who = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    domain = os.environ.get("USERDOMAIN") or ""
    if not who:
        return ""
    return who if not domain or "\\" in who else f"{domain}\\{who}"


def identity() -> str:
    """返回 icacls 认的主体写法。`*S-1-5-...` 是 SID 语法。"""
    try:
        done = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        done = None
    if done is not None and done.returncode == 0:
        text = done.stdout.decode("mbcs", errors="replace").strip()
        tail = text.rsplit(",", 1)[-1].strip().strip('"')
        if tail.startswith("S-1-"):
            return f"*{tail}"
    return _account()


def _run(argv: list[str]) -> bool:
    try:
        done = subprocess.run(argv, capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def readable(target: Path | str) -> bool:
    """真的打开一次。权限表好看不代表能读。"""
    try:
        with Path(target).open("rb"):
            return True
    except OSError:
        return False


def tighten(target: Path | str) -> bool:
    """剥掉继承，只留自己。返回"确实收紧了且文件仍可读"。"""
    path = Path(target)
    if not path.exists():
        return False
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o600)
        except OSError:
            return False
        return True

    owner = identity()
    if not owner:
        return False

    # 一条命令完成"断继承 + 授权"。分成两条会留下空 DACL 的窗口期。
    if not _run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{owner}:(F)"]):
        return False
    if readable(path):
        return True

    # 收紧之后自己反而读不了了。先把访问权放回去，宁可这层没收紧，也不能锁死数据。
    _run(["icacls", str(path), "/grant", f"{owner}:(F)"])
    return False


def audit(target: Path | str) -> tuple[bool, tuple[str, ...]]:
    """回读 ACL。返回 (是否只剩自己, 残留的宽泛主体)。"""
    path = Path(target)
    if sys.platform != "win32":
        try:
            mode = path.stat().st_mode
        except OSError:
            return False, ("<无法读取>",)
        return (mode & 0o077) == 0, ()

    try:
        done = subprocess.run(["icacls", str(path)], capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return False, ("<调用失败>",)

    listing = done.stdout.decode("mbcs", errors="replace")
    leftovers = tuple(dict.fromkeys(m.group(0) for m in _WIDE.finditer(listing)))
    return (not leftovers, leftovers)


def secure(target: Path | str) -> bool:
    """收紧 + 验证。只有两者都过才返回真，调用方据此决定要不要告警。"""
    tightened = tighten(target)
    clean, _leftovers = audit(target)
    return tightened and clean and readable(target)
