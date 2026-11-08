"""写操作回执。

`ambiguous` 是这里唯一值得多看一眼的字段：它为真表示"请求发出去了，但不知道结果"。
调用方拿到它时**不能重试**，只能记成 `UNKNOWN` 交给下一次探测。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import Fault


@dataclass(frozen=True, slots=True)
class Receipt:
    ok: bool
    message: str = ""
    remote_id: str = ""
    fault: Fault | None = None
    ambiguous: bool = False

    @classmethod
    def success(cls, remote_id: str = "", message: str = "") -> Receipt:
        return cls(True, message=message, remote_id=remote_id)

    @classmethod
    def rejected(cls, fault: Fault, message: str) -> Receipt:
        return cls(False, message=message, fault=fault)

    @classmethod
    def unclear(cls, message: str, *, fault: Fault | None = None) -> Receipt:
        return cls(False, message=message, fault=fault, ambiguous=True)

    @property
    def settled(self) -> bool:
        return self.ok or (not self.ambiguous and self.fault is not None)
