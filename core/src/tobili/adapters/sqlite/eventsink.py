"""把事件总线上的事件落到 `run_event` 表。

为什么需要这层薄包装：总线的 `Event.at` 是 epoch 秒，而仓储要 datetime；总线的
payload 是任意 dict，而表里存的是 JSON 文本。转换只该在一个地方发生。

写失败一律吞掉 —— 历史记不上只是刷新后少一条，把正在跑的任务带崩才是事故。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from ...runtime.events import Event
from ...runtime.redact import scrub
from .repos import RunEventRepo


class SqliteEventSink:
    def __init__(self, repo: RunEventRepo) -> None:
        self._repo = repo

    def append(self, event: Event) -> None:
        try:
            payload = json.dumps(scrub(event.payload), ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            payload = json.dumps({"unserializable": True}, ensure_ascii=False)
        self._repo.append(
            seq=event.seq,
            topic=event.topic,
            payload=payload,
            at=datetime.fromtimestamp(event.at, tz=UTC),
        )

    def last_seq(self) -> int:
        return self._repo.last_seq()
