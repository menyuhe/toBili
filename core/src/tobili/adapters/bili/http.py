"""线路层：一次请求怎么发出去。

设计上把"能重发的"和"不能重发的"做成两个不同的方法名，而不是一个布尔参数。
布尔参数会被忘掉，方法名不会。

    fetch(...)        读。失败可以再试，最多 `attempts` 次。
    submit(...)       写。**只发一次**。没有响应就抛 `AmbiguousWrite`。

`AmbiguousWrite` 是这里最重要的输出：它表示"我不知道结果"，而不是"失败了"。
这两者混为一谈，就会产生重复转发。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from ...domain.errors import AmbiguousWrite, Fault, RemoteError
from .headers import compose


def _label(url: str) -> str:
    """日志里只出现路径。查询串可能带 csrf，落盘就是泄露。"""
    return urlsplit(url).path or url


class Wire:
    def __init__(
        self,
        cookie: str = "",
        *,
        patience: float = 12.0,
        attempts: int = 2,
        step: float = 0.35,
        gap: float = 1.5,
    ) -> None:
        self._cookie = cookie.strip()
        self._attempts = max(0, attempts)
        self._step = step
        self._gap = gap
        self._stamped = 0.0
        self._pace_lock = threading.Lock()
        self._session = httpx.Client(timeout=patience, follow_redirects=False)

    # ---- 生命周期 ----
    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> Wire:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---- 凭据 ----
    @property
    def cookie(self) -> str:
        return self._cookie

    def pick(self, name: str) -> str:
        for chunk in self._cookie.split(";"):
            key, _, value = chunk.strip().partition("=")
            if key == name:
                return value
        return ""

    @property
    def csrf(self) -> str:
        return self.pick("bili_jct")

    def jar(self) -> dict[str, str]:
        """会话里已经收到的 cookie（来自 Set-Cookie）。

        扫码登录有两种下发方式：把参数拼在跳转链接里，或者用 Set-Cookie 直接种在
        会话上。只认前一种，接口一改就登不进去，而且报错会说"链接里没有 SESSDATA"，
        看起来像上游坏了。
        """
        return {cookie.name: cookie.value for cookie in self._session.cookies.jar}

    def _heads(self, referer: str) -> dict[str, str]:
        return compose(cookie=self._cookie, referer=referer)

    # ---- 读 ----
    def fetch(
        self,
        url: str,
        *,
        query: Mapping[str, Any] | None = None,
        referer: str = "",
    ) -> dict[str, Any]:
        trouble: RemoteError | None = None
        for turn in range(self._attempts + 1):
            self._pace()
            try:
                reply = self._session.get(url, params=query, headers=self._heads(referer))
            except httpx.TimeoutException as exc:
                trouble = RemoteError(Fault.TIMEOUT, f"读取超时 {_label(url)}")
                trouble.__cause__ = exc
            except httpx.HTTPError as exc:
                trouble = RemoteError(Fault.NETWORK, f"读取中断 {_label(url)}")
                trouble.__cause__ = exc
            else:
                if reply.status_code >= 500:
                    trouble = RemoteError(
                        Fault.NETWORK,
                        f"上游 {reply.status_code} {_label(url)}",
                        code=reply.status_code,
                    )
                elif reply.status_code in (412, 429):
                    raise RemoteError(
                        Fault.RISK if reply.status_code == 412 else Fault.RATE_LIMIT,
                        f"平台暂时限制请求 HTTP {reply.status_code} {_label(url)}",
                        code=reply.status_code,
                    )
                elif reply.status_code >= 400:
                    raise RemoteError(
                        Fault.REMOTE_BUSINESS,
                        f"HTTP {reply.status_code} {_label(url)}",
                        code=reply.status_code,
                    )
                else:
                    return _json_of(reply, url)

            if turn == self._attempts:
                raise trouble
            time.sleep(self._step * (turn + 1))
        raise trouble  # pragma: no cover

    # ---- 写 ----
    def submit(
        self,
        url: str,
        form: Mapping[str, Any],
        *,
        referer: str = "",
    ) -> dict[str, Any]:
        self._pace()
        try:
            reply = self._session.post(url, data=form, headers=self._heads(referer))
        except httpx.HTTPError as exc:
            raise AmbiguousWrite(f"提交结果未知 {_label(url)}") from exc
        return _write_json_of(reply, url)

    def submit_json(
        self,
        url: str,
        body: Mapping[str, Any],
        *,
        referer: str = "",
        query: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._pace()
        try:
            reply = self._session.post(url, json=body, params=query, headers=self._heads(referer))
        except httpx.HTTPError as exc:
            raise AmbiguousWrite(f"提交结果未知 {_label(url)}") from exc
        return _write_json_of(reply, url)

    def _pace(self) -> None:
        """Space request starts, including reads, retries and read-after-write checks."""
        with self._pace_lock:
            if self._stamped:
                waited = time.monotonic() - self._stamped
                if waited < self._gap:
                    time.sleep(self._gap - waited)
            self._stamped = time.monotonic()


def _write_json_of(reply: httpx.Response, url: str) -> dict[str, Any]:
    # The POST already left this process. An unreadable response is not evidence
    # of rejection, and must never permit an automatic repeat of that write.
    if reply.status_code in (412, 429):
        raise AmbiguousWrite(
            f"提交遇到平台限制，结果待核对 {_label(url)}",
            fault=Fault.RISK if reply.status_code == 412 else Fault.RATE_LIMIT,
        )
    try:
        body = _json_of(reply, url)
        code = body.get("code")
        if isinstance(code, bool) or not isinstance(code, (int, str)):
            raise ValueError("missing business code")
        int(code)
        return body
    except (RemoteError, ValueError) as exc:
        raise AmbiguousWrite(f"提交响应无效，结果待核对 {_label(url)}", fault=Fault.PARSE) from exc


def _json_of(reply: httpx.Response, url: str) -> dict[str, Any]:
    try:
        body = reply.json()
    except ValueError as exc:
        raise RemoteError(Fault.PARSE, f"响应不是 JSON {_label(url)}") from exc
    if not isinstance(body, dict):
        raise RemoteError(Fault.PARSE, f"响应结构异常 {_label(url)}")
    return body
