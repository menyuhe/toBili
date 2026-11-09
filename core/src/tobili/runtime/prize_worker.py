import threading

from ..usecases.prize_review import review_batch
from .redact import safe_exception


class PrizeWorker:
    def __init__(self, box):
        self.box = box
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self.run, name="prize-review", daemon=True)
        self.worker.start()

    def run(self):
        delay = 3
        policy_at=0
        while not self.stop.wait(delay):
            delay = 10
            import time
            if time.time()-policy_at>=60:
                try:
                    from ..usecases.catalog_policy import maintain
                    maintain(self.box.db,self.box.clock.now())
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('Catalog maintenance failed')
                policy_at=time.time()
            if self.box.repos.settings.get(0, "prizes.enabled", "0") != "1":
                continue
            try:
                delay = 1 if review_batch(self.box) else 30
            except Exception as exc:
                note = safe_exception(exc)
                self.box.repos.settings.put(0, "prizes.note", note)
                self.box.bus.publish("llm", {"title": "奖品核对暂停 · " + note})
                delay = 600

    def close(self):
        self.stop.set()
        self.worker.join(timeout=8)
