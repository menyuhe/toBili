"""Immutable request/job identity. Never use a process-wide active account."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class AccountContext:
    uid: str
    login_session: str = ""


current_account: ContextVar[AccountContext | None] = ContextVar("account", default=None)


@contextmanager
def account_scope(uid: str, login_session: str = ""):
    token = current_account.set(AccountContext(str(uid), login_session))
    try:
        yield current_account.get()
    finally:
        current_account.reset(token)


def scoped_uid() -> str:
    context = current_account.get()
    return context.uid if context else ""
