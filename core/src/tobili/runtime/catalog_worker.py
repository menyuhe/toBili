"""Local catalog housekeeping, independent of account IO and model latency."""

import json
import threading
import time

from ..usecases.catalog_policy import maintain
from .redact import safe_exception

LOCK = threading.RLock()
PREFIX = "catalog.cleanup."


def view(box):
    settings = box.repos.settings
    minutes = int(settings.get(0, PREFIX + "minutes", "60"))
    last = json.loads(settings.get(0, PREFIX + "latest", "null"))
    return dict(
        enabled=settings.get(0, PREFIX + "enabled", "1") == "1",
        minutes=minutes,
        latest=last,
        next_at=float(settings.get(0, PREFIX + "next", "0")),
        history=json.loads(settings.get(0, PREFIX + "history", "[]")),
    )


def run(box, *, force=False):
    with LOCK:
        state = view(box)
        now = box.clock.now()
        if not force and (not state["enabled"] or state["next_at"] > now.timestamp()):
            return None
        try:
            counts = maintain(box.db, now)
            result = dict(
                at=now.isoformat(),
                ok=True,
                **counts,
                note=f"整理过期活动 {counts['expired']} 条（有参与记录 {counts['with_history']}、无参与记录 {counts['without_history']}），移出队列 {counts['pool_removed']} 条；其他目录移除 {counts['removed'] - counts['expired']} 条。参与及开奖结果账本保留",
            )
        except Exception as exc:
            result = dict(
                at=now.isoformat(), ok=False, note="活动库整理失败：" + safe_exception(exc)
            )
        settings = box.repos.settings
        settings.put(0, PREFIX + "latest", json.dumps(result, ensure_ascii=False))
        settings.put(
            0, PREFIX + "history", json.dumps([result, *state["history"]][:20], ensure_ascii=False)
        )
        settings.put(
            0,
            PREFIX + "next",
            str(now.timestamp() + (state["minutes"] * 60 if result["ok"] else 60)),
        )
        box.bus.publish("catalog.cleanup", {"title": result["note"], **result})
        return result


class CatalogWorker:
    def __init__(self, box):
        self.box = box
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self.loop, name="catalog-cleanup", daemon=True)
        self.worker.start()

    def loop(self):
        metadata_at = 0
        while not self.stop.wait(10):
            try:
                # Fresh imports need date normalization before candidate selection;
                # a long cleanup interval must not delay their eligibility.
                if time.monotonic() - metadata_at >= 60:
                    maintain(self.box.db, self.box.clock.now(), normalize_only=True)
                    metadata_at = time.monotonic()
                run(self.box)
            except Exception:
                import logging

                logging.getLogger(__name__).exception("Catalog cleanup scheduler failed")

    def close(self):
        self.stop.set()
        self.worker.join(timeout=8)
