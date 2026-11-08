"""Opt-in, per-account, read-only discovery; independent of participation schedules."""

import threading
import time

from .lane import BusyAccount
from .redact import safe_exception
from .work_priority import requested


class SourceRefresh:
    def __init__(self, box):
        self.box = box
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self.run, name="source-refresh", daemon=True)
        self.worker.start()

    def run(self):
        while not self.stop.wait(15):
            try:
                self.tick()
            except Exception:
                # A transient database/network failure must not end the service thread.
                import logging

                logging.getLogger(__name__).exception("Source refresh tick failed")

    def tick(self):
        box = self.box
        for owner in box.repos.accounts.ids():
            if self.stop.is_set():
                return
            if (
                requested(box, owner)
                or float(box.repos.settings.get(owner, "sources.next_slot", "0")) > time.time()
            ):
                continue
            if box.repos.settings.get(owner, "sources.refresh", "0") != "1" or not box.has_session(
                owner
            ):
                continue
            try:
                with box.lanes.exclusive(owner):
                    if not requested(box, owner):
                        self.refresh_one(owner)
            except BusyAccount:
                continue

    def refresh_one(self, owner):
        box = self.box
        now = time.time()
        feeds = [f for f in box.repos.feeds.list_for(owner) if f["enabled"]]
        if not feeds:
            return

        def due(feed):
            try:
                return float(box.repos.settings.get(owner, f"sources.due.{feed['id']}", "0"))
            except ValueError:
                return 0

        feed = min(feeds, key=due)
        if due(feed) > now:
            return
        box.repos.settings.put(owner, "sources.next_slot", str(now + 300))
        key = f"sources.due.{feed['id']}"
        # Reserve before IO; a process crash retries after five minutes, not on every boot.
        box.repos.settings.put(owner, key, str(now + 300))
        try:
            if not hasattr(box, "source_progress"):
                box.source_progress = {}
            box.source_progress[owner] = {
                "key": "source",
                "label": "订阅更新",
                "note": feed.get("label") or feed["target"],
                "since": box.clock.now().isoformat(),
            }
            report = box.scan_feeds(owner).run(owner, feed_id=int(feed["id"]), pages=1, per_feed=5)
            interval = max(
                30, min(1440, int(box.repos.settings.get(owner, "sources.interval", "60")))
            )
            delay = interval * 60
            box.repos.settings.put(owner, key, str(time.time() + delay))
            if getattr(box, "bus", None):
                box.bus.publish(
                    "scan",
                    {
                        "account_id": owner,
                        "feed_id": feed["id"],
                        "automatic": True,
                        "scanned": getattr(report, "scanned", 0),
                        "matched": getattr(report, "matched", 0),
                        "problems": list(report.problems),
                        "model_calls": getattr(report, "model_calls", 0),
                        "model_cache_hits": getattr(report, "model_cache_hits", 0),
                        "title": f"订阅更新 · {feed.get('label') or feed['target']} · "
                        + (
                            "；".join(report.problems)
                            if report.problems
                            else f"读取 {getattr(report, 'scanned', 0)} 条，活动 {getattr(report, 'matched', 0)} 条"
                        ),
                    },
                )
        except Exception as exc:
            box.repos.feeds.record_status(
                int(feed["id"]), now=box.clock.now(), error=safe_exception(exc)
            )
            box.repos.settings.put(owner, key, str(time.time() + 600))
            if getattr(box, "bus", None):
                box.bus.publish(
                    "scan",
                    {
                        "account_id": owner,
                        "feed_id": feed["id"],
                        "title": "订阅更新失败 · " + safe_exception(exc),
                    },
                )

        finally:
            getattr(box, "source_progress", {}).pop(owner, None)

    def close(self):
        self.stop.set()
        self.worker.join(timeout=8)
