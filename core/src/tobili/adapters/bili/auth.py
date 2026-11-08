"""扫码登录。

流程只有三步：申请 → 轮询 → 拿到 cookie。这里额外做了一件事：登录成功后补一个
浏览器指纹 cookie（buvid3）。缺它的话，部分接口会把已登录的请求当成未登录，
症状是"明明登录了但读不到数据"，很容易误判成 cookie 失效。
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ...domain.errors import Fault, RemoteError
from ...ports.models import QrProgress, QrTicket
from . import codes, endpoints
from .http import Wire

_WANTED = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid")

_WAITING = 86101
_SCANNED = 86090
_EXPIRED = 86038
_CONFIRMED = 0


class QrAuthenticator:
    def __init__(self, transport: Wire | None = None) -> None:
        self._transport = transport or Wire()
        self._owned = transport is None

    def close(self) -> None:
        if self._owned:
            self._transport.close()

    def begin(self) -> QrTicket:
        payload = codes.ensure_ok(self._transport.fetch(endpoints.QR_ISSUE), what="申请二维码")
        data = payload.get("data") or {}
        key = str(data.get("qrcode_key") or "")
        url = str(data.get("url") or "")
        if not key or not url:
            raise RemoteError(Fault.PARSE, "二维码响应缺少必要字段")
        return QrTicket(key=key, url=url)

    def poll(self, ticket: QrTicket) -> QrProgress:
        payload = self._transport.fetch(endpoints.QR_CHECK, query={"qrcode_key": ticket.key})
        code = codes.code_of(payload)
        data = payload.get("data") or {}
        stage = int(data.get("code") or 0)

        if code == _CONFIRMED and stage == _CONFIRMED:
            return QrProgress(state="confirmed", credential=self._assemble(data))
        if stage == _SCANNED:
            return QrProgress(state="scanned")
        if stage == _EXPIRED:
            return QrProgress(state="expired")
        if stage == _WAITING:
            return QrProgress(state="waiting")
        return QrProgress(state="rejected")

    def _assemble(self, data: dict[str, Any]) -> str:
        redirect = str(data.get("url") or "")
        query = parse_qs(urlsplit(redirect).query)

        found: dict[str, str] = {}
        for name in _WANTED:
            value = (query.get(name) or [""])[0]
            if value:
                found[name] = value

        if "SESSDATA" not in found:
            # 参数也可能不在跳转链接里，而是随响应头 Set-Cookie 下发到会话上。
            # 两条路都查，缺哪条都不该硬判失败。
            for name, value in self._transport.jar().items():
                if name in _WANTED and value and name not in found:
                    found[name] = value

        if "SESSDATA" not in found:
            raise RemoteError(Fault.PARSE, self._diagnose(redirect, query))

        pairs = [f"{name}={found[name]}" for name in _WANTED if name in found]
        fingerprint = self._fingerprint() or self._jar_fingerprint()
        if fingerprint:
            pairs.append(fingerprint)
        return "; ".join(pairs)

    def _diagnose(self, redirect: str, query: dict[str, list[str]]) -> str:
        """失败时只报告"收到的是什么形状"，不报告任何值。"""
        host = urlsplit(redirect).netloc or "空"
        fields = "/".join(sorted(query)) or "无"
        jar = "/".join(sorted(self._transport.jar())) or "无"
        return f"登录回调里没有 SESSDATA（跳转域名 {host}，链接字段 {fields}，会话 cookie {jar}）"

    def _jar_fingerprint(self) -> str:
        jar = self._transport.jar()
        parts = [f"{name}={jar[name]}" for name in ("buvid3", "buvid4") if jar.get(name)]
        return "; ".join(parts)

    def _fingerprint(self) -> str:
        """取 buvid3/buvid4。失败不影响登录，只是少一层伪装。"""
        try:
            payload = self._transport.fetch(endpoints.FINGERPRINT)
        except RemoteError:
            return ""
        data = payload.get("data") or {}
        parts = [f"{k}={data[k]}" for k in ("b_3", "b_4") if data.get(k)]
        if not parts:
            return ""
        return "; ".join(
            [
                parts[0].replace("b_3=", "buvid3="),
                *([parts[1].replace("b_4=", "buvid4=")] if len(parts) > 1 else []),
            ]
        )


def wait_for_scan(
    authenticator: QrAuthenticator,
    ticket: QrTicket,
    *,
    interval: float = 2.0,
    limit_seconds: float = 180.0,
    on_tick=None,  # type: ignore[no-untyped-def]
) -> QrProgress:
    """轮询到出结果为止。`on_tick` 用于把状态推给界面。"""
    deadline = time.monotonic() + limit_seconds
    last = QrProgress(state="waiting")
    while time.monotonic() < deadline:
        last = authenticator.poll(ticket)
        if on_tick is not None:
            on_tick(last)
        if last.done:
            return last
        time.sleep(interval)
    return QrProgress(state="expired")
