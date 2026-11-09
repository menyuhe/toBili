"""订阅源管理。

校验写在这里而不是路由里：CLI 和 HTTP 是同一个入口的两种外壳，规则只该有一份。
路由层负责把 `FeedRejected` 翻译成 400，不自己判断对错。
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from ..ports.repo import FeedRepo

SUPPORTED_KINDS = ("space", "following", "topic", "collection")

# 没有 target 的来源，用它当默认备注。
KIND_DEFAULT_LABEL = {"following": "关注流"}


class FeedRejected(ValueError):
    """输入不成立。消息是给用户看的，可以原样显示。"""


@dataclass(frozen=True, slots=True)
class FeedView:
    id: int
    kind: str
    target: str
    label: str
    enabled: bool
    cursor: str
    last_scan_at: str | None
    error: str = ""
    attempted_at: str | None = None
    shared: bool = False


class ManageFeeds:
    def __init__(self, feeds: FeedRepo) -> None:
        self._feeds = feeds

    def add(self, account_id: int, *, kind: str, target: str, label: str = "") -> int:
        cleaned_kind, cleaned_target = clean(kind, target)
        fallback = KIND_DEFAULT_LABEL.get(cleaned_kind) or cleaned_target
        return self._feeds.add(
            account_id,
            cleaned_kind,
            cleaned_target,
            label.strip() or fallback,
        )

    def list(self, account_id: int) -> list[FeedView]:
        return [to_view(row) for row in self._feeds.list_for(account_id)]

    def _own(self, account_id: int, feed_id: int) -> None:
        owned = {int(row["id"]) for row in self._feeds.list_for(account_id)}
        if feed_id not in owned:
            # 不区分"不存在"和"不属于你"，避免变成一个探测别人订阅源的工具。
            raise FeedRejected(f"订阅源 {feed_id} 不存在")

    @contextmanager
    def _edit(self, feed_id):
        guard = getattr(self._feeds, "edit_guard", None)
        try:
            with guard(feed_id) if guard else nullcontext():
                yield
        except ValueError as exc:
            raise FeedRejected(str(exc)) from exc

    def remove(self, account_id: int, feed_id: int) -> None:
        """停用。暂时不看它，但配置留着 —— 和 delete 是两件事。"""
        self._own(account_id, feed_id)
        with self._edit(feed_id):
            self._feeds.disable(feed_id)

    def enable(self, account_id: int, feed_id: int) -> None:
        self._own(account_id, feed_id)
        with self._edit(feed_id):
            self._feeds.enable(feed_id)

    def rename(self, account_id: int, feed_id: int, label: str) -> None:
        self._own(account_id, feed_id)
        cleaned = (label or "").strip()
        if len(cleaned) > 40:
            raise FeedRejected("备注最多 40 个字")
        # 清空备注时用 kind 的默认名兜底，和添加时的规则一致 ——
        # 否则界面上会出现一行没有名字的订阅源。
        row = next((r for r in self._feeds.list_for(account_id) if int(r["id"]) == feed_id), None)
        kind = str(row["kind"]) if row else ""
        target = str(row["target"]) if row else ""
        with self._edit(feed_id):
            self._feeds.rename(feed_id, cleaned or KIND_DEFAULT_LABEL.get(kind) or target)

    def delete(self, account_id: int, feed_id: int) -> None:
        """真的删掉。已经发现的活动不受影响，只是"从哪看到的"这条线断了。"""
        self._own(account_id, feed_id)
        with self._edit(feed_id):
            self._feeds.delete(feed_id)


def clean(kind: str, target: str) -> tuple[str, str]:
    value = (kind or "").strip().lower()
    if value not in SUPPORTED_KINDS:
        allowed = "/".join(SUPPORTED_KINDS)
        raise FeedRejected(f"暂不支持来源类型 {value or '（空）'}，目前只支持 {allowed}")

    if value == "following":
        # 关注流属于当前账号自己，没有"看谁的"这回事。target 一律清空，
        # 否则会因 UNIQUE(account_id, kind, target) 造出多条一模一样的源。
        return value, ""

    raw = (target or "").strip()
    if raw.startswith(("https://", "http://")):
        parsed = urlsplit(raw)
        if value == "topic":
            digits = (
                parse_qs(parsed.query).get("topic_id", [""])[0]
                if parsed.hostname == "www.bilibili.com"
                and parsed.path.rstrip("/") == "/v/topic/detail"
                else ""
            )
        else:
            digits = (
                parsed.path.strip("/").split("/")[0]
                if parsed.hostname == "space.bilibili.com"
                else ""
            )
    else:
        digits = raw.split(":")[-1].strip()
    if not digits.isascii() or not digits.isdigit() or len(digits) > 20 or int(digits) == 0:
        raise FeedRejected(
            "请输入有效的话题 ID 或话题链接"
            if value == "topic"
            else "请输入有效的 UP UID 或空间链接"
        )
    return value, str(int(digits))


def to_view(row: dict[str, object]) -> FeedView:
    last = row.get("last_scan_at")
    return FeedView(
        id=int(row["id"]),  # type: ignore[arg-type]
        kind=str(row.get("kind") or ""),
        target=str(row.get("target") or ""),
        label=str(row.get("label") or ""),
        enabled=int(row.get("enabled") or 0) == 1,  # type: ignore[arg-type]
        cursor=str(row.get("cursor") or ""),
        last_scan_at=str(last) if last else None,
        error=str(row.get("error") or ""),
        attempted_at=str(row.get("attempted_at") or "") or None,
        shared=row.get("kind") in ("topic", "collection"),
    )
