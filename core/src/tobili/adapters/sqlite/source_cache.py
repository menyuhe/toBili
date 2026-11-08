"""Bounded, account-local discovery cache. Never used to authorize a Bilibili write."""

import json
import time


class SourceCache:
    def __init__(self, db, account):
        self.db, self.account = db, account
        with db.write() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS source_read_cache (
                account INTEGER NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,
                expires REAL NOT NULL, saved REAL NOT NULL, PRIMARY KEY(account,key))""")

    def get(self, key):
        row = (
            self.db.connect()
            .execute(
                "SELECT payload FROM source_read_cache WHERE account=? AND key=? AND expires>?",
                (self.account, key, time.time()),
            )
            .fetchone()
        )
        return json.loads(row["payload"]) if row else None

    def put(self, key, value, ttl):
        text = json.dumps(value, ensure_ascii=False)
        if len(text.encode()) > 512 * 1024:
            return
        now = time.time()
        with self.db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO source_read_cache VALUES(?,?,?,?,?)",
                (self.account, key, text, now + ttl, now),
            )
            conn.execute("DELETE FROM source_read_cache WHERE expires<?", (now,))
            # Whole database limit; independent of how many accounts are configured.
            conn.execute("""DELETE FROM source_read_cache WHERE rowid IN (
                SELECT rowid FROM (SELECT rowid, SUM(length(CAST(payload AS BLOB)))
                OVER (ORDER BY saved DESC,rowid DESC) AS bytes FROM source_read_cache)
                WHERE bytes>67108864)""")
