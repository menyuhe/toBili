"""请求头。

单独一个文件，因为"像不像一个浏览器"和"请求怎么发"是两件不同的事，混在一起
两边都改不利索。
"""

from __future__ import annotations

BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BASELINE = {
    "User-Agent": BROWSER,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": "https://www.bilibili.com",
}


def compose(*, cookie: str = "", referer: str = "") -> dict[str, str]:
    """合并出一次请求需要的头。没有 cookie 时不要塞空字符串，某些网关会因此判非法。"""
    merged = dict(BASELINE)
    if referer:
        merged["Referer"] = referer
    if cookie:
        merged["Cookie"] = cookie
    return merged
