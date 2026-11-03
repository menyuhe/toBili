"""Fence every Bilibili mutation, including calls outside the job runner."""
from contextlib import contextmanager
from contextvars import ContextVar

_guard = ContextVar("cloud_write_guard", default=None)
_confirmed = ContextVar("cloud_write_confirmed", default=None)


@contextmanager
def guarded_writes(check, confirmed=None):
    token = _guard.set(check)
    confirmed_token = _confirmed.set(confirmed)
    try:
        yield
    finally:
        _guard.reset(token)
        _confirmed.reset(confirmed_token)


def confirm_cloud_writes():
    """Call only after an activity's success and durable result are confirmed."""
    callback = _confirmed.get()
    if callback:
        callback()


def check_cloud_write():
    callback = _guard.get()
    if callback:
        callback()
        return
    # Catalog connectivity never gates local account tasks.
