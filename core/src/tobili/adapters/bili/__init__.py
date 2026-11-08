"""B 站适配器。

对外只暴露 `BiliGateway` 与 `Wire`。想换实现（比如改用 app 端接口），
只要满足 `ports.bili` 里的 Protocol 即可，用例一行不用改。
"""

from .auth import QrAuthenticator, wait_for_scan
from .codes import code_of, ensure_ok, fault_of
from .feeds import DynamicFeed, SourceUnsupported
from .gateway import BiliGateway
from .http import Wire
from .reader import DynamicReader
from .writer import BiliWriter

__all__ = [
    "BiliGateway",
    "BiliWriter",
    "DynamicFeed",
    "DynamicReader",
    "QrAuthenticator",
    "SourceUnsupported",
    "Wire",
    "code_of",
    "ensure_ok",
    "fault_of",
    "wait_for_scan",
]
