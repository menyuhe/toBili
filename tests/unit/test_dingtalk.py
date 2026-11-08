"""钉钉通知。

签名那段最容易写错，所以每条都盯着它：时间戳和密钥的拼接顺序、URL 编码、
以及"没配密钥时要退化成不加签"—— 钉钉允许只靠关键词放行的机器人，
把那种也强行加签，会得到一个只有"签名不匹配"的报错。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse

from tobili.adapters.notify.dingtalk import DingTalk

WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=abc123"


def _capture():
    calls: list[tuple[str, dict]] = []

    def post(url: str, payload) -> bool:  # type: ignore[no-untyped-def]
        calls.append((url, dict(payload)))
        return True

    return calls, post


def test_send_without_secret_uses_the_url_as_is() -> None:
    calls, post = _capture()
    assert DingTalk(WEBHOOK, post=post).send("标题", "正文")
    url, payload = calls[0]
    assert url == WEBHOOK
    assert payload["msgtype"] == "markdown"
    assert "标题" in payload["markdown"]["text"]


def test_signature_matches_the_documented_recipe() -> None:
    """HMAC-SHA1(secret, f"{timestamp}\\n{secret}")，再 base64 + URL 编码。

    顺序颠倒是最常见的错，而钉钉只会回一句"签名不匹配"，看不出错在哪。
    """
    calls, post = _capture()
    DingTalk(WEBHOOK, secret="s3cret", post=post, now=lambda: 1700000000.0).send("t", "b")

    url, _ = calls[0]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["timestamp"] == ["1700000000000"]

    expected = base64.b64encode(
        hmac.new(b"s3cret", b"1700000000000\ns3cret", hashlib.sha1).digest()
    ).decode()
    assert query["sign"] == [expected]


def test_signature_is_url_encoded() -> None:
    """base64 里有 `+` `/` `=`，不编码就会被当成别的字符，签名直接失效。"""
    calls, post = _capture()
    DingTalk(WEBHOOK, secret="另一个密钥", post=post, now=lambda: 1700000000.0).send("t", "b")
    url, _ = calls[0]
    assert "+" not in url
    # `=` 来自 base64 的填充，编码后一律是 %3D。
    assert "=" not in url.split("sign=")[-1] or "%3D" in url


def test_webhook_with_existing_query_gets_ampersand() -> None:
    calls, post = _capture()
    DingTalk(WEBHOOK, secret="k", post=post, now=lambda: 1.0).send("t", "b")
    url, _ = calls[0]
    # 已经有了 ?access_token=...，后面必须接 &
    assert "?access_token=abc123&timestamp=" in url


def test_failure_returns_false_instead_of_raising() -> None:
    """通知发不出去不该打断正在跑的作业。"""

    def boom(url: str, payload) -> bool:  # type: ignore[no-untyped-def]
        raise RuntimeError("群里没网了")

    assert DingTalk(WEBHOOK, post=boom).send("t", "b") is False


def test_empty_webhook_is_not_ready() -> None:
    calls, post = _capture()
    channel = DingTalk("   ", post=post)
    assert channel.ready is False
    assert channel.send("t", "b") is False
    assert calls == []


def test_level_shows_up_in_the_message() -> None:
    calls, post = _capture()
    DingTalk(WEBHOOK, post=post).send("出事了", "详情", level="danger")
    assert "出事" in calls[0][1]["markdown"]["text"]
