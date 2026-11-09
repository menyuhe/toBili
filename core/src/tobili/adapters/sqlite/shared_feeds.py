"""Merge shared discovery sources without touching account work records."""

import json
from collections import defaultdict
from contextlib import suppress

KINDS = ("topic", "collection")


def migrate(conn):
    groups = defaultdict(list)
    for row in conn.execute("SELECT * FROM feed WHERE kind IN ('topic','collection') ORDER BY id"):
        target = str(row["target"]).strip()
        target = str(int(target)) if target.isascii() and target.isdigit() else target
        groups[(row["kind"], target)].append(dict(row))
    for (_, target), rows in groups.items():
        keeper = rows[0]["id"]
        latest = max(rows, key=lambda r: (r["last_scan_at"] or "", r["id"]))
        # Preserve the latest cursor as a whole: combining cursor fragments can skip pages.
        label = next(
            (r["label"] for r in rows if r["label"] and r["label"] != r["target"]), latest["label"]
        )
        health = []
        due = []
        for row in rows:
            fid = row["id"]
            status = conn.execute(
                "SELECT attempted_at,error FROM feed_health WHERE feed_id=?", (fid,)
            ).fetchone()
            if status:
                health.append(tuple(status))
            for key in (f"tasks.feed_due.{fid}", f"sources.due.{fid}"):
                for (value,) in conn.execute("SELECT value FROM setting WHERE key=?", (key,)):
                    with suppress(ValueError):
                        due.append(float(value))
            if fid == keeper:
                continue
            conn.execute(
                """INSERT INTO campaign_source(campaign_id,feed_id,feed_label,first_seen_at)
                SELECT campaign_id,?,feed_label,first_seen_at FROM campaign_source WHERE feed_id=?
                ON CONFLICT(campaign_id,feed_id) DO UPDATE SET first_seen_at=min(first_seen_at,excluded.first_seen_at)""",
                (keeper, fid),
            )
            conn.execute("DELETE FROM feed WHERE id=?", (fid,))
        conn.execute(
            "UPDATE feed SET target=?,label=?,cursor=?,last_scan_at=?,enabled=? WHERE id=?",
            (
                target,
                label,
                latest["cursor"],
                latest["last_scan_at"],
                int(any(r["enabled"] for r in rows)),
                keeper,
            ),
        )
        if health:
            at, error = max(health)
            conn.execute("INSERT OR REPLACE INTO feed_health VALUES(?,?,?)", (keeper, at, error))
        conn.execute(
            "INSERT OR IGNORE INTO feed_refresh(feed_id,next_at,last_finished) VALUES(?,?,0)",
            (keeper, max(due, default=0)),
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_feed_shared ON feed(kind,target) WHERE kind IN ('topic','collection')"
    )


def interval(db, kind):
    key = "topics" if kind == "topic" else "collections"
    periods = []
    rows = db.execute(
        "SELECT s.value FROM setting s JOIN account a ON a.id=s.account_id WHERE s.key='tasks.config' AND a.disabled=0"
    )
    for (raw,) in rows:
        try:
            plan = json.loads(raw)
            if plan.get("running"):
                periods.extend(
                    max(30, int(t["minutes"])) * 60
                    for t in plan.get("tasks", [])
                    if t["key"] == key and t["enabled"]
                )
        except (ValueError, TypeError, KeyError):
            continue
    for (
        raw,
    ) in db.execute("""SELECT coalesce(i.value,'60') FROM setting s JOIN account a ON a.id=s.account_id
        LEFT JOIN setting i ON i.account_id=s.account_id AND i.key='sources.interval'
        WHERE s.key='sources.refresh' AND s.value='1' AND a.disabled=0"""):
        with suppress(ValueError):
            periods.append(max(30, int(raw)) * 60)
    return min(periods, default=3600)
