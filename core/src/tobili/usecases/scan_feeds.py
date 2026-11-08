"""扫描订阅源，把动态变成活动记录。

两段式：先用正文做粗筛（`looks_like_campaign`），只有通过的才值得再请求一次详情。
反过来做的话，一个关注了 500 人的账号每次扫描要发上千个请求，风控第二天就来了。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta, timezone

from ..domain.campaign import Campaign
from ..domain.extract import (
    confidence_of,
    deadline_from,
    kind_from,
    looks_like_campaign,
)
from ..ports.bili import Gateway
from ..ports.collab import Clock
from ..ports.models import FeedItem
from ..ports.repo import FeedRepo
from ..runtime.redact import safe_exception
from .scan_model import facts_for


@dataclass(frozen=True, slots=True)
class ScanReport:
    feeds: int = 0
    scanned: int = 0
    matched: int = 0
    unique: int = 0
    sources: int = 0
    problems: tuple[str, ...] = field(default=())
    model_calls: int = 0
    model_cache_hits: int = 0
    model_extracted: int = 0

    @property
    def duplicates(self) -> int:
        """同一活动被多个源看到的次数。数字越大说明关注面越重叠。"""
        return max(0, self.matched - self.unique)

    @property
    def ok(self) -> bool:
        return not self.problems


class ScanFeeds:
    def __init__(
        self, *, gateway: Gateway, feeds: FeedRepo, campaigns, clock: Clock, model=None
    ) -> None:
        self._gateway = gateway
        self._feeds = feeds
        self._campaigns = campaigns
        self._clock = clock
        self._model = model

    def run(
        self,
        account_id: int,
        *,
        per_feed: int = 20,
        pages: int = 1,
        history: bool = False,
        feed_id: int | None = None,
        feed_ids: list[int] | None = None,
    ) -> ScanReport:
        now = self._clock.now()
        sources = [
            f
            for f in self._feeds.list_for(account_id)
            if (int(f["id"]) == feed_id if feed_id is not None else int(f.get("enabled") or 0) == 1)
        ]
        if feed_ids is not None:
            sources = [f for f in sources if int(f["id"]) in feed_ids]
        if not sources:
            return ScanReport(problems=("还没有配置订阅源",))

        scanned = matched = sources_recorded = 0
        seen: set[str] = set()
        problems: list[str] = []
        depth = max(1, pages)

        for source in sources:
            feed_id = int(source["id"])
            label = str(source.get("label") or source.get("target") or "")
            saved = str(source.get("cursor") or "")
            cursor = saved if history else ""
            collection = source["kind"] == "collection"
            tracked = source["kind"] in ("collection", "topic")
            source_problem = ""
            progress = {"latest": "", "history": ""}
            if tracked:
                if saved.startswith("v2:"):
                    try:
                        progress.update(json.loads(saved[3:]))
                    except (ValueError, TypeError):
                        problems.append(f"{label}：扫描进度损坏，请重新添加该订阅")
                        continue
                elif saved:
                    progress["history"] = saved
                cursor = progress["history" if history else "latest"]

            for _ in range(depth):
                try:
                    page = self._gateway.scan(
                        kind=str(source["kind"]),
                        target=str(source["target"]),
                        cursor=cursor,
                        limit=per_feed,
                    )
                except Exception as exc:  # 单个源坏掉不该中断整轮
                    source_problem = safe_exception(exc)
                    problems.append(f"{label}：{source_problem}")
                    break

                scanned += page.count
                for item in page.items:
                    campaign = self._build(item, now=now, label=label)
                    if campaign is None:
                        continue
                    matched += 1
                    seen.add(campaign.dyn_id)
                    campaign_id = self._campaigns.upsert(campaign, source_ref=label)
                    if self._campaigns.attach_source(
                        campaign_id, feed_id=feed_id, label=label, now=now
                    ):
                        sources_recorded += 1

                # 游标没前进就停：否则会在同一页上反复请求，看起来像在翻页。
                next_cursor = page.cursor if not page.exhausted else ""
                if tracked:
                    if history:
                        progress["history"] = next_cursor
                    else:
                        # Finish the current article across bounded rounds, then check
                        # the newest article again. Historical traversal is separate.
                        if collection:
                            finished = page.exhausted or next_cursor.endswith(":::0")
                        else:
                            if not cursor and page.items:
                                progress["next_head"] = page.items[0].dyn_id
                            previous = progress.get("head", "")
                            finished = (
                                page.exhausted
                                or (
                                    bool(previous) and any(x.dyn_id == previous for x in page.items)
                                )
                                or (not previous and not next_cursor.startswith("page:"))
                            )
                            if finished:
                                progress["head"] = progress.get("next_head", "")
                        if page.problem:
                            finished = False
                        progress["latest"] = "" if finished else next_cursor
                        if not progress["history"]:
                            progress["history"] = next_cursor
                    self._feeds.save_cursor(feed_id, "v2:" + json.dumps(progress), now=now)
                    if page.problem:
                        source_problem = page.problem
                        problems.append(f"{label}：{page.problem}")
                        break
                    if not history and finished:
                        break
                elif history or not saved:
                    self._feeds.save_cursor(feed_id, next_cursor, now=now)
                else:
                    self._feeds.save_cursor(feed_id, saved, now=now)
                if not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor

            record = getattr(self._feeds, "record_status", None)
            if record:
                record(feed_id, now=self._clock.now(), error=source_problem)

        if self._model and self._model.problem:
            problems.append(self._model.problem)
        return ScanReport(
            feeds=len(sources),
            scanned=scanned,
            matched=matched,
            unique=len(seen),
            sources=sources_recorded,
            problems=tuple(problems),
            model_calls=getattr(self._model, "calls", 0),
            model_cache_hits=getattr(self._model, "hits", 0),
            model_extracted=getattr(self._model, "extracted", 0),
        )

    def _build(self, item: FeedItem, *, now, label: str) -> Campaign | None:
        if not looks_like_campaign(item.text):
            return None
        reference = (item.published_at or now).astimezone(timezone(timedelta(hours=8)))
        kind, deadline, reward = facts_for(item, reference, self._model)
        if kind.value == "unknown":
            return None
        original_kind = kind_from(item.text)
        original_deadline = deadline_from(
            item.text, now=reference, allow_relative=item.published_at is not None
        )
        return Campaign(
            dyn_id=item.dyn_id,
            up_uid=item.up_uid,
            title=_headline(item.text),
            kind=kind,
            up_name=item.up_name,
            reward=reward,
            deadline=deadline,
            confidence=confidence_of(item.text, original_kind, original_deadline)
            if kind == original_kind
            else 0.0,
            source_ref=label,
            raw_ref=item.raw_ref,
            seen_at=now,
            published_at=item.published_at,
            body_text=item.text,
        )


def _headline(text: str, limit: int = 80) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    return text.strip()[:limit]
