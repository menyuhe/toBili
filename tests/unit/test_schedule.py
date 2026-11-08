"""定时自动化：秒针本身的行为。

这里不测"跑一轮做了什么"（那是 pipeline 的测试），只测这一层最容易错的三件事：
启停、失败不停表、以及"重启接上上次状态"。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from tobili.runtime.schedule import Schedule


class StepClock:
    """每次 now() 往前走一段。让定时循环在测试里几乎立刻到点。"""

    def __init__(self, step: timedelta) -> None:
        self._moment = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
        self._step = step

    def now(self) -> datetime:
        self._moment = self._moment + self._step
        return self._moment


def _wait(runs: list, want: int = 1, timeout: float = 3.0) -> bool:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if len(runs) >= want:
            return True
        time.sleep(0.02)
    return False


def test_start_sets_running_and_next_at() -> None:
    sched = Schedule(runner=lambda apply: {"note": "ok"}, clock=StepClock(timedelta(seconds=0)))
    state = sched.start(interval_min=30, apply=False)
    try:
        assert state.running
        assert state.interval_min == 30
        assert state.next_at is not None
    finally:
        sched.stop()
    assert not sched.status().running
    assert sched.status().next_at is None


def test_run_now_records_a_run_without_scheduling() -> None:
    seen: list[bool] = []
    sched = Schedule(
        runner=lambda apply: seen.append(apply) or {"note": "预演完成", "scanned": 3},
        clock=StepClock(timedelta(seconds=0)),
    )

    run = sched.run_now(False)

    assert seen == [False]
    assert run.ok
    assert run.note == "预演完成"
    assert run.scanned == 3
    # "现在跑一次"不该顺手把定时器打开 —— 那是两件事。
    assert not sched.status().running
    assert sched.status().last is run


def test_a_failing_round_does_not_kill_the_clock() -> None:
    """一轮失败只记一笔。停表等于把临时故障变成永久关闭。"""
    calls: list[int] = []

    def runner(apply: bool) -> dict:
        calls.append(1)
        raise RuntimeError("网络抖动")

    sched = Schedule(runner=runner, clock=StepClock(timedelta(seconds=0)))
    first = sched.run_now(True)
    second = sched.run_now(True)

    assert not first.ok
    assert "网络抖动" in first.note
    assert second is not None
    assert len(calls) == 2


def test_persist_is_called_on_start_and_stop() -> None:
    saved: list[tuple[int, bool, bool]] = []
    sched = Schedule(
        runner=lambda apply: {"note": "ok"},
        clock=StepClock(timedelta(seconds=0)),
        on_save=lambda minutes, apply, running: saved.append((minutes, apply, running)),
    )
    sched.start(interval_min=45, apply=True)
    sched.stop()

    assert saved[0] == (45, True, True)
    assert saved[-1][2] is False


def test_persist_failure_does_not_break_start() -> None:
    """存不下配置只影响重启后记不记得，不能让"开始"这个动作失败。"""

    def boom(minutes: int, apply: bool, running: bool) -> None:
        raise OSError("磁盘只读")

    sched = Schedule(
        runner=lambda apply: {"note": "ok"}, clock=StepClock(timedelta(seconds=0)), on_save=boom
    )
    state = sched.start(interval_min=10, apply=False)
    try:
        assert state.running
    finally:
        sched.stop()


def test_the_clock_actually_fires() -> None:
    """用一只每次往前跳 5 分钟的钟，验证循环真的会到点跑，而不只是个 setter。"""
    runs: list[bool] = []
    sched = Schedule(
        runner=lambda apply: runs.append(apply) or {"note": "到点了"},
        clock=StepClock(step=timedelta(minutes=5)),
    )
    sched.start(interval_min=1, apply=False)
    try:
        assert _wait(runs), "秒针没有在超时前跑到点"
    finally:
        sched.stop()
    # 钟跳得比间隔快，所以会连跑几轮；这里只关心"跑起来了"且用的是预演。
    assert runs and not any(runs)


def test_resume_is_a_noop_when_interval_is_zero() -> None:
    sched = Schedule(runner=lambda apply: {"note": "ok"}, clock=StepClock(timedelta(seconds=0)))
    sched.resume(interval_min=0, apply=True)
    assert not sched.status().running


def test_runs_are_capped() -> None:
    """历史留在内存里，不能无限涨。"""
    sched = Schedule(runner=lambda apply: {"note": "ok"}, clock=StepClock(timedelta(seconds=0)))
    for _ in range(30):
        sched.run_now(False)
    assert len(sched.status().runs) == 20


@pytest.mark.parametrize("apply", [True, False])
def test_apply_flag_reaches_the_runner(apply: bool) -> None:
    seen: list[bool] = []
    sched = Schedule(
        runner=lambda flag: seen.append(flag) or {"note": "ok"},
        clock=StepClock(timedelta(seconds=0)),
    )
    sched.run_now(apply)
    assert seen == [apply]
