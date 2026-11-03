"""Local-process scheduler; all Bilibili operations use the existing job slot."""
from __future__ import annotations
import os
import threading
import time
from sqlmodel import select
from src.bilibili_auth import get_login_uid
from src.db.models import MaintenanceSettingsRow, CleanupStepRow
from src.db.session import session_scope
from src.notifications import deliver_pending, recover_deliveries


def recover_cleanup_steps():
    with session_scope() as session:
        for row in session.exec(select(CleanupStepRow).where(CleanupStepRow.state == "running")).all():
            row.state, row.message = "unknown", "上次执行中断，下次先核验当前状态"


class MaintenanceWorker:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = None
        self.sender = None

    def start(self):
        if os.environ.get("BINGGO_E2E") == "1" or os.environ.get("BINGGO_DISABLE_BACKGROUND") == "1":
            return
        if self.thread and self.thread.is_alive():
            return
        recover_deliveries()
        recover_cleanup_steps()
        from src.accounts import list_accounts
        from src.account_context import account_scope
        from web.auto_scheduler import auto_scheduler
        for account in list_accounts():
            if account.get("logged_in") and account.get("auto_enabled"):
                with account_scope(account["uid"]):
                    auto_scheduler.start()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.loop, daemon=True, name="binggo-maintenance")
        self.sender = threading.Thread(target=self.send_loop, daemon=True, name="binggo-notifications")
        self.thread.start()
        self.sender.start()

    def stop(self):
        self.stop_event.set()
        from web.auto_scheduler import auto_scheduler
        for scheduler in auto_scheduler.items.values():
            scheduler.stop(reason="软件退出")
        from web.job_runner import runner
        if runner.is_running() and str(runner.get_status().action).startswith("maintenance_"):
            runner.cancel()
        for thread in (self.thread, self.sender):
            if thread:
                thread.join(timeout=2)

    def tick(self, job_runner=None, now=None):
        from web.job_runner import runner
        from web.auto_scheduler import auto_scheduler
        from src.maintenance import settings
        runner = job_runner or runner
        uid = str(get_login_uid() or "")
        if not uid or runner.is_running():
            return False
        from web.schedule_config import load
        if load(uid).nonstop:
            return False
        now = int(time.time()) if now is None else now
        cfg = settings(uid)
        if now - cfg["last_run"] < 1800:
            return False
        # Give a currently active auto sequence priority between its steps.
        if auto_scheduler.get_status().get("refresh_pipeline", {}).get("active"):
            return False
        if runner.try_start("maintenance_cycle", {"uid": uid}, source="system") is None:
            return False
        with session_scope() as session:
            row = session.get(MaintenanceSettingsRow, uid)
            if row is None:
                row = MaintenanceSettingsRow(uid=uid)
                session.add(row)
            row.last_run = now
        return True

    def loop(self):
        while not self.stop_event.wait(1):
            try:
                from src.accounts import list_accounts
                from src.account_context import account_scope
                for account in list_accounts():
                    if account.get("logged_in"):
                        with account_scope(account["uid"]):
                            self.tick()
            except Exception:
                from src.app_logging import get_logger
                get_logger("maintenance").warning("本轮维护调度失败，将稍后重试")

    def send_loop(self):
        while not self.stop_event.wait(2):
            try:
                deliver_pending()
            except Exception:
                from src.app_logging import get_logger
                get_logger("notifications").warning("通知队列处理失败，将稍后重试")


worker = MaintenanceWorker()
