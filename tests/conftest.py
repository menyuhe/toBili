"""测试夹具。

刻意不用 mock 框架：假实现是普通类，行为一眼能读完。对这套系统来说，
"能看懂假实现做了什么"比"mock 语法正确"重要得多。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from tobili.adapters.sqlite import Database, Repositories
from tobili.domain.action import Action, ActionKind
from tobili.domain.inbox import Message
from tobili.domain.probe import ProbeState
from tobili.domain.receipt import Receipt
from tobili.domain.tidy import ShareRef
from tobili.ports.models import (
    AccountProfile,
    CampaignDetail,
    FeedItem,
    FeedSlice,
    MessageStat,
    QrProgress,
    QrTicket,
    UserCandidate,
)


class FixedClock:
    def __init__(self, moment: datetime | None = None) -> None:
        self._moment = moment or datetime(2026, 11, 4, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._moment

    def advance(self, **kwargs: float) -> datetime:
        self._moment = self._moment + timedelta(**kwargs)
        return self._moment


class StubGateway:
    """可编排的假网关：想让它怎么回答，就怎么设置。"""

    def __init__(self) -> None:
        self.probe = ProbeState.ABSENT
        self.receipt = Receipt.success("dyn-1", "已提交")
        self.writes: list[Action] = []
        self.probes: list[tuple[ActionKind, str]] = []
        self.detail_text = "转发抽奖：关注并转发，1月5日 23:59 截止，送出 3 份大会员"
        self.feed_items: list[FeedItem] = []
        self.user_candidates: list[UserCandidate] = []
        self.detail_fails: set[str] = set()
        # 清理相关：默认"我转发过这条"，未读全为 0
        self.share: ShareRef | None = None
        self.share_lookup_fails = False
        self.unread_stat = MessageStat()
        self.unread_fails = False
        self.inbox_items: list[Message] = []
        self.inbox_fails = False

    # Prober
    def did(self, action: ActionKind, *, dyn_id: str, payload: Mapping[str, str]) -> ProbeState:
        self.probes.append((action, dyn_id))
        return self.probe

    def whoami(self) -> str:
        return "1001"

    # Writer
    def perform(self, action: Action) -> Receipt:
        self.writes.append(action)
        return self.receipt

    # Reader
    def profile(self) -> AccountProfile:
        return AccountProfile(uid="1001", nickname="测试账号", level=5)

    def scan(self, *, kind: str, target: str, cursor: str, limit: int) -> FeedSlice:
        return FeedSlice(items=tuple(self.feed_items), cursor="cursor-2", exhausted=False)

    def detail(self, dyn_id: str) -> CampaignDetail:
        if dyn_id in self.detail_fails:
            raise RuntimeError(f"动态 {dyn_id} 读取失败")
        return CampaignDetail(
            dyn_id=dyn_id,
            up_uid="2002",
            up_name="某个 UP",
            text=self.detail_text,
            rid=dyn_id,
            comment_type=17,
        )

    def search_users(
        self, keyword: str, *, page: int = 1, limit: int = 20
    ) -> tuple[UserCandidate, ...]:
        return tuple(self.user_candidates[:limit])

    def find_share(self, orig_dyn_id: str, *, pages: int = 4) -> ShareRef | None:
        if self.share_lookup_fails:
            raise RuntimeError("读取我的动态失败")
        if self.share is None:
            return None
        return self.share if self.share.orig_dyn_id == orig_dyn_id else None

    def unread(self) -> MessageStat:
        if self.unread_fails:
            raise RuntimeError("消息中心读取失败")
        return self.unread_stat

    def inbox(self, *, pages: int = 1) -> list[Message]:
        if self.inbox_fails:
            raise RuntimeError("消息列表读取失败")
        return list(self.inbox_items)

    # Authenticator
    def begin(self) -> QrTicket:
        return QrTicket(key="k1", url="https://example.invalid/qr")

    def poll(self, ticket: QrTicket) -> QrProgress:
        return QrProgress(state="waiting")


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def repos(tmp_path) -> Repositories:  # type: ignore[no-untyped-def]
    database = Database(tmp_path / "test.db")
    database.migrate()
    return Repositories(database)


@pytest.fixture
def gateway() -> StubGateway:
    return StubGateway()


@pytest.fixture
def account_id(repos: Repositories) -> int:
    return repos.accounts.upsert(AccountProfile(uid="1001", nickname="测试账号"), "test-session")
