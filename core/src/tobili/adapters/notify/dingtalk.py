"""钉钉群机器人。

只做一件事：往一个 webhook 发 markdown。做成这个形状是因为它要满足
`ports.collab.Notifier` —— 用例只要求"发一条通知，失败别抛"，换企业微信、
飞书、Bark 都只是换一个实现，用例一行不用改。

**失败返回 False，不抛。** 通知发不出去不该让正在跑的作业停下来：
转发到一半因为群里没网就整轮失败，是本末倒置。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse
from collections.abc import Callable, Mapping

import httpx

_KIND = {"info": "通知", "ok": "完成", "warn": "注意", "danger": "出事"}


class DingTalk:
    def __init__(
        self,
        webhook: str,
        *,
        secret: str = "",
        timeout: float = 8.0,
        post: Callable[[str, Mapping[str, object]], bool] | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        """`secret` 留空表示机器人没开加签（靠关键词放行），这也是钉钉允许的配置。

        `post` 是注入点：签名对不对、payload 长什么样，可以用一个只记录参数的
        假函数测，不必真的往群里发一条。
        """
        self._webhook = (webhook or "").strip()
        self._secret = (secret or "").strip()
        self._timeout = timeout
        self._post = post or self._http_post
        self._now = now

    @property
    def ready(self) -> bool:
        return bool(self._webhook)

    def send(self, title: str, body: str, *, level: str = "info") -> bool:
        if not self.ready:
            return False
        url = self._signed_url()
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                # 前面挂个小标签，群里一眼能分出是通知还是出事。
                "text": f"### {title}\n\n{body}\n\n---\n**{_KIND.get(level, '通知')}**",
            },
        }
        try:
            return bool(self._post(url, payload))
        except Exception:
            # 见模块注释：通知失败不打断主流程。
            return False

    def _signed_url(self) -> str:
        if not self._secret:
            return self._webhook
        stamp = str(int(self._now() * 1000))
        # 钉钉的规矩：把时间戳和密钥用换行拼起来做 HmacSHA1，再 base64。
        # 顺序颠倒（先 base64 再拼）是最常见的错，报错信息只有一句"签名不匹配"。
        digest = hmac.new(
            self._secret.encode("utf-8"),
            f"{stamp}\n{self._secret}".encode(),
            hashlib.sha1,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(digest))
        joiner = "&" if "?" in self._webhook else "?"
        return f"{self._webhook}{joiner}timestamp={stamp}&sign={sign}"

    def _http_post(self, url: str, payload: Mapping[str, object]) -> bool:
        with httpx.Client(timeout=self._timeout) as client:
            reply = client.post(url, json=payload)
        if reply.status_code >= 400:
            return False
        body = reply.json() if reply.content else {}
        return int(body.get("errcode") or 0) == 0
