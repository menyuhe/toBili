"""wbi 签名。

B 站从 2023 年起把一批接口换成了 `wbi` 签名，评论列表就在其中。做法是：从 `nav`
接口拿两张图的文件名拼成一个 64 字符的串，按固定表重排取前 32 位当密钥，再把
请求参数按字典序拼上时间戳算 MD5。

没有签名时接口回的是 `-403 访问权限不足` —— 注意不是 `-404`。这个区别重要：
`-404 啥都木有` 是真的没有，`-403` 是"你签名不对"。两个都当成"没有评论"的话，
表现是"功能一直静默失败"，而人只会以为这些动态没人评论。

密钥按天轮换，所以缓存到当天结束，并允许一次强制刷新（签名过期时会回 -403，
这时重取密钥再试一次，仍失败才算真的失败）。
"""

from __future__ import annotations

import hashlib
import time
from urllib.parse import urlencode

from . import endpoints

# 官方前端里的定值。它的作用是把 64 位打乱成 32 位，算法本身没有秘密 ——
# 谁都能在网页源码里看到这张表，改它没有意义。
_MIXIN_TABLE = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)  # fmt: skip

# 参数里这几个字符必须去掉，否则签名对不上（官方实现就是这么过滤的）。
_BAD_CHARS = "!'()*"


def mixin_key(img_key: str, sub_key: str) -> str:
    raw = (img_key + sub_key)[:64]
    return "".join(raw[i] for i in _MIXIN_TABLE if i < len(raw))[:32]


def _key_from_url(url: str) -> str:
    return url.rsplit("/", 1)[-1].split(".")[0]


class WbiSigner:
    """按天缓存密钥。线程安全靠"最坏情况是重复取一次"，不值得为它加锁。"""

    def __init__(self, transport) -> None:  # type: ignore[no-untyped-def]
        self._transport = transport
        self._key = ""
        self._day = ""

    def _ensure(self, *, force: bool = False) -> str:
        today = time.strftime("%Y%m%d")
        if self._key and self._day == today and not force:
            return self._key
        payload = self._transport.fetch(endpoints.NAV)
        data = payload.get("data") or {}
        wbi = data.get("wbi_img") or {}
        img = _key_from_url(str(wbi.get("img_url") or ""))
        sub = _key_from_url(str(wbi.get("sub_url") or ""))
        if not img or not sub:
            # 没登录时 nav 也可能不给 wbi_img。没有密钥就只能不签名地请求，
            # 让上层拿到 -403，而不是在这里抛一个看不出原因的错误。
            return ""
        self._key = mixin_key(img, sub)
        self._day = today
        return self._key

    def sign(self, query: dict[str, object], *, force: bool = False) -> dict[str, object]:
        """补齐 `wts` 与 `w_rid`，返回新的参数字典。没密钥时原样返回。"""
        key = self._ensure(force=force)
        if not key:
            return dict(query)
        params: dict[str, object] = {
            k: "".join(c for c in str(v) if c not in _BAD_CHARS) for k, v in query.items()
        }
        params["wts"] = int(time.time())
        ordered = dict(sorted(params.items()))
        digest = hashlib.md5((urlencode(ordered) + key).encode()).hexdigest()
        ordered["w_rid"] = digest
        return ordered
