"""Let a manual participation request take the next free account slot."""

import threading
import time
from contextlib import contextmanager

from .lane import BusyAccount

KEY = "work.participation_requested_until"
_guard = threading.Lock()
_waiting = set()


def requested(box, owner):
    return float(box.repos.settings.get(owner, KEY, "0")) > time.time()


@contextmanager
def participation_turn(box, owner):
    identity = (id(box), owner)
    with _guard:
        if identity in _waiting:
            raise BusyAccount("参与任务已在执行或等待中")
        _waiting.add(identity)
    deadline = time.monotonic() + 90
    lane = None
    try:
        box.repos.settings.put(owner, KEY, str(time.time() + 120))
        while lane is None:
            candidate = box.lanes.exclusive(owner)
            try:
                candidate.__enter__()
                lane = candidate
            except BusyAccount:
                if time.monotonic() >= deadline:
                    raise BusyAccount("当前任务尚未结束，请稍后再试") from None
                if box.repos.pool.paused(owner)[0]:
                    raise BusyAccount("队列已暂停") from None
                box.pool_progress[owner] = "等待当前任务结束，随后参与"
                time.sleep(0.25)
        yield
    finally:
        try:
            box.repos.settings.put(owner, KEY, "0")
            box.pool_progress.pop(owner, None)
        finally:
            if lane is not None:
                lane.__exit__(None, None, None)
            with _guard:
                _waiting.discard(identity)
