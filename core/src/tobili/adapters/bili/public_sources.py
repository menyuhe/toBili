"""Public topic cards and links in an author's article; independent read adapters."""

import hashlib
import json
import re
import time
from html import unescape
from html.parser import HTMLParser

from ...domain.errors import Fault, RemoteError
from ...ports.models import FeedSlice
from . import codes, endpoints
from .parser import to_feed_item

TOPIC = "https://app.bilibili.com/x/topic/web/details/cards"
ARTICLES = endpoints.API + "/x/space/article"
ARTICLE = endpoints.API + "/x/article/view"
OPUS = endpoints.API + "/x/polymer/web-dynamic/v1/opus/detail"
LINK = re.compile(
    r"https?://(?:www\.bilibili\.com/opus/|t\.bilibili\.com/|m\.bilibili\.com/dynamic/)([0-9]{1,24})(?![0-9])"
)


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(content_text(x) for x in value)
    if isinstance(value, dict):
        return "\n".join(
            content_text(v)
            for k, v in value.items()
            if k
            in (
                "text",
                "words",
                "word",
                "rich",
                "show_text",
                "nodes",
                "paragraphs",
                "heading",
                "title",
                "content",
            )
        )
    return ""


def opus_item(raw):
    modules = raw.get("modules")
    if isinstance(modules, dict):
        return raw
    if not isinstance(modules, list):
        raise ValueError("Opus 正文结构变化")
    author, pieces, merged, cards = {}, [], {}, []
    for module in modules:
        if not isinstance(module,dict):continue
        merged.update(module)
        if module.get("module_author"):
            author = module["module_author"]
        for key in ("module_title", "module_content"):
            if module.get(key):
                pieces.append(content_text(module[key]))
        from .official_draw import objects
        for node in objects(module.get('module_content',{})):
            for name in ('reserve','upower_lottery'):
                if isinstance(node.get(name),dict):cards.append((name,node[name]))
    dynamic=dict(merged.get('module_dynamic') or {})
    dynamic['desc']={'text':'\n'.join(pieces)}
    for name,card in cards:dynamic['additional']={name:card}
    merged.update(module_author=author,module_dynamic=dynamic)
    return {**raw,'modules':merged}


NAV_LINK = re.compile(r"往期|上期|传送门|合集|汇总|个人主页")


class BodyLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.href = None
        self.words = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href", "")
            self.words = []

    def handle_data(self, text):
        if self.href is not None:
            self.words.append(text)
        else:
            self.links.extend(LINK.findall(text))

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            if not NAV_LINK.search("".join(self.words)):
                self.links.extend(LINK.findall(self.href))
            self.href = None


def activity_links(article):
    opus = article.get("opus") or {}
    body = opus.get("content") or {}
    paragraphs = body.get("paragraphs") if isinstance(body, dict) else None
    paragraphs = paragraphs or article.get("paragraphs") or []
    links = []
    if paragraphs:
        for paragraph in paragraphs:
            # Navigation to previous collections is not a lottery entry.
            if NAV_LINK.search(content_text(paragraph)):
                continue
            links.extend(
                LINK.findall(
                    unescape(json.dumps(paragraph, ensure_ascii=False).replace("\\/", "/"))
                )
            )
    else:
        content = article.get("content") or (body if isinstance(body, str) else "")
        parser = BodyLinks()
        parser.feed(content)
        links.extend(parser.links)
    return list(dict.fromkeys(x for x in links if x != str(opus.get("opus_id") or "")))


class PublicSources:
    def __init__(self, wire):
        self.wire = wire

    def read(self, url, query, referer):
        cache = getattr(self.wire, "source_cache", None)
        cache_key = (
            "body:" + hashlib.sha256((url + json.dumps(query, sort_keys=True)).encode()).hexdigest()
        )
        if cache and (cached := cache.get(cache_key)) is not None:
            return cached
        holds = getattr(self.wire, "source_holds", {})
        hold = (cache.get("hold:" + url) if cache else holds.get(url)) or {}
        remaining = hold.get("until", 0) - time.time()
        if remaining > 0:
            raise RemoteError(Fault.RISK, f"来源接口冷却中，{int(remaining) // 60 + 1} 分钟后重试")
        pace = getattr(self.wire, "_pace", None)
        if pace:
            pace()
        try:
            reply = codes.ensure_ok(
                self.wire.fetch(url, query=query, referer=referer), what="读取订阅源"
            )
        except RemoteError as exc:
            if exc.code in (412, 429, -412, -352, -509, -799) or exc.fault.suggests_backoff:
                strikes = min(hold.get("strikes", 0) + 1, 4)
                seconds = min(600 * 2 ** (strikes - 1), 3600)
                hold = {"until": time.time() + seconds, "strikes": strikes}
                holds[url] = hold
                self.wire.source_holds = holds
                if cache:
                    cache.put("hold:" + url, hold, 86400)
                raise RemoteError(
                    Fault.RISK,
                    f"B 站限制读取，{seconds // 60} 分钟后重试；已保留进度",
                    code=exc.code,
                ) from exc
            raise
        data = reply.get("data")
        if not isinstance(data, dict):
            raise ValueError("来源返回结构变化，已保留扫描进度")
        ttl = 21600 if url in (OPUS, endpoints.DYN_DETAIL) else 300 if url == ARTICLE else 60
        if cache:
            cache.put(cache_key, data, ttl)
            if hold:
                cache.put("hold:" + url, {}, 1)
        holds.pop(url, None)
        return data

    def detail(self, dyn):
        # Opus is the normal read path for article-linked posts. Discovery cache
        # is never consulted by participation or cleanup preflight.
        try:
            data = self.read(
                OPUS,
                {"id": dyn, "features": "htmlNewStyle,ugcDelete,editable,opusPrivateVisible"},
                endpoints.dyn_referer(dyn),
            )
            raw = data.get("item")
            if isinstance(raw, dict) and raw:
                item = to_feed_item(opus_item(raw))
                if item and item.text.strip():
                    return item
        except RemoteError as exc:
            if exc.fault in (Fault.RISK, Fault.AUTH) or exc.code in codes.GONE_CODES:
                raise
            if exc.code not in (404, -404):
                raise
        data = self.read(
            endpoints.DYN_DETAIL,
            {"id": dyn, "features": "itemOpusStyle"},
            endpoints.dyn_referer(dyn),
        )
        raw = data.get("item")
        item = to_feed_item(raw) if isinstance(raw, dict) else None
        if item is None or not item.text.strip():
            raise ValueError("活动正文为空，保留进度")
        return item

    def scan(self, *, kind, target, cursor, limit):
        if kind == "topic":
            return self.topic(target, cursor, limit)
        return self.collection(target, cursor, min(10, max(1, limit)))

    def topic(self, target, cursor, limit):
        start = 0
        if cursor.startswith("page:"):
            _, index, cursor = cursor.split(":", 2)
            start = int(index)
        data = self.read(
            TOPIC,
            {"topic_id": target, "sort_by": 3, "offset": cursor, "page_size": 20, "source": "H5"},
            f"https://www.bilibili.com/v/topic/detail?topic_id={target}",
        )
        page = data.get("topic_card_list")
        if not isinstance(page, dict) or not isinstance(page.get("items"), list):
            raise ValueError("话题未返回动态列表，已保留扫描进度")
        rows = page["items"]
        offset = str(page.get("offset") or "")
        more = bool(page.get("has_more"))
        if more and (not offset or offset == cursor or not rows):
            raise ValueError("话题分页未前进，已保留扫描进度")
        end = start + max(1, limit)
        items = []
        for row in rows[start:end]:
            card = row.get("dynamic_card_item") if isinstance(row, dict) else None
            if card is None:  # Topic advertisements and non-dynamic cards.
                continue
            item = to_feed_item(card)
            if item is None:
                raise ValueError("话题动态缺少 ID，已保留扫描进度")
            items.append(item)
        partial = end < len(rows)
        return FeedSlice(
            tuple(items), f"page:{end}:{cursor}" if partial else offset, not (partial or more)
        )

    def collection(self, target, cursor, limit):
        pn, cv, digest, start = 1, "", "", 0
        if cursor:
            marker, page, cv, digest, index = cursor.split(":", 4)
            if marker != "article" or not page.isdigit() or not index.isdigit():
                raise ValueError("合集扫描进度无效")
            pn, start = max(1, int(page)), int(index)
        if not cv:
            data = self.read(
                ARTICLES,
                {"mid": target, "pn": pn, "ps": 1, "sort": "publish_time"},
                f"https://space.bilibili.com/{target}/upload/opus",
            )
            rows = data.get("articles")
            if not isinstance(rows, list):
                raise ValueError("合集未返回专栏列表")
            if not rows:
                return FeedSlice()
            cv = str(rows[0]["id"])
        if not cv.isdigit():
            raise ValueError("专栏 ID 无效")
        article = self.read(ARTICLE, {"id": cv}, f"https://www.bilibili.com/read/cv{cv}")
        links = activity_links(article)
        current = hashlib.sha256(",".join(links).encode()).hexdigest()[:16]
        # Edits may reorder links: restart that article instead of skipping new links.
        if digest and digest != current:
            start = 0
        items = []
        for index, dyn in enumerate(links[start : start + limit], start):
            try:
                item = self.detail(dyn)
            except RemoteError as exc:
                if exc.code in codes.GONE_CODES:
                    continue
                return FeedSlice(
                    tuple(items), f"article:{pn}:{cv}:{current}:{index}", False, str(exc)
                )
            except (ValueError, KeyError) as exc:
                return FeedSlice(
                    tuple(items), f"article:{pn}:{cv}:{current}:{index}", False, str(exc)
                )
            items.append(item)
        end = start + limit
        next_cursor = (
            f"article:{pn}:{cv}:{current}:{end}" if end < len(links) else f"article:{pn + 1}:::0"
        )
        return FeedSlice(tuple(items), next_cursor, False)
