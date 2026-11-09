import threading
from types import SimpleNamespace

from tobili.runtime.source_refresh import SourceRefresh


def test_refresh_is_opt_in_and_account_scoped(repos, clock, account_id):
    feed = repos.feeds.add(account_id, "topic", "1", "话题")
    calls = []

    class Scanner:
        def run(self, owner, **kw):
            calls.append((owner, kw))
            return SimpleNamespace(problems=[])

    from contextlib import nullcontext

    box = SimpleNamespace(
        repos=repos,
        clock=clock,
        has_session=lambda owner: True,
        lanes=SimpleNamespace(exclusive=lambda owner: nullcontext()),
        scan_feeds=lambda owner: Scanner(),
    )
    worker = SourceRefresh.__new__(SourceRefresh)
    worker.box = box
    worker.stop = threading.Event()
    worker.tick()
    assert calls == []
    repos.settings.put(account_id, "sources.refresh", "1")
    worker.tick()
    worker.tick()
    assert len(calls) == 1 and calls[0][0] == account_id and calls[0][1]["feed_id"] == feed
    assert "apply" not in calls[0][1]
    # Due time survives constructing a second worker against the same storage.
    again = SourceRefresh.__new__(SourceRefresh)
    again.box = box
    again.stop = threading.Event()
    again.tick()
    assert len(calls) == 1


def test_day_of_refresh_keeps_accounts_independent_and_delays_failures(
    repos, clock, account_id, monkeypatch
):
    from contextlib import nullcontext

    from tobili.ports.models import AccountProfile

    other = repos.accounts.upsert(AccountProfile(uid="222", nickname="second"), "second")
    # Personal UP dynamics remain account scoped; shared topics have separate tests.
    repos.feeds.add(account_id, "space", "1", "limited")
    repos.feeds.add(other, "space", "2", "healthy")
    for owner in (account_id, other):
        repos.settings.put(owner, "sources.refresh", "1")
    now = [1_800_000_000.0]
    monkeypatch.setattr("tobili.runtime.source_refresh.time.time", lambda: now[0])
    calls = {account_id: 0, other: 0}

    class Scanner:
        def run(self, owner, **kw):
            calls[owner] += 1
            assert "apply" not in kw
            if owner == account_id:
                raise OSError("temporary outage")
            return SimpleNamespace(problems=[])

    box = SimpleNamespace(
        repos=repos,
        clock=clock,
        has_session=lambda owner: True,
        lanes=SimpleNamespace(exclusive=lambda owner: nullcontext()),
        scan_feeds=lambda owner: Scanner(),
    )
    for _ in range(24 * 12):
        # Recreate each tick: persisted due times must also withstand restarts.
        worker = SourceRefresh.__new__(SourceRefresh)
        worker.box = box
        worker.stop = threading.Event()
        worker.tick()
        now[0] += 300
    assert calls == {account_id: 144, other: 24}
    assert repos.feeds.list_for(account_id)[0]["error"]
    assert not repos.feeds.list_for(other)[0]["error"]
