"""Cooperative cancellation shared by a job and its copied worker contexts."""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event


class TaskCancelled(Exception):
    """A requested stop, not a retryable network or business failure."""


_cancel_event: ContextVar[Event | None] = ContextVar("job_cancel_event", default=None)


@contextmanager
def task_cancellation(event: Event | None):
    token = _cancel_event.set(event)
    try:
        check_cancelled()
        yield
    finally:
        _cancel_event.reset(token)


def check_cancelled() -> None:
    event = _cancel_event.get()
    if event is not None and event.is_set():
        raise TaskCancelled("任务已取消")


def cancellable_sleep(seconds: float) -> None:
    event = _cancel_event.get()
    if event is None:
        time.sleep(seconds)
    elif event.wait(max(0, seconds)):
        raise TaskCancelled("任务已取消")
