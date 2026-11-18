"""One device-wide schedule for the shared feed catalog."""

import json
import time

KEY = "tasks.shared.config"
FEEDS = ("topics", "collections")
COMMON = (*FEEDS, "cleanup")


def put(db, owner, key, value):
    db.execute(
        "INSERT INTO setting(account_id,key,value) VALUES(?,?,?) "
        "ON CONFLICT(account_id,key) DO UPDATE SET value=excluded.value",
        (owner, key, str(value)),
    )


def config(box):
    raw = box.repos.settings.get(0, KEY, "")
    if raw:
        return json.loads(raw)
    with box.db.write() as db:
        settings = box.repos.settings
        existing = db.execute(
            "SELECT value FROM setting WHERE account_id=0 AND key=?", (KEY,)
        ).fetchone()
        if existing:
            return json.loads(existing[0])
        owners = box.repos.accounts.ids()
        tasks = {key: [] for key in COMMON}
        for owner in owners:
            old = json.loads(settings.get(owner, "tasks.config", "{}"))
            if old.get("running"):
                for task in old["tasks"]:
                    key = task["key"]
                    if key in COMMON and task["enabled"]:
                        due = float(settings.get(owner, "tasks.due." + key, "0"))
                        tasks[key].append((task["minutes"], due))
            if settings.get(owner, "sources.refresh", "0") == "1":
                kinds = {f["kind"] for f in box.repos.feeds.list_for(owner) if f["enabled"]}
                for key, kind in zip(FEEDS, ("topic", "collection"), strict=True):
                    if kind in kinds:
                        tasks[key].append((int(settings.get(owner, "sources.interval", "60")), 0))
        rows = []
        for key in FEEDS:
            entries = tasks[key]
            rows.append(
                dict(
                    key=key, enabled=bool(entries), minutes=min((m for m, _ in entries), default=60)
                )
            )
            put(db, 0, "tasks.due." + key, min((d for _, d in entries), default=0))
        if tasks["cleanup"]:
            for suffix, value in (
                ("minutes", min(m for m, _ in tasks["cleanup"])),
                ("next", min(d for _, d in tasks["cleanup"])),
            ):
                previous = settings.get(
                    0, "catalog.cleanup." + suffix, "60" if suffix == "minutes" else "0"
                )
                put(
                    db,
                    0,
                    "catalog.cleanup." + suffix,
                    min(float(previous), value) if suffix == "next" else min(int(previous), value),
                )
        result = dict(running=True, tasks=rows)
        put(db, 0, KEY, json.dumps(result))
        return result


def save(box, tasks):
    previous = config(box)
    with box.db.write() as db:
        for task in tasks:
            if task not in previous["tasks"]:
                put(db, 0, "tasks.due." + task["key"], time.time() + task["minutes"] * 60)
        put(db, 0, KEY, json.dumps(dict(running=True, tasks=tasks)))
