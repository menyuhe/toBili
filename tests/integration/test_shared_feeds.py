import json
import subprocess
import sys
import time

import pytest

from tobili.adapters.sqlite import Database, Repositories
from tobili.ports.models import AccountProfile, FeedSlice
from tobili.usecases.manage_feeds import FeedRejected, ManageFeeds
from tobili.usecases.scan_feeds import ScanFeeds


@pytest.fixture
def db(repos):
    return repos.feeds._db


def test_shared_list_edit_remove_and_private_sources(repos, account_id):
    other = repos.accounts.upsert(AccountProfile(uid="second", nickname="Second"), "other")
    manage = ManageFeeds(repos.feeds)
    topic = manage.add(account_id, kind="topic", target="001", label="共同话题")
    assert manage.add(other, kind="topic", target="1", label="新备注") == topic
    collection = manage.add(account_id, kind="collection", target="2")
    private = manage.add(account_id, kind="following", target="")
    assert {f.id for f in manage.list(other)} == {topic, collection}
    manage.remove(other, topic)
    assert not next(f for f in manage.list(account_id) if f.id == topic).enabled
    manage.rename(other, topic, "共享备注")
    assert next(f for f in manage.list(account_id) if f.id == topic).label == "共享备注"
    with pytest.raises(FeedRejected):
        manage.delete(other, private)
    repos.accounts.disable(account_id)
    assert {f.id for f in manage.list(other)} == {topic, collection}
    manage.delete(other, topic)
    assert topic not in {f.id for f in manage.list(account_id)}


def test_simultaneous_process_and_back_to_back_accounts_fetch_once(
    repos, db, clock, account_id, tmp_path
):
    other = repos.accounts.upsert(AccountProfile(uid="b", nickname="B"), "b")
    fid = repos.feeds.add(account_id, "topic", "123", "topic")
    calls = []

    class Gateway:
        def scan(self, **kwargs):
            calls.append(kwargs)
            return FeedSlice()

    scanner = ScanFeeds(
        gateway=Gateway(), feeds=repos.feeds, campaigns=repos.campaigns, clock=clock
    )
    script = """import sys
from tobili.adapters.sqlite import Database,Repositories
d=Database(sys.argv[1]);d.migrate();r=Repositories(d)
with r.feeds.scan_guard(r.feeds.list_for(1)[0]) as source:
 print('locked',flush=True);sys.stdin.readline()
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(db.path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "locked"
        assert scanner.run(other).reused == 1 and not calls
        with pytest.raises(FeedRejected):
            ManageFeeds(repos.feeds).delete(other, fid)
    finally:
        proc.communicate("\n", timeout=10)
        assert proc.returncode == 0
    scanner.run(account_id)
    assert len(calls) == 1
    assert scanner.run(other).reused == 1 and len(calls) == 1
    restarted = Database(db.path)
    restarted.migrate()
    rr = Repositories(restarted)
    again = ScanFeeds(gateway=Gateway(), feeds=rr.feeds, campaigns=rr.campaigns, clock=clock)
    assert again.run(other, automatic=True).reused == 1 and len(calls) == 1
    restarted.close()


def test_merge_preserves_history_cursor_health_and_account_schedules(db, repos, account_id):
    other = repos.accounts.upsert(AccountProfile(uid="b", nickname="B"), "b")
    with db.write() as c:
        c.execute("DROP INDEX ix_feed_shared")
        a = c.execute(
            "INSERT INTO feed(account_id,kind,target,label,cursor,last_scan_at,enabled) VALUES(1,'topic','001','原备注','old','2026-11-01',1)"
        ).lastrowid
        b = c.execute(
            "INSERT INTO feed(account_id,kind,target,label,cursor,last_scan_at,enabled) VALUES(?,'topic','1','后备注','v2:latest','2026-11-08',0)",
            (other,),
        ).lastrowid
        c.execute(
            "INSERT INTO campaign(dyn_id,up_uid,first_seen_at,updated_at) VALUES('fake','fake','2026-11-01','2026-11-01')"
        )
        cid = c.execute("SELECT id FROM campaign").fetchone()[0]
        c.execute("INSERT INTO campaign_source VALUES(?,?,?,?)", (cid, b, "later", "2026-11-02"))
        c.execute("INSERT INTO campaign_source VALUES(?,?,?,?)", (cid, a, "older", "2026-11-01"))
        c.execute("INSERT INTO feed_health VALUES(?,?,?)", (b, "2026-11-08", "old error"))
        c.execute(
            "INSERT INTO setting VALUES(1,'tasks.config',?)",
            (json.dumps({"running": True, "tasks": []}),),
        )
        c.execute(
            "INSERT INTO setting VALUES(?,'tasks.config',?)",
            (other, json.dumps({"running": False, "tasks": []})),
        )
        c.execute("INSERT INTO setting VALUES(?,?,'2000000000')", (other, f"tasks.feed_due.{b}"))
        plans = c.execute(
            "SELECT * FROM setting WHERE key='tasks.config' ORDER BY account_id"
        ).fetchall()
        c.execute("PRAGMA user_version=14")
    db.migrate()
    db.migrate()
    feeds = repos.feeds.list_for(other)
    assert len(feeds) == 1
    row = feeds[0]
    assert (
        row["id"] == a
        and row["target"] == "1"
        and row["cursor"] == "v2:latest"
        and row["enabled"] == 1
    )
    assert (
        row["label"] == "原备注"
        and row["error"] == "old error"
        and row["next_scan_at"] == 2000000000
    )
    assert db.connect().execute("SELECT count(*) FROM campaign_source").fetchone()[0] == 1
    assert (
        db.connect().execute("SELECT first_seen_at FROM campaign_source").fetchone()[0]
        == "2026-11-01"
    )
    assert (
        plans
        == db.connect()
        .execute("SELECT * FROM setting WHERE key='tasks.config' ORDER BY account_id")
        .fetchall()
    )
    assert not db.connect().execute("PRAGMA foreign_key_check").fetchall()


def test_two_due_accounts_and_legacy_refresh_share_global_interval(
    db, repos, clock, account_id, monkeypatch
):
    from types import SimpleNamespace

    from tobili.runtime.lane import Lanes
    from tobili.runtime.taskbook import TaskBook

    other = repos.accounts.upsert(AccountProfile(uid="b", nickname="B"), "b")
    fid = repos.feeds.add(account_id, "collection", "321", "collection")
    now = [1800000000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    calls = []

    class Gateway:
        def scan(self, **kw):
            calls.append(kw)
            return FeedSlice()

    box = SimpleNamespace(
        repos=repos,
        db=db,
        clock=clock,
        lanes=Lanes(db.path.parent / "lanes"),
        has_session=lambda owner: True,
        pool_progress={},
        bus=SimpleNamespace(publish=lambda *args: None),
    )
    box.scan_feeds = lambda owner: ScanFeeds(
        gateway=Gateway(), feeds=repos.feeds, campaigns=repos.campaigns, clock=clock
    )
    book = TaskBook(box)
    for owner, minutes in [(account_id, 30), (other, 60)]:
        plan = {
            "running": True,
            "apply": False,
            "tasks": [{"key": "collections", "enabled": True, "minutes": minutes}],
        }
        repos.settings.put(owner, "tasks.config", json.dumps(plan))
    # Both accounts due at the same time: one network call, shared durable cooldown.
    for owner in [account_id, other]:
        book.run(owner, "collections", book.config(owner), scheduled=True)
    assert len(calls) == 1
    due = repos.feeds.list_for(other)[0]["next_scan_at"]
    assert due == now[0] + 1800
    now[0] += 1801
    # Another account may do the next read; result still becomes shared immediately.
    report = box.scan_feeds(other).run(other, automatic=True, feed_id=fid)
    assert report.feeds == 1 and len(calls) == 2
    assert box.scan_feeds(account_id).run(account_id, automatic=True).reused == 1
