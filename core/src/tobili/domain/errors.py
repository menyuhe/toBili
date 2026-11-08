"""故障分类。

分类存在的唯一目的：决定**能不能重发**。而在这个项目里，答案永远是"读可以，写不行"。
所以 `retryable` 只对读路径有意义，写路径必须显式忽略它。
"""

from __future__ import annotations

from enum import StrEnum


class Fault(StrEnum):
    NETWORK = "network"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    RISK = "risk"
    AUTH = "auth"
    REMOTE_BUSINESS = "remote_business"
    PARSE = "parse"
    UNKNOWN = "unknown"

    @property
    def retryable_read(self) -> bool:
        """读请求重发是无害的，所以这几类值得再试一次。"""
        return self in {Fault.NETWORK, Fault.TIMEOUT, Fault.RATE_LIMIT}

    @property
    def needs_relogin(self) -> bool:
        return self is Fault.AUTH

    @property
    def suggests_backoff(self) -> bool:
        return self in {Fault.RATE_LIMIT, Fault.RISK}


class RemoteError(RuntimeError):
    """远端返回了明确的失败。携带故障分类，不携带凭据。"""

    def __init__(self, fault: Fault, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.fault = fault
        self.code = code

    def __str__(self) -> str:
        if self.code is None:
            return f"[{self.fault.value}] {self.args[0]}"
        return f"[{self.fault.value}:{self.code}] {self.args[0]}"


class AmbiguousWrite(RuntimeError):
    """写请求发出去了，但结果未知。

    这个异常不该被捕获后重试，它应该被记录成 `UNKNOWN` 并交给下一次探测。
    """


class MissingCredential(RuntimeError):
    """没有可用的登录态。调用方应引导用户重新扫码，而不是尝试用空 cookie 发请求。"""


class SchemaTooNew(RuntimeError):
    """库的版本比这个程序新，说明用户拿旧版程序打开了新版数据。

    继续跑下去的后果不是报错，而是**静默地读到空结果**：新表在这个程序里没有仓储，
    新列不会被查询，界面上看起来就是"活动都不见了"。所以宁可拒绝启动。
    """

    def __init__(self, found: int, expected: int) -> None:
        super().__init__(
            f"数据目录来自更新的版本（库版本 {found}，本程序只到 {expected}）。"
            "请用新版程序打开，或换一个数据目录。直接降级会读到不完整的数据。"
        )
        self.found = found
        self.expected = expected


class WriteUnavailable(RuntimeError):
    """写入权限暂不可用，队列应保留以便下轮继续。"""
