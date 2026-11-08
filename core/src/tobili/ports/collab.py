"""外部协作能力：凭据保险库、通知、文案生成、公共目录、时钟。

全部是 Protocol —— 用例只依赖这些形状，换实现不需要改用例。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from .models import CampaignDetail


class SecretStore(Protocol):
    """凭据存取。实现必须保证：落盘为密文，且权限只对当前用户开放。"""

    def read(self, name: str) -> str | None: ...
    def write(self, name: str, value: str) -> None: ...
    def erase(self, name: str) -> None: ...
    def exists(self, name: str) -> bool: ...


class Notifier(Protocol):
    def send(self, title: str, body: str, *, level: str = "info") -> bool:
        """发通知。失败返回 False，不抛 —— 通知失败不该中断主流程。"""
        ...


class Composer(Protocol):
    def draft(
        self,
        *,
        action: str,
        campaign_title: str,
        up_name: str,
        dyn_id: str = "",
        body: str = "",
    ) -> str:
        """生成评论/转发正文。

        `dyn_id` 和 `body` 是给"看原动态现写"那条路用的：抓评论区要有动态 ID，
        让模型写要有正文。传空串时实现方应当只用标题和 UP 名，不要报错。

        返回空串表示放弃自动撰写，交由人工填写。
        """
        ...


class CatalogFeed(Protocol):
    """公共目录。它是 Editorial 后台唯一的数据入口，不暴露任何账号数据。"""

    def candidates(
        self, *, since: datetime | None = None, limit: int = 200
    ) -> Sequence[CampaignDetail]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class Events(Protocol):
    """向界面推送事件。实现可以是 SSE、内存队列或空实现。"""

    def publish(self, topic: str, payload: Mapping[str, object]) -> None: ...
