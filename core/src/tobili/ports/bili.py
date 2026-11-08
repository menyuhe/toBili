"""B 站侧的能力契约。

刻意把写操作收敛成 `Writer.perform(action)` 一个入口，而不是 `follow()` / `share()` /
`comment()` 一串方法。理由：调用方需要统一走"探测 → 记账 → 单次发送"这条路径，
方法一多，就一定有人绕过它直接调某一个。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ..domain.action import Action, ActionKind
from ..domain.inbox import Message
from ..domain.probe import ProbeState
from ..domain.receipt import Receipt
from ..domain.tidy import ShareRef
from .models import (
    AccountProfile,
    CampaignDetail,
    FeedSlice,
    MessageStat,
    QrProgress,
    QrTicket,
    UserCandidate,
)


class Authenticator(Protocol):
    def begin(self) -> QrTicket:
        """申请一次扫码会话。"""
        ...

    def poll(self, ticket: QrTicket) -> QrProgress:
        """查一次扫码进度。返回 `done` 为真的结果时流程结束。"""
        ...


class Reader(Protocol):
    def profile(self) -> AccountProfile:
        """当前登录账号的档案。失败说明凭据失效，抛 `MissingCredential` 或 `RemoteError(AUTH)`。"""
        ...

    def scan(self, *, kind: str, target: str, cursor: str, limit: int) -> FeedSlice:
        """拉一页订阅源。`cursor` 为空表示从头开始。"""
        ...

    def detail(self, dyn_id: str) -> CampaignDetail:
        """把一条动态解析成结构化材料。**不做参与判断**。"""
        ...

    def search_users(
        self, keyword: str, *, page: int = 1, limit: int = 20
    ) -> tuple[UserCandidate, ...]:
        """按关键词找 UP 主。用于发现订阅源，不用于发现活动。"""
        ...

    def find_share(self, orig_dyn_id: str, *, pages: int = 4) -> ShareRef | None:
        """在我自己的空间动态里找转发自 `orig_dyn_id` 的那一条。

        返回 `None` 表示翻完了没找到。调用方只能拿它决定"要不要动手"，
        不能拿它证明"没转过"。
        """
        ...

    def unread(self) -> MessageStat:
        """消息中心未读数。读不到要抛错 —— 闸门把"读不到"当成关闭。"""
        ...

    def inbox(self, *, pages: int = 1) -> list[Message]:
        """未读消息清单：私信会话、回复我的、@我的。

        **系统通知读不到。** 消息中心里"系统通知"那一栏只有计数，没有公开的
        列表接口（`/x/msgfeed/system` 等一串路径实测全 404）。这里不编造数据，
        调用方要按"只有计数"来展示。

        单类失败不该让整张清单空掉：某一类读不到就跳过它，其余照常返回。
        """
        ...

    def comments(
        self,
        *,
        rid: str,
        comment_type: int = 17,
        pages: int = 3,
        page_size: int = 20,
        referer: str = "",
    ) -> list[str]:
        """评论区正文。抓不到返回空列表，不抛 —— 它是加分项，不是前置条件。

        为什么返回 `list[str]` 而不是结构化评论：我们要的只是"别人怎么说话"，
        点赞数、楼中楼、用户等级全都用不上。要得越多，接口一变就坏得越厉害。
        """
        ...


class Prober(Protocol):
    def did(self, action: ActionKind, *, dyn_id: str, payload: Mapping[str, str]) -> ProbeState:
        """我是否已经做过这个动作。

        实现必须遵守：只有**确证没做过**才返回 `ABSENT`；网络失败、限流、
        风控、翻页翻不到，一律 `UNKNOWN`。
        """
        ...

    def whoami(self) -> str:
        """当前登录的 UID。探测需要它来定位"我的动态流"。"""
        ...


class Writer(Protocol):
    def perform(self, action: Action) -> Receipt:
        """执行一次写操作。

        契约：
        - **只发一次**，不重试
        - 超时/连接中断 → 返回 `Receipt.unclear()`，`ambiguous=True`
        - 远端明确拒绝 → 返回 `Receipt.rejected(fault, msg)`
        """
        ...


class Gateway(Reader, Prober, Writer, Authenticator, Protocol):
    """完整网关。装配时由适配器一次性实现，方便注入。"""
