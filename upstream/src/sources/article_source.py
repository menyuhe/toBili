"""Shared read-only collection adapter for the newly verified article sources."""
from dataclasses import dataclass
from pathlib import Path
import time

from src.bilibili_client import BilibiliClient
from src.sources.common import CheckResult, extract_opus_links_with_hints, load_previous_output, save_result
from src.state_store import DATA_DIR, get_last_container


@dataclass(frozen=True)
class ArticleSource:
    source_id: str
    mid: int

    @property
    def output_path(self) -> Path:
        return DATA_DIR / "output" / f"ds{self.source_id.split('-')[1]}_latest.json"

    def check_update(self, *, force: bool = False) -> CheckResult:
        with BilibiliClient() as client:
            latest = client.get_latest_article(self.mid)
            cv_id = str(latest["id"])
            container_url = f"https://www.bilibili.com/read/cv{cv_id}"
            previous = get_last_container(self.source_id)
            cached = load_previous_output(self.output_path) if previous == container_url else None
            if not force and cached:
                return CheckResult(
                    source_id=self.source_id, updated=False, container_url=container_url,
                    container_id=cv_id, cv_id=cv_id, title=latest.get("title", ""),
                    published_at=int(latest.get("publish_time") or 0),
                    previous_container_url=previous, checked_at=int(time.time()),
                    activity_links=cached.get("activity_links") or [],
                    link_hints=cached.get("link_hints") or {},
                )
            detail = client.get_article_detail(int(cv_id))
            opus_id = str((detail.get("opus") or {}).get("opus_id") or "")
            links, hints = extract_opus_links_with_hints(detail, container_opus_id=opus_id)
            return CheckResult(
                source_id=self.source_id, updated=True, container_url=container_url,
                container_id=cv_id, cv_id=cv_id, title=detail.get("title") or latest.get("title", ""),
                published_at=int(detail.get("publish_time") or latest.get("publish_time") or 0),
                previous_container_url=previous, checked_at=int(time.time()),
                activity_links=links, link_hints=hints,
            )

    def save_result(self, result: CheckResult) -> Path | None:
        return save_result(self.output_path, result)


NEW_ARTICLE_SOURCES = (
    ArticleSource("DS-8", 6435997),
    ArticleSource("DS-9", 3546589855484661),
    ArticleSource("DS-10", 14062306),
)
