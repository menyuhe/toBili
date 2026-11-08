"""业务码映射。

B 站把"成功/未登录/风控/太频繁"都塞在 HTTP 200 的 `code` 字段里，如果只看状态码
就会把风控当成成功。这里统一翻译成 `Fault`，让上层能用同一套判断处理。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...domain.errors import Fault, RemoteError

OK = 0
NOT_LOGGED_IN = -101
CSRF_INVALID = -111
# 接口要签名但没签、或者签错了。和 -404 必须分开：评论接口对"签名不对"回 -403，
# 对"这个 oid 没有评论区"回 -404。混在一起的表现是功能静默失效。
FORBIDDEN = -403
# 资源不存在。评论接口用它表示"这条动态没有可读的评论区"。
NOT_FOUND = -404
RISK = -352
INTERCEPTED = -412
TOO_FREQUENT = -509

_RISK_CODES = {RISK, INTERCEPTED, TOO_FREQUENT}
_AUTH_CODES = {NOT_LOGGED_IN, CSRF_INVALID}

# 已经做过的情况。出现在写操作里不算错误，应当当作"已达成"。
ALREADY_DONE_CODES = {
    65006,  # 已点赞
    22014,  # 已关注
    71000,  # 已预约
    11001,  # 已收藏
}

# 目标已经不存在。实测：详情接口对"形状正确但不存在的动态 ID"返回 4101105，
# 删除接口对同一 ID 返回 4126143。两者都只说明"这条动态没了"，不说明权限或风控。
# 清理流程据此把"已经删掉了"识别出来，而不是当成失败反复重试。
#
# 4101152 是删除**成功**之后详情接口的答复（"动态不可见"），2026-11-05 真机删除一条
# 转发后实测得到。最初只收了 4101105，后果不是重复删除（探测不到就不写），而是删完
# 之后复查永远返回 UNKNOWN —— 界面上一句"未确认"，等于没验。
GONE_CODES = {4101105, 4126143, 4101152}


def is_gone(code: int) -> bool:
    return code in GONE_CODES


def code_of(payload: Mapping[str, Any]) -> int:
    try:
        return int(payload.get("code") or 0)
    except (TypeError, ValueError):
        return OK


def fault_of(code: int) -> Fault:
    if code in _RISK_CODES:
        return Fault.RISK
    if code in _AUTH_CODES:
        return Fault.AUTH
    return Fault.REMOTE_BUSINESS


def ensure_ok(payload: Mapping[str, Any], *, what: str) -> dict[str, Any]:
    """读路径用：非 0 一律抛错，不猜。"""
    code = code_of(payload)
    if code == OK:
        return dict(payload)
    raise RemoteError(
        fault_of(code),
        f"{what}失败：{payload.get('message') or payload.get('msg') or '未知原因'}",
        code=code,
    )
