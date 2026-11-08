"""日志脱敏。

走在两层的中间：上一层是结构化字段，下一层是文本兜底。字段名命中敏感集合就整体
替换；文本里出现 `SESSDATA=...` 这种也能被正则拦住。只用黑名单会在漏掉新键名时
翻车，所以最后还有一道"整串疑似凭据就丢弃"的判断。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_SENSITIVE = {
    "cookie",
    "cookies",
    "sessdata",
    "bili_jct",
    "csrf",
    "token",
    "refresh_token",
    "access_token",
    "authorization",
    "password",
    "secret",
    "api_key",
    "apikey",
    "webhook",
    "sign",
    "credential",
}

_INLINE = re.compile(
    r"(SESSDATA|bili_jct|DedeUserID__ckMd5|access_token|refresh_token|Authorization)"
    r"\s*[=:]\s*[^\s;,&\"'}]+",
    re.IGNORECASE,
)

_MAX_TEXT = 500


def _hit(key: str) -> bool:
    lowered = key.lower()
    return lowered in _SENSITIVE or any(word in lowered for word in ("secret", "password", "token"))


def scrub(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "<too-deep>"
    if isinstance(value, Mapping):
        return {
            key: ("***" if _hit(str(key)) else scrub(item, depth=depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def scrub_text(text: str) -> str:
    clipped = text[:_MAX_TEXT]
    return _INLINE.sub(lambda m: f"{m.group(1)}=***", clipped)


def mask_id(value: str, *, head: int = 4, tail: int = 4) -> str:
    """账号标识。足够人工核对，不足以拿去搜索。"""
    if len(value) <= head + tail:
        return "***"
    return f"{value[:head]}…{value[-tail:]}"


def safe_exception(exc: BaseException) -> str:
    message = scrub_text(str(exc)) or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {message}"
