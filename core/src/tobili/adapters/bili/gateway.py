"""网关：把四个能力组装成一个门面。

用例只拿它，不关心底层分了几个对象。扫码用的是不带 cookie 的连接，读和写用带
cookie 的连接 —— 这两个连接不能混，否则扫码头会带着旧凭据发出去。
"""

from __future__ import annotations

from ...domain.action import Action, ActionKind
from ...domain.inbox import Message
from ...domain.probe import ProbeState
from ...domain.receipt import Receipt
from ...domain.tidy import ShareRef
from ...ports.models import (
    AccountProfile,
    CampaignDetail,
    FeedSlice,
    MessageStat,
    QrProgress,
    QrTicket,
    UserCandidate,
)
from .auth import QrAuthenticator
from .feeds import DynamicFeed
from .http import Wire
from .prober import BiliProber
from .reader import DynamicReader
from .writer import BiliWriter


class BiliGateway:
    def public_detail(self, dyn_id: str):
        from .public_sources import PublicSources

        return PublicSources(self._transport).detail(dyn_id)

    def __init__(self, transport: Wire, *, authenticator: QrAuthenticator | None = None) -> None:
        self._transport = transport
        self._reader = DynamicReader(transport)
        self._feed = DynamicFeed(transport)
        self._writer = BiliWriter(transport)
        self._prober = BiliProber(transport, self._reader)
        self._auth = authenticator

    # ---- Authenticator ----
    def begin(self) -> QrTicket:
        return self._authenticator().begin()

    def poll(self, ticket: QrTicket) -> QrProgress:
        return self._authenticator().poll(ticket)

    def _authenticator(self) -> QrAuthenticator:
        if self._auth is None:
            self._auth = QrAuthenticator()
        return self._auth

    # ---- Reader ----
    def profile(self) -> AccountProfile:
        return self._reader.profile()

    def scan(self, *, kind: str, target: str, cursor: str, limit: int) -> FeedSlice:
        return self._feed.scan(kind=kind, target=target, cursor=cursor, limit=limit)

    def detail(self, dyn_id: str) -> CampaignDetail:
        return self._reader.detail(dyn_id)

    def search_users(
        self, keyword: str, *, page: int = 1, limit: int = 20
    ) -> tuple[UserCandidate, ...]:
        return self._reader.search_users(keyword, page=page, limit=limit)

    def find_share(self, orig_dyn_id: str, *, pages: int = 4) -> ShareRef | None:
        return self._reader.find_share(orig_dyn_id, pages=pages)

    def own_shares(self, *, cursor: str = "") -> dict:
        return self._reader.own_shares(cursor=cursor)

    def share_ref(self, share_id: str) -> ShareRef:
        from .parser import author_of, dyn_id_of, orig_id_of

        raw = self._reader.raw_detail(share_id)
        uid, _, published = author_of(raw)
        return ShareRef(
            dyn_id=dyn_id_of(raw),
            author_uid=uid,
            orig_dyn_id=orig_id_of(raw),
            published_at=published,
        )

    def lottery_notice(self, business_id: str, business_type: int = 1) -> dict | None:
        return self._reader.lottery_notice(business_id, business_type)

    def unread(self) -> MessageStat:
        return self._reader.unread()

    def inbox(self, *, pages: int = 1) -> list[Message]:
        return self._reader.inbox(pages=pages)

    def comments(
        self,
        *,
        rid: str,
        comment_type: int = 17,
        pages: int = 3,
        page_size: int = 20,
        referer: str = "",
    ) -> list[str]:
        return self._reader.comments(
            rid=rid,
            comment_type=comment_type,
            pages=pages,
            page_size=page_size,
            referer=referer,
        )

    # ---- Prober ----
    def did(self, action: ActionKind, *, dyn_id: str, payload: dict[str, str]) -> ProbeState:
        return self._prober.did(action, dyn_id=dyn_id, payload=payload)

    def whoami(self) -> str:
        return self._prober.whoami()

    @property
    def probe_reason(self) -> str:
        return self._prober.last_reason

    # ---- Writer ----
    def perform(self, action: Action) -> Receipt:
        return self._writer.perform(action)

    # ---- 生命周期 ----
    def close(self) -> None:
        if self._auth is not None:
            self._auth.close()
        self._transport.close()

    def __enter__(self) -> BiliGateway:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
