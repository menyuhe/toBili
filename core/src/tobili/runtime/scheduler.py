"""定时循环。

循环本身只做三件事：跑一轮、报一次进度、等下一次。真正的作业在 `usecases.pipeline`
里，所以这里没有业务判断，也因此可以测。

两个刻意的地方：

- **等待是切成小片的。** 一小时一分钟地睡死，Ctrl+C 要等到分片结束才生效，用户
  会以为程序卡住了。这里每片不超过一秒，随时能停。
- **单轮异常不结束循环。** 网络抖动、上游改字段、某条动态解析失败，都应该是"这轮
  没跑好"，不是"服务停止"。异常上报后继续等下一轮。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .events import EventBus
from .redact import safe_exception

SLICE = 1.0


@dataclass(frozen=True, slots=True)
class RoundResult:
    seq: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class Scheduler:
    def __init__(
        self,
        task: Callable[[], Mapping[str, Any]],
        *,
        interval: float,
        bus: EventBus | None = None,
        stop: threading.Event | None = None,
        sleep: Callable[[float], bool] | None = None,
        rounds: int | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError("间隔必须大于 0 秒")
        self._task = task
        self._interval = interval
        self._bus = bus
        self._stop = stop if stop is not None else threading.Event()
        # 注入点：返回 True 表示"该停了"。测试里用它跳过真实等待。
        self._sleep = sleep if sleep is not None else self._stop.wait
        self._rounds = rounds
        self.history: list[RoundResult] = []

    @property
    def stop_event(self) -> threading.Event:
        return self._stop

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> int:
        """跑到停止为止。返回实际执行的轮数。"""
        done = 0
        while not self._stop.is_set():
            if self._rounds is not None and done >= self._rounds:
                break
            result = self._round(done + 1)
            self.history.append(result)
            done += 1
            if self._rounds is not None and done >= self._rounds:
                break
            if self._pause():
                break
        return done

    def _round(self, seq: int) -> RoundResult:
        try:
            payload = dict(self._task() or {})
        except Exception as exc:  # 单轮失败不等于服务停止
            message = safe_exception(exc)
            self._publish("daemon.round_failed", {"round": seq, "error": message})
            return RoundResult(seq=seq, error=message)

        self._publish("daemon.round_done", {"round": seq, **payload})
        return RoundResult(seq=seq, payload=payload)

    def _pause(self) -> bool:
        """等一个间隔。返回 True 表示期间被要求停止。"""
        remaining = self._interval
        while remaining > 0:
            if self._stop.is_set():
                return True
            step = min(SLICE, remaining)
            if self._sleep(step):
                return True
            remaining -= step
        return self._stop.is_set()

    def _publish(self, topic: str, payload: Mapping[str, Any]) -> None:
        if self._bus is not None:
            self._bus.publish(topic, payload)
