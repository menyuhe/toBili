import time
from contextlib import contextmanager
from types import SimpleNamespace

from test_queue_review import seed
from test_taskbook_api import config
from test_taskbook_api import setup as setup

from tobili.domain.action import Action, ActionKind
from tobili.domain.enrollment import Stage
from tobili.domain.errors import TemporaryWriteUnavailable, WriteUnavailable
from tobili.domain.receipt import Receipt
from tobili.runtime.journal import Journal
from tobili.runtime.taskbook import TaskBook
from tobili.usecases.enroll import EnrollReport


def enabled(client, box):
    cfg = config(
        client, running=True, apply=True, confirmed=True, selection="queue", hourly_limit=50
    )
    assert client.put("/api/tasks", json=cfg).is_success
    return box.tasks.config(1)


def test_timeout_before_write_survives_restart_and_recovery_clears_latest(setup, monkeypatch):
    client, box = setup
    cfg = enabled(client, box)
    now = [time.time()]
    monkeypatch.setattr("tobili.runtime.taskbook.time.time", lambda: now[0])

    def offline(owner):
        raise TemporaryWriteUnavailable("backup timeout")

    box.check_writer = offline
    result = box.tasks.run(1, "participate", cfg, apply=True, scheduled=True)
    assert result["retrying"] and not result["ok"]
    assert box.tasks.config(1) == cfg
    calls = []
    box.check_writer = lambda owner: None
    box.run_pool = lambda owner: SimpleNamespace(
        run=lambda *a, **k: (
            calls.append(1) or SimpleNamespace(failed=0, attempted=0, joined=0, note="队列已完成")
        )
    )
    # The original period is still in the future; the explicit retry must take priority.
    box.repos.settings.put(1, "tasks.due.participate", str(now[0] + 900))
    restarted = TaskBook(box)
    restarted.tick(1)
    assert not calls
    now[0] += 61
    restarted.tick(1)
    restarted.tick(1)
    assert calls == [1]
    state = restarted.view(1)["states"]["participate"]
    assert state["latest"]["ok"] and state["latest"]["note"] == "队列已完成"
    assert not state["history"][1]["ok"]  # Historical failure is retained, not the current warning.
    assert float(box.repos.settings.get(1, "tasks.retry.participate")) == 0
    assert restarted.config(1) == cfg


def test_mid_item_timeout_does_not_repeat_completed_share_or_comment(setup, gateway):
    client, box = setup
    ids = seed(box, 1)
    cfg = enabled(client, box)
    outage = [True]

    @contextmanager
    def fence(owner):
        if outage[0] and gateway.writes:
            raise TemporaryWriteUnavailable("backup timeout before comment")
        yield

    journal = Journal(
        gateway=gateway, intents=box.repos.intents, clock=box.clock, write_scope=fence
    )

    def run(owner, dyn, **kw):
        for kind in (ActionKind.SHARE, ActionKind.COMMENT):
            journal.attempt(account_id=owner, campaign_id=ids[0], action=Action(kind, dyn, {}))
        return EnrollReport(dyn, ids[0], 1, Stage.SETTLED)

    box._enroll = lambda _: SimpleNamespace(run=run)
    failed = box.tasks.run(1, "participate", cfg, apply=True, scheduled=True)
    assert failed["retrying"] and box.tasks.config(1) == cfg
    assert not box.repos.pool.paused(1)[0]
    outage[0] = False
    result = client.post(
        "/api/tasks/once", json=dict(account_id=1, key="participate", apply=True, confirmed=True)
    ).json()
    assert result["ok"]
    assert [a.kind for a in gateway.writes] == [ActionKind.SHARE, ActionKind.COMMENT]
    box.tasks.run(1, "participate", cfg, apply=True)
    assert len(gateway.writes) == 2
    assert client.get("/api/tasks").json()["states"]["participate"]["latest"]["ok"]


def test_unknown_share_is_not_resent_after_connection_recovers(setup, gateway):
    client, box = setup
    cid = seed(box, 1)[0]
    journal = Journal(gateway=gateway, intents=box.repos.intents, clock=box.clock)
    action = Action(ActionKind.SHARE, "99000", {})
    gateway.receipt = Receipt.unclear("lost Bilibili response")
    assert journal.attempt(account_id=1, campaign_id=cid, action=action).outcome.value == "unknown"
    cfg = enabled(client, box)
    box.check_writer = lambda _: (_ for _ in ()).throw(TemporaryWriteUnavailable("backup timeout"))
    assert box.tasks.run(1, "participate", cfg, apply=True, scheduled=True)["retrying"]
    box.check_writer = None
    gateway.receipt = Receipt.success("remote-id")
    assert journal.attempt(account_id=1, campaign_id=cid, action=action).outcome.value == "unknown"
    assert len(gateway.writes) == 1


def test_permanent_denial_still_disables_and_preview_does_not_clear_error(setup):
    client, box = setup
    cfg = enabled(client, box)
    box.check_writer = lambda _: (_ for _ in ()).throw(
        WriteUnavailable("different execution owner")
    )
    failed = box.tasks.run(1, "participate", cfg, apply=True, scheduled=True)
    assert not failed["ok"] and not failed.get("retrying")
    assert not next(t for t in box.tasks.config(1)["tasks"] if t["key"] == "participate")["enabled"]
    box.tasks.run(1, "participate", cfg, apply=False)
    assert not box.tasks.view(1)["states"]["participate"]["latest"]["ok"]


def test_manual_pause_during_timeout_is_not_reenabled(setup):
    client, box = setup
    cfg = enabled(client, box)

    def timeout(owner):
        box.tasks.pause(owner)
        raise TemporaryWriteUnavailable("timeout")

    box.check_writer = timeout
    assert box.tasks.run(1, "participate", cfg, apply=True, scheduled=True)["retrying"]
    assert not box.tasks.config(1)["running"]
    assert not box.repos.settings.get(1, "tasks.retry.participate", "")
