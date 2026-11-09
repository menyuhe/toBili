"""跨层传递的朴素结构。

这些不是领域对象，而是"外部世界交回来的原始材料"：一条动态、一段游标、一份账号档案。
放在端口层，是因为它们的形态由外部决定，不该污染 domain。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from ..domain.draw import DrawFacts


@dataclass(frozen=True, slots=True)
class FeedItem:
    dyn_id: str
    up_uid: str
    text: str
    published_at: datetime | None = None
    up_name: str = ""
    raw_ref: str = ""
    attachments: tuple[str, ...] = ()
    draw_hint: bool = False
    draw: DrawFacts | None = None

    @property
    def has_body(self) -> bool:
        return bool(self.text.strip())


@dataclass(frozen=True, slots=True)
class FeedSlice:
    items: tuple[FeedItem, ...] = ()
    cursor: str = ""
    exhausted: bool = True
    problem: str = ""

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass(frozen=True, slots=True)
class UserCandidate:
    """搜索到的一个 UP 主。这是"可能值得订阅的源"，不是活动。"""

    uid: str
    name: str
    fans: int = 0
    sign: str = ""
    videos: int = 0


@dataclass(frozen=True, slots=True)
class AccountProfile:
    uid: str
    nickname: str = ""
    avatar: str = ""
    level: int = 0
    following: int = 0
    followers: int = 0


@dataclass(frozen=True, slots=True)
class CampaignDetail:
    """一条动态的解析结果。解析器只负责提取事实，判断"能不能参加"是领域的事。"""

    dyn_id: str
    up_uid: str
    up_name: str
    text: str
    published_at: datetime | None = None
    deadline: datetime | None = None
    rid: str = ""
    comment_type: int = 0
    reserve_id: str = ""
    reserve_total: int = 0
    keywords: tuple[str, ...] = ()
    raw_ref: str = ""
    extra: dict[str, str] = field(default_factory=dict)
    draw: DrawFacts | None = None


@dataclass(frozen=True, slots=True)
class MessageStat:
    """消息中心未读数。清理闸门的输入之一。

    只统计"可能需要人看一眼"的四类。私信和系统通知是真会带消息的：中奖通知、
    违规提醒、"活动结束"都在里面。回复和点赞数量大、噪声多，只用来看，不用来拦。
    """

    chat: int = 0
    system: int = 0
    reply: int = 0
    at: int = 0
    like: int = 0

    @property
    def needs_attention(self) -> int:
        return self.chat + self.system


@dataclass(frozen=True, slots=True)
class QrTicket:
    """扫码登录的一次会话。`key` 是轮询凭据，`url` 编码进二维码。"""

    key: str
    url: str
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class QrProgress:
    state: str  # waiting | scanned | confirmed | expired | rejected
    credential: str = ""
    profile: AccountProfile | None = None

    @property
    def done(self) -> bool:
        return self.state in {"confirmed", "expired", "rejected"}
