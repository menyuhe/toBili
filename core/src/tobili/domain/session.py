"""会话键的命名。

一个账号一份 cookie，所以保险库里的键必须带上账号身份。老版本是单账号，
键就叫 `bilibili-session`；那一份要能被认出来并改名，否则升级之后所有人都
变成"未登录"，而凭据其实好端端躺在硬盘上。

**键必须能安全地当文件名。** 保险库把键直接拼成 `<键>.bin`，并有一张字符白名单。
在这里就把非法字符挡掉，比让保险库在下游抛一个不带上下文的 ValueError 好。
"""

from __future__ import annotations

# 单账号时代的键名。只用于迁移识别，新代码不该再往这个键里写东西。
LEGACY_SESSION = "bilibili-session"


class BadUid(ValueError):
    """uid 不能拿来拼键。消息是给人看的。"""


def session_key(uid: str) -> str:
    """某个账号的会话键。"""
    value = (uid or "").strip()
    if not value:
        raise BadUid("账号 uid 是空的，无法定位凭据")
    # B 站 uid 是纯数字。放宽到字母数字是为了能用测试号，但绝不允许
    # `.` `/` 这类能跳出目录的字符。
    if not value.isalnum() or len(value) > 32:
        raise BadUid(f"账号 uid {value!r} 不是合法的键名（应为字母数字）")
    return f"{LEGACY_SESSION}.{value}"


def looks_legacy(key: str) -> bool:
    """这个键是不是单账号时代留下的那一份。"""
    return key == LEGACY_SESSION
