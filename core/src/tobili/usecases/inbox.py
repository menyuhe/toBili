"""收件箱：把未读消息拉下来，标出"这条可能在说中奖"。

三件事在这里定：

1. **一条读不到不牵连其他。** 小类接口经常单独抽风，整张清单变空会被误读成
   "没有新消息"，那比少显示几条危险得多。
2. **"已看"是本机的，不是 B 站的。** 详见 `domain.inbox` 和 schema 注释。
   界面上必须写清楚，否则人会以为红点清了、结果没清。
3. **系统通知分两条路。** 独立的通知列表接口全部 404，所以只有条数可用；
   但官方账号的系统卡片在私信会话里能读到正文，那部分按 `system` 归类如实显示。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from ..domain.inbox import Message, annotate, digest, notify_line
from ..ports.bili import Reader
from ..ports.collab import Clock
from ..ports.repo import InboxRepo
from ..runtime.redact import safe_exception


@dataclass(frozen=True, slots=True)
class InboxView:
    items: tuple[Message, ...] = field(default=())
    system_count: int = 0
    """系统通知的条数。**只有数字**，没有内容可看。"""

    problems: tuple[str, ...] = field(default=())
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def prize_line(self) -> str:
        return notify_line(list(self.items))


class Inbox:
    def __init__(
        self,
        *,
        gateway: Reader,
        seen: InboxRepo,
        clock: Clock | None = None,
    ) -> None:
        self._gateway = gateway
        self._seen = seen
        self._clock = clock

    def fetch(self, account_id: int, *, pages: int = 1, limit: int = 60) -> InboxView:
        """拉一次收件箱。

        `limit` 是上限而不是页大小 —— 界面一次不会展示几百条，而多抓只是在
        浪费一次可能被限流的请求。
        """
        problems: list[str] = []
        raw: list[Message] = []
        try:
            raw = self._gateway.inbox(pages=pages)
        except Exception as exc:
            problems.append(f"消息列表读取失败：{safe_exception(exc)}")

        system_count = 0
        stat = None
        try:
            stat = self._gateway.unread()
            system_count = int(stat.system)
        except Exception:
            # 未读计数读不到不影响清单本身，只是"系统通知"那一格显示不出来。
            problems.append("系统通知条数读取失败")

        known = self._seen.seen(account_id)
        irrelevant = self._seen.irrelevant(account_id)
        items: list[Message] = []
        for message in raw:
            marked = annotate(message)
            # Chat keys identify a conversation. Review only this exact message;
            # a future prize notification from the same sender must remain visible.
            review_key = hashlib.sha256(
                json.dumps(
                    [
                        message.key,
                        message.text,
                        message.context,
                        message.at.isoformat() if message.at else None,
                    ],
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            reviewed = review_key in irrelevant
            items.append(
                Message(
                    kind=marked.kind,
                    key=marked.key,
                    who=marked.who,
                    text=marked.text,
                    at=marked.at,
                    # 本机看过就不再算未读。远端那条一直在，红点也一直在，
                    # 所以"要不要再提醒"这件事只能由这里决定。
                    unread=marked.unread and marked.key not in known,
                    link=marked.link,
                    prize=marked.prize and not reviewed,
                    reason="人工标记：与抽奖无关" if reviewed else marked.reason,
                    context=marked.context,
                    automatic=marked.automatic,
                    sender_uid=marked.sender_uid,
                    review_key=review_key,
                    reviewed=reviewed,
                )
            )
        items.sort(key=lambda item: _order(item.at), reverse=True)

        # 拿未读计数对一遍账。私信列表实测会偶尔整批返回空（被限流），
        # 那时清单上一条私信都没有，而计数明明写着十几条 —— 不点破的话，
        # 界面显示"没有新消息"，人就真的以为没有。
        if stat is not None:
            for label, count, kind in (
                ("私信", int(stat.chat), "chat"),
                ("回复", int(stat.reply), "reply"),
                ("@我", int(stat.at), "at"),
            ):
                if count > 0 and not any(item.kind == kind for item in items):
                    problems.append(
                        f"{label}显示有 {count} 条未读，但列表没读出来（可能被限流，过一会儿再刷新）"
                    )

        return InboxView(
            items=tuple(items[: max(1, limit)]),
            system_count=system_count,
            problems=tuple(problems),
            counts=digest(items),
        )

    def mark_seen(self, account_id: int, keys: list[str]) -> int:
        """记下"这些我看过了"。**本机记录，不影响 B 站的红点。**"""
        return self._seen.mark(account_id, list(keys))

    def forget_seen(self, account_id: int) -> None:
        self._seen.forget(account_id)


def _order(at: datetime | None) -> float:
    return at.timestamp() if at is not None else 0.0
