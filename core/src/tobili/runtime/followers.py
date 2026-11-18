"""Hourly account statistics. Only successful, UID-matched samples replace the cache."""

import json
import logging
import threading

KEY = "account.follower_sample"


def sample(box, owner, uid):
    raw = box.repos.settings.get(owner, KEY, "")
    data = json.loads(raw) if raw else {}
    return data if data.get("uid") == uid else {}


def refresh(box, owner):
    from .resource_access import allowed

    profile = box.repos.accounts.by_id(owner)
    if not profile:
        return
    uid = profile.uid
    current = sample(box, owner, uid)
    sync = getattr(box, "sync_followers", None)
    if allowed(box, owner):
        now = int(box.clock.now().timestamp())
        if now - current.get("at", 0) >= 3600:
            count = box.gateway_for(owner).follower_count(uid)
            current = {"uid": uid, "count": count, "at": now}
            box.repos.settings.put(owner, KEY, json.dumps(current))
        if sync and current and current.get("published_at") != current["at"]:
            sync(uid, current)
            current["published_at"] = current["at"]
            box.repos.settings.put(owner, KEY, json.dumps(current))
    elif sync:
        latest = sync(uid, None)
        if (
            latest
            and type(latest.get("count")) is int
            and latest["count"] >= 0
            and type(latest.get("at")) is int
            and latest["at"] > current.get("at", 0)
        ):
            box.repos.settings.put(
                owner, KEY, json.dumps({"uid": uid, "count": latest["count"], "at": latest["at"]})
            )


class FollowerWorker:
    def __init__(self, box):
        self.box = box
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True, name="account-followers")
        self.thread.start()

    def loop(self):
        delay = 10
        try:
            while not self.stop.wait(delay):
                delay = 300
                for owner in self.box.repos.accounts.ids():
                    try:
                        refresh(self.box, owner)
                    except Exception as exc:
                        logging.getLogger(__name__).info(
                            "Follower refresh deferred for %s (%s)", owner, type(exc).__name__
                        )
        finally:
            self.box.db.close()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=10)
