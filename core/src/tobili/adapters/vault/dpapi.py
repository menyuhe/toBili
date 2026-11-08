"""Windows DPAPI 封装。

只做一件事：把一段字节变成"只有当前 Windows 用户、在这台机器上才能解开"的字节。
密钥由系统托管，本程序不持有、不备份、不传输——这是它与"自己生成一把密钥然后
放在同一个目录里"最本质的区别。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Final

# 附加熵：让别的程序即使以同一用户身份调用 DPAPI，也解不开本程序的数据。
_ENTROPY: Final[bytes] = b"tobili.vault.v1"
_UI_FORBIDDEN: Final[int] = 0x01


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(payload: bytes) -> tuple[_Blob, ctypes.Array]:  # type: ignore[type-arg]
    if not payload:
        raise ValueError("DPAPI 不接受空数据")
    buf = ctypes.create_string_buffer(payload, len(payload))
    return _Blob(len(payload), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _take(blob: _Blob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        if blob.pbData:
            ctypes.windll.kernel32.LocalFree(blob.pbData)


def available() -> bool:
    return sys.platform == "win32"


def _crypt32() -> ctypes.WinDLL:  # type: ignore[name-defined]
    if not available():
        raise OSError("当前平台没有 DPAPI")
    return ctypes.WinDLL("crypt32", use_last_error=True)


def seal(payload: bytes) -> bytes:
    """加密。结果只能被同一用户在同一台机器上解开。"""
    api = _crypt32()
    data, _keep_data = _to_blob(payload)
    entropy, _keep_entropy = _to_blob(_ENTROPY)
    out = _Blob()
    ok = api.CryptProtectData(
        ctypes.byref(data),
        None,
        ctypes.byref(entropy),
        None,
        None,
        _UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData 失败")
    return _take(out)


def unseal(blob: bytes) -> bytes:
    """解密。换用户或换机器会失败 —— 这是预期行为，不是 bug。"""
    api = _crypt32()
    data, _keep_data = _to_blob(blob)
    entropy, _keep_entropy = _to_blob(_ENTROPY)
    out = _Blob()
    ok = api.CryptUnprotectData(
        ctypes.byref(data),
        None,
        ctypes.byref(entropy),
        None,
        None,
        _UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData 失败")
    return _take(out)
