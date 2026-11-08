"""定时自动化：进程内的一根秒针。

为什么不是 cron / 计划任务：这个程序的数据全在一台机器上，账号互斥是靠
`runtime.lane` 的文件锁做的。把调度放到系统层会多出一个"它在跑但我看不见"的
状态，而这个工具最怕的正是看不见的写操作。放在同一个进程里，启停、下一轮时间、
上一轮结果都能立刻被界面读到。

三条刻意的取舍：

1. **默认预演。** `apply=False` 是默认值。要真写必须单独打开，且界面上写清楚。
   定时 + 自动写是唯一一种"人不在场也会改线上状态"的组合，默认值不能是它。
2. **重启会恢复。** 配置落库（`schedule.enabled` / `interval` / `apply`），
   进程起来时如果上次是开着的就接着跑。定时任务重启后静默失效比跑起来更糟。
3. **一轮失败不停表。** 网络抖动、cookie 过期都只记在 `last` 里。停表等于把
   "暂时的故障"变成"永久的关闭"，而人往往不会发现它已经停了。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..ports.collab import Clock

# 保留多少轮结果给界面看。太多了没人翻，太少了排查时不够。
KEEP_RUNS = 20


@dataclass(frozen=True, slots=True)
class Run:
    at: datetime
    ok: bool
    note: str
    scanned: int = 0
    written: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat(),
            "ok": self.ok,
            "note": self.note,
            "scanned": self.scanned,
            "written": self.written,
        }


@dataclass
class ScheduleState:
    running: bool = False
    interval_min: int = 60
    apply: bool = False
    next_at: datetime | None = None
    runs: tuple[Run, ...] = field(default=())

    @property
    def last(self) -> Run | None:
        return self.runs[0] if self.runs else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "interval_min": self.interval_min,
            "apply": self.apply,
            "next_at": self.next_at.isoformat() if self.next_at else None,
            "last": self.last.as_dict() if self.last else None,
            "runs": [item.as_dict() for item in self.runs],
        }


class Schedule:
    """一根秒针。`runner` 是"跑一轮"，返回 `{scanned, written, note}` 这样的摘要。"""

    def __init__(
        self,
        *,
        runner: Callable[[bool], Mapping[str, Any]],
        clock: Clock,
        on_save: Callable[[int, bool, bool], None] | None = None,
    ) -> None:
        self._runner = runner
        self._clock = clock
        self._on_save = on_save
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = ScheduleState()
        self._runs: deque[Run] = deque(maxlen=KEEP_RUNS)

    # ---- 控制 ----
    def start(self, *, interval_min: int, apply: bool) -> ScheduleState:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                # 已经在跑就只改参数，不重启 —— 重启会把"下一轮"推迟一整个间隔。
                self._state.interval_min = max(1, int(interval_min))
                self._state.apply = bool(apply)
                self._state.next_at = self._clock.now() + timedelta(
                    minutes=self._state.interval_min
                )
                self._persist()
                return self._snapshot()
            self._state.running = True
            self._state.interval_min = max(1, int(interval_min))
            self._state.apply = bool(apply)
            self._stop = threading.Event()
            # 下一轮时间在这里就算好：界面调 start 之后立刻要显示"多久后"，
            # 等后台线程自己跑起来再填，会有一小段空白。
            self._state.next_at = self._clock.now() + timedelta(minutes=self._state.interval_min)
            self._thread = threading.Thread(
                target=self._loop, args=(self._stop,), name="tobili-schedule", daemon=True
            )
            self._thread.start()
            self._persist()
            return self._snapshot()

    def stop(self, *, persist: bool = True) -> ScheduleState:
        """停表。

        `persist=False` 是给"进程退出"用的：退出时把 enabled=0 写回库，等于
        亲手抹掉"上次是开着的"这条信息，下次启动就再也接不上了 —— 表现为
        "定时任务重启后总是关的"，而且没有任何报错。
        """
        with self._lock:
            self._stop.set()
            self._state.running = False
            self._state.next_at = None
            self._thread = None
            if persist:
                self._persist()
            return self._snapshot()

    def status(self) -> ScheduleState:
        with self._lock:
            return self._snapshot()

    def run_now(self, apply: bool | None = None) -> Run:
        """立刻跑一轮，不影响排期。界面上的"现在跑一次"。"""
        return self._once(self._state.apply if apply is None else apply)

    def resume(self, *, interval_min: int, apply: bool) -> None:
        """进程启动时接上上次的状态。**只在库里有明确记录时调用。**"""
        if interval_min <= 0:
            return
        self.start(interval_min=interval_min, apply=apply)

    # ---- 内部 ----
    def _loop(self, stop: threading.Event) -> None:
        # 第一轮不立刻跑：进程刚起来时 cookie、库、网络都还没热，
        # 而且"打开界面就看见它开始写"会让人没机会先改设置。
        while not stop.is_set():
            minutes = max(1, self._state.interval_min)
            deadline = self._clock.now() + timedelta(minutes=minutes)
            with self._lock:
                if stop.is_set():
                    break
                self._state.next_at = deadline
            while not stop.is_set():
                left = (deadline - self._clock.now()).total_seconds()
                if left <= 0:
                    break
                # 睡短一点，停表才能在 1 秒内响应，而不是等满一个间隔。
                stop.wait(min(1.0, left))
            if stop.is_set():
                break
            self._once(self._state.apply)

    def run_selection(self, apply: bool, runner: Callable[[bool], Mapping[str, Any]]) -> Run:
        return self._once(apply, runner)

    def _once(self, apply: bool, runner: Callable[[bool], Mapping[str, Any]] | None = None) -> Run:
        started = self._clock.now()
        try:
            summary = (runner or self._runner)(apply)
            run = Run(
                at=started,
                ok=bool(summary.get("ok", True)),
                note=str(summary.get("note") or ("已写入" if apply else "预演完成")),
                scanned=int(summary.get("scanned") or 0),
                written=int(summary.get("written") or 0),
            )
        except Exception as exc:
            run = Run(at=started, ok=False, note=f"{type(exc).__name__}: {exc}")
        with self._lock:
            self._runs.appendleft(run)
            self._state.runs = tuple(self._runs)
        return run

    def _should_stop(self) -> bool:
        return self._stop.is_set()

    def _snapshot(self) -> ScheduleState:
        return ScheduleState(
            running=self._state.running,
            interval_min=self._state.interval_min,
            apply=self._state.apply,
            next_at=self._state.next_at,
            runs=tuple(self._runs),
        )

    def _persist(self) -> None:
        if self._on_save is None:
            return
        # 存不下只影响"重启后还记不记得"，不该让启停失败。
        with suppress(Exception):
            self._on_save(self._state.interval_min, self._state.apply, self._state.running)


def sleep_until(seconds: float) -> None:  # pragma: no cover - 仅测试与手工排查用
    time.sleep(max(0.0, seconds))
