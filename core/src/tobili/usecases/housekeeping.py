"""历史数据保留。

库里有两张表只增不减：

- `run_event`：每轮每次探测、提交、跳过都写一行。daemon 常驻时一天几百行，
  一年就是十万行 —— 界面上的"运行记录"和 `last_seq()` 都会越来越慢。
- `inbox_seen`：看过的消息 key。量很小，但没有理由永久留着。

窗口定在 **30 天 / 90 天**，而不是"全留"或"只留一周"：前者是把增长问题往后推，
后者会让"上周那条是不是已经提醒过"变成猜。30 天足够覆盖一次完整的活动周期。

清理只删"我本机的记录"，不碰活动、结论、意图 —— 那些是判断依据，删了就不可恢复。
所以这里没有任何开关，也不需要确认：删错了最多是事件流少一段旧记录。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from ..ports.collab import Clock
from ..ports.repo import InboxRepo, RunEventRepo

EVENT_DAYS = 30
INBOX_DAYS = 90


@dataclass(frozen=True, slots=True)
class Swept:
    events: int = 0
    inbox: int = 0

    @property
    def total(self) -> int:
        return self.events + self.inbox


class Housekeeping:
    def __init__(
        self,
        *,
        events: RunEventRepo,
        inbox: InboxRepo,
        clock: Clock,
    ) -> None:
        self._events = events
        self._inbox = inbox
        self._clock = clock

    def run(self) -> Swept:
        now = self._clock.now()
        return Swept(
            events=self._events.prune(before=now - timedelta(days=EVENT_DAYS)),
            inbox=self._inbox.prune(before=now - timedelta(days=INBOX_DAYS)),
        )
