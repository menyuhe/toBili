"""用例层：一个文件一个用户意图。

用例只依赖 `ports` 里的 Protocol 和 `domain` 里的值对象，不 import 任何适配器。
"""

from .enroll import Enroll, EnrollReport
from .scan_feeds import ScanFeeds, ScanReport
from .sign_in import QrSignIn, SignInExpired, SignInRejected, SignInResult

__all__ = [
    "Enroll",
    "EnrollReport",
    "QrSignIn",
    "ScanFeeds",
    "ScanReport",
    "SignInExpired",
    "SignInRejected",
    "SignInResult",
]
