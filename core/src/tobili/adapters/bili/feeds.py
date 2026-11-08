"""订阅源读取。

两种来源，入口形状一样：给一个游标，还一页数据和一个新游标。加来源不影响用例。

    space      某个 UP 的空间动态，target 是 UID
    following  我关注的人的动态流，target 为空

`following` 不是"更方便的 space"。关注了六百多人时，逐个扫空间是六百次请求，
每小时一轮就是不可接受的数量；关注流一次请求就覆盖全部，只是只能看到最近的。
这两者的取舍写在方案里，不是实现细节。
"""

from __future__ import annotations

from ...ports.models import FeedSlice
from . import codes, endpoints
from .http import Wire
from .parser import to_feed_item

SUPPORTED_KINDS = ("space", "following", "topic", "collection")


class SourceUnsupported(ValueError):
    pass


class DynamicFeed:
    def __init__(self, transport: Wire) -> None:
        self._transport = transport

    def scan(self, *, kind: str, target: str, cursor: str, limit: int) -> FeedSlice:
        if kind in ("topic", "collection"):
            from .public_sources import PublicSources

            return PublicSources(self._transport).scan(
                kind=kind, target=target, cursor=cursor, limit=limit
            )
        start = 0
        if cursor.startswith("page:"):
            _, index, cursor = cursor.split(":", 2)
            start = int(index)
        if kind == "space":
            page = self._space(target=target, cursor=cursor, limit=limit, start=start)
        elif kind == "following":
            page = self._following(cursor=cursor, limit=limit, start=start)
        else:
            raise SourceUnsupported(f"暂不支持的来源类型：{kind}")
        return page

    def _space(self, *, target: str, cursor: str, limit: int, start: int = 0) -> FeedSlice:
        payload = codes.ensure_ok(
            self._transport.fetch(
                endpoints.SPACE_FEED,
                query={
                    "host_mid": target,
                    "offset": cursor,
                    "type": "all",
                    "platform": "web",
                },
                referer=endpoints.SPACE_REFERER.format(uid=target),
            ),
            what="读取空间动态",
        )
        return _slice(payload, limit=limit, start=start, cursor=cursor)

    def _following(self, *, cursor: str, limit: int, start: int = 0) -> FeedSlice:
        payload = codes.ensure_ok(
            self._transport.fetch(
                endpoints.FOLLOWING_FEED,
                query={
                    "type": "all",
                    "page": 1,
                    "offset": cursor,
                    "platform": "web",
                },
                referer=endpoints.FOLLOWING_REFERER,
            ),
            what="读取关注动态",
        )
        return _slice(payload, limit=limit, start=start, cursor=cursor)


def _slice(payload: dict, *, limit: int, start: int = 0, cursor: str = "") -> FeedSlice:
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("动态接口未返回有效列表")
    raw_items = data["items"]
    items = []
    end = start + max(1, limit)
    for raw in raw_items[start:end]:
        if not isinstance(raw, dict):
            continue
        parsed = to_feed_item(raw)
        if parsed is not None:
            items.append(parsed)

    if end < len(raw_items):
        return FeedSlice(items=tuple(items), cursor=f"page:{end}:{cursor}", exhausted=False)
    next_cursor = str(data.get("offset") or "")
    has_more = bool(data.get("has_more"))
    return FeedSlice(
        items=tuple(items),
        cursor=next_cursor,
        # 上游可以返回未满的一页，只要有后续游标仍可翻页。
        exhausted=not (has_more and next_cursor),
    )
