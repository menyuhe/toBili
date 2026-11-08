"""事件总线。

界面靠它推进度，不靠轮询数据库。带上自增序号是为了支持"断线重连后从上次的位置
继续"——SSE 的 `Last-Event-ID` 直接用这个号。
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    topic: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)


class EventSink(Protocol):
    """把事件写到磁盘。实现方不该抛异常 —— 记不上历史是遗憾，打断业务是事故。"""

    def append(self, event: Event) -> None: ...

    def last_seq(self) -> int: ...


class EventBus:
    def __init__(
        self,
        *,
        capacity: int = 500,
        start_seq: int = 0,
        sink: EventSink | None = None,
    ) -> None:
        self._items: deque[Event] = deque(maxlen=capacity)
        # 从库里已落盘的最大序号接上。否则重启后序号从 1 重来，浏览器带着旧
        # Last-Event-ID 重连，会把"序号比它小"的新事件全部丢掉，界面就一直停在
        # 重启前的样子。
        self._seq = max(0, start_seq)
        self._sink = sink
        self._changed = threading.Condition()

    def publish(self, topic: str, payload: Mapping[str, Any] | None = None) -> Event:
        with self._changed:
            self._seq += 1
            event = Event(self._seq, topic, dict(payload or {}))
            self._items.append(event)
            self._changed.notify_all()
        # 落库放在锁外：订阅者可能慢，但更怕的是写盘慢了把整条管道卡住。
        if self._sink is not None:
            # 历史记不上只是"刷新后看不到这条"，不该让正在跑的任务崩掉。
            with contextlib.suppress(Exception):
                self._sink.append(event)
        return event

    @property
    def last_seq(self) -> int:
        with self._changed:
            return self._seq

    def since(self, seq: int) -> list[Event]:
        with self._changed:
            return [e for e in self._items if e.seq > seq]

    def wait(self, seq: int, *, timeout: float = 15.0) -> list[Event]:
        """等到有新事件或超时。超时返回空列表，由调用方决定是否发心跳。"""
        deadline = time.monotonic() + timeout
        with self._changed:
            while self._seq <= seq:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    return []
                self._changed.wait(remain)
            return [e for e in self._items if e.seq > seq]
