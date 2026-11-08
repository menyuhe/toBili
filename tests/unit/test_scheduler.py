"""定时循环。

真正要证明的只有两件事：该停的时候停得下来，出错的时候不跟着一起死。
"""

from __future__ import annotations

import threading

import pytest

from tobili.runtime.events import EventBus
from tobili.runtime.scheduler import Scheduler


def _fast() -> tuple:
    """不真的睡。返回 (sleep 函数, 调用记录)。"""
    calls: list[float] = []

    def sleep(seconds: float) -> bool:
        calls.append(seconds)
        return False

    return sleep, calls


def test_runs_requested_rounds() -> None:
    sleep, _calls = _fast()
    runs: list[int] = []
    scheduler = Scheduler(
        lambda: runs.append(1) or {"ok": True}, interval=10, sleep=sleep, rounds=3
    )

    assert scheduler.run() == 3
    assert len(runs) == 3
    assert len(scheduler.history) == 3


def test_interval_is_waited_in_slices() -> None:
    """一小时一分钟地睡死，Ctrl+C 要等到分片结束才生效，用户会以为程序卡住了。"""
    sleep, calls = _fast()
    Scheduler(lambda: {}, interval=3.5, sleep=sleep, rounds=2).run()

    # 两轮之间等一次；每次等待都切成 1 秒以内的片。
    assert calls
    assert all(step <= 1.0 for step in calls)
    assert pytest.approx(sum(calls), abs=0.01) == 3.5


def test_failure_does_not_stop_the_loop() -> None:
    sleep, _calls = _fast()
    seen: list[str] = []

    def task() -> dict[str, object]:
        seen.append("x")
        if len(seen) == 1:
            raise RuntimeError("上游炸了")
        return {"ok": True}

    scheduler = Scheduler(task, interval=1, sleep=sleep, rounds=2)
    assert scheduler.run() == 2
    assert scheduler.history[0].ok is False
    assert "上游炸了" in scheduler.history[0].error
    assert scheduler.history[1].ok is True


def test_stop_event_breaks_immediately() -> None:
    stop = threading.Event()
    stop.set()
    scheduler = Scheduler(lambda: {}, interval=1, stop=stop)

    assert scheduler.run() == 0


def test_stop_during_wait_is_honoured() -> None:
    def sleep(seconds: float) -> bool:
        return True  # 每次等待都报告"该停了"

    scheduler = Scheduler(lambda: {}, interval=60, sleep=sleep, rounds=5)
    assert scheduler.run() == 1


def test_publishes_round_events() -> None:
    bus = EventBus()
    sleep, _calls = _fast()
    scheduler = Scheduler(lambda: {"settled": 2}, interval=1, bus=bus, sleep=sleep, rounds=1)
    scheduler.run()

    events = bus.since(0)
    assert [e.topic for e in events] == ["daemon.round_done"]
    assert events[0].payload["settled"] == 2


def test_rejects_zero_interval() -> None:
    with pytest.raises(ValueError):
        Scheduler(lambda: {}, interval=0)
