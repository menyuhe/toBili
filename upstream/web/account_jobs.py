"""Independent execution slots; source refresh has one shared slot."""
import threading
import time
from contextvars import copy_context
from src.account_context import account_scope, current_account, scoped_uid

GLOBAL_ACTIONS = frozenset({"refresh_all", "refresh_source", "refresh_watch", "refresh_discovery", "refresh_discovery_history"})


class AccountJobRunner:
    def __init__(self, factory):
        self.factory = factory
        self._lock = threading.RLock()
        self._runners = {}
        self._login = {}
        self._queues = {}
        self._dispatchers = set()

    def for_uid(self, uid):
        with self._lock:
            return self._runners.setdefault(str(uid), self.factory())

    def any_running(self):
        with self._lock:
            return any(r.is_running() for r in self._runners.values()) or any(self._queues.values())

    def recover_on_startup(self):
        self.for_uid("__global__").recover_on_startup()

    def try_start(self, action, params=None, *, source="ui"):
        from src.accounts import default_uid
        params = dict(params or {})
        context = current_account.get()
        uid = scoped_uid() if context is not None else str(params.get("uid") or default_uid())
        if params.get("uid") and str(params["uid"]) != uid:
            raise ValueError("任务账号与请求账号不一致")
        key = uid
        if action in GLOBAL_ACTIONS:
            key, uid = "__global__", default_uid()
        legacy_login = action == "login" and not params.get("login_session")
        if legacy_login:
            params["login_session"] = __import__("uuid").uuid4().hex
        login_session = params.get("login_session", "")
        if login_session and not __import__("re").fullmatch(r"[a-f0-9]{32}", login_session):
            raise ValueError("登录会话标识无效")
        if action == "login":
            if login_session:
                key = uid or ("__login__" if legacy_login else "login:" + login_session)
            elif not uid:
                key = "__login__"
        params["uid"] = uid
        params["scope"] = "global" if action in GLOBAL_ACTIONS else "account"
        target = self.for_uid(key)
        with self._lock, account_scope(uid, login_session):
            waiting = self._queues.setdefault(key, [])
            if target.is_running() or waiting:
                if action == "login":
                    return None
                from src.job_store import insert_running_job, mark_job_queued
                from web.job_runner import _build_label
                if action in GLOBAL_ACTIONS and target.get_status().action == action:
                    return target.get_status().id
                job_id = insert_running_job(action=action, label=_build_label(action, params), source=source, params=params)
                mark_job_queued(job_id)
                waiting.append((job_id, action, params, source, copy_context()))
                if key not in self._dispatchers:
                    self._dispatchers.add(key)
                    threading.Thread(target=self._drain, args=(key,), daemon=True, name="account-queue-" + key).start()
            else:
                job_id = target.try_start(action, params, source=source)
        if job_id and login_session:
            self._login[login_session] = (target, job_id)
        return job_id

    def _drain(self, key):
        while True:
            with self._lock:
                target = self.for_uid(key)
                waiting = self._queues[key]
                if not waiting:
                    self._dispatchers.discard(key)
                    return
                if not target.is_running():
                    job_id, action, params, source, context = waiting.pop(0)
                    try:
                        context.run(target.try_start, action, params, source=source, queued_job_id=job_id)
                    except Exception as exc:
                        from src.job_store import finish_job
                        finish_job(job_id, state="error", message=str(exc), log_summary="", result=None, error_kind="queue")
            time.sleep(.1)

    def cancel_pending(self, uid):
        from src.job_store import finish_job
        with self._lock:
            for job_id, *_ in self._queues.pop(uid, []):
                finish_job(job_id, state="cancelled", message="账号已退出，取消排队任务", log_summary="", result=None, error_kind="cancelled")
            self._queues[uid] = []

    def start(self, action, params=None, *, source="ui"):
        return self.try_start(action, params, source=source) is not None

    def get_status(self):
        own = self.for_uid(scoped_uid()).get_status()
        if own.state == "running":
            return own
        for key in ("__global__", "__login__"):
            other = self.for_uid(key).get_status()
            if other.state == "running":
                return other
        return own

    def is_running(self):
        return self.for_uid(scoped_uid()).is_running()

    def resolve_job_status(self, job_id):
        from src.job_store import get_job
        from web.job_runner import _status_from_row, JobStatus
        row = get_job(job_id)
        if row and str((row.get("params") or {}).get("uid", "")) not in (scoped_uid(), "") and (row.get("params") or {}).get("scope") != "global":
            raise ValueError("任务不属于当前账号")
        return _status_from_row(row) if row else JobStatus(id=job_id, state="interrupted")

    def cancel(self, job_id=None):
        from src.job_store import finish_job
        with self._lock:
            waiting = self._queues.get(scoped_uid(), [])
            for queued in list(waiting):
                if queued[0] == job_id:
                    waiting.remove(queued)
                    finish_job(job_id, state="cancelled", message="排队任务已取消", log_summary="", result=None, error_kind="cancelled")
                    return True
        target = self.for_uid(scoped_uid())
        if job_id is None or target.get_status().id == job_id:
            return target.cancel(job_id)
        shared = self.for_uid("__global__")
        return shared.cancel(job_id) if shared.get_status().id == job_id else False

    def wait_until_terminal(self, *args, **kwargs):
        # The wait implementation resolves the persisted job by ID, not the visible slot.
        from web.job_runner import JobRunner
        return JobRunner.wait_until_terminal(self, *args, **kwargs)

    def login_status(self, session_id):
        from fastapi import HTTPException
        if session_id not in self._login:
            raise HTTPException(404, "登录会话不存在")
        target, job_id = self._login[session_id]
        return target.resolve_job_status(job_id).to_dict()

    def cancel_login(self, session_id):
        self.login_status(session_id)
        target, job_id = self._login[session_id]
        return target.cancel(job_id)
