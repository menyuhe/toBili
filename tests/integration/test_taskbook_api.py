from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tobili.api.app import create_app
from tobili.ports.models import AccountProfile
from tobili.runtime.taskbook import TaskBook


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(TaskBook, "start", lambda self: None)
    app = create_app(home=str(tmp_path))
    with TestClient(app) as client:
        box = app.state.container
        box.repos.accounts.upsert(AccountProfile(uid="11", nickname="A"), "a")
        box.has_session = lambda owner: True
        box.check_writer = None
        yield client, box


def config(client, **kw):
    view = client.get("/api/tasks").json()
    return {
        k: view[k] for k in ("account_id", "running", "apply", "selection", "limit", "tasks")
    } | kw


def test_scheduled_countdown_is_separate_from_ordinary_source_refresh(setup):
    client, box = setup
    owner = client.get("/api/tasks").json()["account_id"]
    box.repos.feeds.add(owner, "topic", "1", "测试话题")
    box.repos.settings.put(owner, "sources.refresh", "1")
    view = client.get("/api/tasks").json()
    assert view["next"]["key"] == "source"
    assert view["next_scheduled"] is None
    body = config(client, running=True)
    for t in body["tasks"]:
        t["enabled"] = t["key"] == "messages"
    view = client.put("/api/tasks", json=body).json()
    assert view["next_scheduled"]["key"] == "messages"
    assert view["next_scheduled"]["at"] > 0
    box.repos.settings.put(owner, "tasks.due.messages", "100")
    assert client.get("/api/tasks").json()["next_scheduled"]["at"] == 100
    assert client.get("/api/tasks").json()["next_scheduled"]["at"] == 100
    view = client.delete("/api/tasks", params={"account_id": owner}).json()
    assert view["next_scheduled"] is None


def test_task_config_account_switch_confirmation_and_validation(setup):
    client, box = setup
    body = config(client, running=True, apply=True)
    assert client.put("/api/tasks", json=body).status_code == 409
    body["confirmed"] = True
    assert client.put("/api/tasks", json=body).is_success
    assert client.get("/api/tasks").json()["running"]
    assert client.post("/api/schedule", json={"account_id": 1}).status_code == 409
    other = box.repos.accounts.upsert(AccountProfile(uid="22", nickname="B"), "b")
    box.repos.accounts.mark_current(other)
    assert client.put("/api/tasks", json=body).status_code == 409
    assert not client.get("/api/tasks").json()["running"]
    assert box.tasks.config(1)["running"]
    body = config(client)
    body["tasks"][0]["minutes"] = 5
    assert client.put("/api/tasks", json=body).status_code == 422
    body["tasks"][0]["minutes"] = 60
    body["tasks"][0] = body["tasks"][1]
    assert client.put("/api/tasks", json=body).status_code == 422


def test_sources_take_over_once_and_readonly_jobs_never_enable_writes(setup):
    client, box = setup
    box.repos.settings.put(1, "sources.refresh", "1")
    body = config(client, running=True)
    for t in body["tasks"]:
        t["enabled"] = t["key"] == "topics"
    assert client.put("/api/tasks", json=body).is_success
    assert box.repos.settings.get(1, "sources.refresh") == "0"
    assert (
        client.put("/api/feeds/refresh", json={"account_id": 1, "enabled": True}).status_code == 409
    )
    seen = []
    box.plan_tidy = lambda: SimpleNamespace(
        run=lambda owner, **kw: seen.append((owner, kw)) or SimpleNamespace(lines=[], clearable=0)
    )
    # Even a plan saved with apply=True must never delete as part of a cleanup check.
    out = box.tasks.run(1, "cleanup", body, apply=True)
    assert out["ok"] and seen == [(1, {"limit": 50, "persist": False})]
    called = []
    box.run_pool = lambda owner: SimpleNamespace(
        run=lambda account, **kw: (
            called.append((owner, account, kw))
            or SimpleNamespace(failed=0, attempted=3, joined=0, note="preview")
        )
    )
    result = client.post(
        "/api/tasks/once", json={"account_id": 1, "key": "participate", "limit": 3}
    )
    assert result.is_success and len(called) == 1
    assert called[0][:2] == (1, 1)
    assert called[0][2]["apply"] is False and called[0][2]["limit"] == 3
    assert called[0][2]["before_each"](0) == ""


def test_durable_due_history_and_stopped_round_cannot_write(setup, monkeypatch):
    client, box = setup
    body = config(client, running=True)
    now = [1800000000.0]
    monkeypatch.setattr("tobili.runtime.taskbook.time.time", lambda: now[0])
    assert client.put("/api/tasks", json=body).is_success
    calls = []
    box.run_pool = lambda owner: SimpleNamespace(
        run=lambda account, **kw: (
            calls.append(kw) or SimpleNamespace(failed=0, attempted=0, joined=0, note="empty")
        )
    )
    box.tasks.tick(1)
    assert not calls
    now[0] += 3601
    restarted = TaskBook(box)
    restarted.tick(1)
    restarted.tick(1)
    assert len(calls) == 1
    assert TaskBook(box).view(1)["states"]["participate"]["history"][0]["note"] == "empty"
    previous = restarted.config(1)
    client.delete("/api/tasks?account_id=1")
    restarted.run(1, "participate", previous, apply=True, scheduled=True)
    assert len(calls) == 1


def test_source_jobs_filter_by_kind(setup):
    client, box = setup
    topic = box.repos.feeds.add(1, "topic", "1", "topic")
    box.repos.feeds.add(1, "collection", "2", "collection")
    seen = []
    box.scan_feeds = lambda owner: SimpleNamespace(
        run=lambda account, **kw: (
            seen.append((owner, account, kw["feed_id"]))
            or SimpleNamespace(problems=[], scanned=2, matched=1)
        )
    )
    assert client.post("/api/tasks/once", json={"account_id": 1, "key": "topics"}).is_success
    assert seen == [(1, 1, topic)]


def test_partial_sources_use_normal_interval_and_stagger_other_sources(setup, monkeypatch):
    client, box = setup
    first = box.repos.feeds.add(1, "topic", "1", "first")
    second = box.repos.feeds.add(1, "topic", "2", "second")
    now = [1800000000.0]
    monkeypatch.setattr("tobili.runtime.taskbook.time.time", lambda: now[0])
    body = config(client, running=True)
    for task in body["tasks"]:
        task["enabled"] = task["key"] == "topics"
    assert client.put("/api/tasks", json=body).is_success
    seen = []

    def scan(account, **kw):
        feed = kw["feed_id"]
        seen.append(feed)
        cursor = (
            'v2:{"latest":"next"}'
            if feed == first and seen.count(first) == 1
            else 'v2:{"latest":""}'
        )
        box.repos.feeds.save_cursor(feed, cursor, now=box.clock.now())
        return SimpleNamespace(problems=[], scanned=2, matched=1)

    box.scan_feeds = lambda owner: SimpleNamespace(run=scan)
    now[0] += 3601
    box.tasks.tick(1)
    assert seen == [first]
    now[0] += 121
    TaskBook(box).tick(1)
    assert seen == [first]
    now[0] += 180
    TaskBook(box).tick(1)
    assert seen == [first, second]
    now[0] += 3300
    TaskBook(box).tick(1)
    assert seen == [first, second, first]
    box.check_writer = lambda owner: (_ for _ in ()).throw(RuntimeError("writer owned elsewhere"))
    called = []
    box.run_pool = lambda owner: called.append(owner)
    result = box.tasks.run(1, "participate", {"selection": "queue", "limit": 10}, apply=True)
    assert not result["ok"] and called == []


def test_edit_running_preserves_other_due_and_stops_between_items(setup):
    client, box = setup
    body = config(client, running=True)
    assert client.put("/api/tasks", json=body).is_success
    due = box.tasks.view(1)["states"]["participate"]["next_at"]
    body["tasks"][0]["minutes"] = 120
    assert client.put("/api/tasks", json=body).is_success
    assert box.tasks.view(1)["states"]["participate"]["next_at"] == due
    saved = box.tasks.config(1)
    observed = []

    def run(account, **kw):
        before = kw["before_each"]
        assert before(0) == ""
        assert box.tasks.view(1)["active"]["key"] == "participate"
        # Saving an unrelated task while the account lane is held must succeed.
        updated = box.tasks.config(1)
        updated["tasks"][0]["minutes"] = 180
        box.tasks.save(1, updated)
        assert before(1) == ""
        box.tasks.pause(1)
        observed.append(before(2))
        return SimpleNamespace(failed=0, attempted=2, joined=0, note=observed[-1])

    box.run_pool = lambda owner: SimpleNamespace(run=run)
    result = box.tasks.run(1, "participate", saved, scheduled=True)
    assert "停止" in observed[0] and result["scanned"] == 2
    assert not box.tasks.view(1)["active"]


def test_hourly_attempt_budget_persists_failure_and_preview_is_free(setup):
    client, box = setup
    seen = []

    def run(account, **kw):
        for i in range(3):
            reason = kw["before_each"](i)
            if reason:
                break
            seen.append(i)
        return SimpleNamespace(failed=0, attempted=len(seen), joined=0, note=reason)

    box.run_pool = lambda owner: SimpleNamespace(run=run)
    body = {"account_id": 1, "key": "participate", "apply": True, "hourly_limit": 2}
    assert client.post("/api/tasks/once", json=body).status_code == 409
    assert not seen
    body["confirmed"] = True
    result = client.post("/api/tasks/once", json=body).json()
    assert seen == [0, 1] and "每小时" in result["note"]
    assert TaskBook(box).view(1)["hourly_used"] == 2
    client.post("/api/tasks/once", json=body)
    assert seen == [0, 1]
    body["apply"] = False
    client.post("/api/tasks/once", json=body)
    assert len(seen) == 5 and box.tasks.view(1)["hourly_used"] == 2


def test_one_account_does_not_block_another(setup):
    import threading

    client, box = setup
    other = box.repos.accounts.upsert(AccountProfile(uid="22", nickname="B"), "b")
    reached = threading.Event()
    release = threading.Event()
    box.tasks.save(1, config(client, running=True))
    box.tasks.save(other, config(client, running=True))
    for owner in (1, other):
        box.repos.settings.put(owner, "tasks.due.participate", "0")

    def run(account, **kw):
        if account == 1:
            reached.set()
            release.wait(3)
        return SimpleNamespace(failed=0, attempted=0, joined=0, note="done")

    box.run_pool = lambda owner: SimpleNamespace(run=run)
    thread = threading.Thread(target=box.tasks._tick_safe, args=(1,))
    thread.start()
    try:
        assert reached.wait(1)
        box.tasks._tick_safe(other)
        assert box.tasks.view(other)["states"]["participate"]["history"]
    finally:
        release.set()
        thread.join(3)


def test_write_failure_stops_participation_but_keeps_read_jobs(setup):
    client, box = setup
    body = config(client, running=True, apply=True, confirmed=True)
    body["tasks"][0]["enabled"] = True
    assert client.put("/api/tasks", json=body).is_success
    box.run_pool = lambda owner: SimpleNamespace(
        run=lambda account, **kw: SimpleNamespace(failed=1, attempted=1, joined=0, note="failed")
    )
    old = box.tasks.config(1)
    result = box.tasks.run(1, "participate", old, apply=True, scheduled=True)
    assert "已停用" in result["note"]
    current = box.tasks.config(1)
    assert current["running"]
    assert not next(t for t in current["tasks"] if t["key"] == "participate")["enabled"]
    assert box.tasks.run(1, "participate", old, apply=True, scheduled=True)["scanned"] == 0


def test_busy_account_defers_without_disabling_scheduled_participation(setup):
    client, box = setup
    body = config(client, running=True, apply=True, confirmed=True)
    assert client.put("/api/tasks", json=body).is_success
    with box.lanes.exclusive(1):
        result = box.tasks.run(1, "participate", box.tasks.config(1), apply=True, scheduled=True)
    assert not result["ok"] and "稍后" in result["note"]
    assert box.tasks.config(1)["running"]
    assert next(t for t in box.tasks.config(1)["tasks"] if t["key"] == "participate")["enabled"]


def test_many_sources_all_receive_a_turn_before_revisiting_first(setup, monkeypatch):
    client, box = setup
    feeds = [box.repos.feeds.add(1, "topic", str(i), str(i)) for i in range(14)]
    now = [1800000000.0]
    monkeypatch.setattr("tobili.runtime.taskbook.time.time", lambda: now[0])
    body = config(client, running=True)
    for task in body["tasks"]:
        task["enabled"] = task["key"] == "topics"
    assert client.put("/api/tasks", json=body).is_success
    seen = []
    box.scan_feeds = lambda owner: SimpleNamespace(
        run=lambda account, **kw: (
            seen.append(kw["feed_id"]) or SimpleNamespace(problems=[], scanned=1, matched=1)
        )
    )
    now[0] += 3601
    for _ in feeds:
        # Recreate the scheduler to also verify durable ordering across restarts.
        TaskBook(box).tick(1)
        now[0] += 301
    assert seen == feeds


def test_due_participation_runs_before_background_discovery(setup, monkeypatch):
    client, box = setup
    now = [1800000000.0]
    monkeypatch.setattr("tobili.runtime.taskbook.time.time", lambda: now[0])
    body = config(client, running=True)
    for task in body["tasks"]:
        task["enabled"] = task["key"] in ("participate", "topics")
    assert client.put("/api/tasks", json=body).is_success
    calls = []
    box.tasks.run = lambda owner, key, *a, **kw: calls.append(key) or {"note": "ok"}
    now[0] += 3601
    box.tasks.tick(1)
    assert calls == ["participate"]


def test_catalog_consumes_selected_shared_items_across_rounds_without_duplicates(setup):
    from tobili.domain.campaign import Campaign, CampaignKind
    from tobili.domain.enrollment import Stage
    from tobili.domain.pool import PoolItem
    from tobili.usecases.enroll import EnrollReport

    client, box = setup
    ids = [
        box.repos.campaigns.upsert(
            Campaign(
                dyn_id=str(90000 + i),
                up_uid="up",
                title="奖品",
                kind=CampaignKind.SHARE,
                confidence=0,
            )
        )
        for i in range(43)
    ]
    box.repos.pool.save(1, [PoolItem(cid, i) for i, cid in enumerate(ids)], now=box.clock.now())
    # Another account's queue must be untouched.
    other = box.repos.accounts.upsert(AccountProfile(uid="22", nickname="B"), "b")
    box.repos.pool.save(other, [PoolItem(ids[0])], now=box.clock.now())
    seen = []

    def enroll(owner, dyn, **kw):
        assert owner == 1 and not kw["dry_run"]
        seen.append(dyn)
        return EnrollReport(dyn, box.repos.campaigns.by_dyn(dyn)[0], 1, Stage.SETTLED)

    box._enroll = lambda owner: SimpleNamespace(run=enroll)
    body = config(client, selection="catalog", limit=15, hourly_limit=50)
    for expected in [15, 15, 13]:
        result = TaskBook(box).run(1, "participate", body, apply=True)
        assert result["ok"] and result["written"] == expected
    assert len(seen) == len(set(seen)) == 43
    assert box.repos.pool.states(1) == []
    assert len(box.repos.pool.states(other)) == 1
    final = TaskBook(box).run(1, "participate", body, apply=True)
    assert final["written"] == 0 and "参与方式待核对" in final["note"]


def test_original_post_found_expired_is_not_reported_as_participated(setup):
    from tobili.domain.campaign import Campaign, CampaignKind
    from tobili.domain.enrollment import Stage
    from tobili.domain.pool import PoolItem
    from tobili.usecases.enroll import EnrollReport

    client, box = setup
    cid = box.repos.campaigns.upsert(
        Campaign(dyn_id="8990", up_uid="up", title="奖品", kind=CampaignKind.SHARE)
    )
    box.repos.pool.save(1, [PoolItem(cid)], now=box.clock.now())
    box._enroll = lambda _: SimpleNamespace(
        run=lambda *a, **kw: EnrollReport("8990", cid, 1, Stage.SETTLED, skipped=True)
    )
    result = box.tasks.run(1, "participate", config(client, selection="catalog"), apply=True)
    assert result["written"] == 0 and "跳过 1" in result["note"]
    assert box.repos.pool.states(1) == []


def test_catalog_respects_manually_paused_queue(setup):
    client, box = setup
    box.repos.pool.set_paused(1, True, note="手动暂停", now=box.clock.now())
    box.batch_enroll = lambda _: (_ for _ in ()).throw(AssertionError("must not bypass pause"))
    result = box.tasks.run(1, "participate", config(client, selection="catalog"), apply=True)
    assert not result["ok"] and "暂停" in result["note"]


def test_queue_expired_rows_do_not_consume_live_batch_limit(setup):
    from datetime import timedelta

    from tobili.domain.campaign import Campaign, CampaignKind
    from tobili.domain.enrollment import Stage
    from tobili.domain.pool import PoolItem
    from tobili.usecases.enroll import EnrollReport

    client, box = setup
    expired = [
        box.repos.campaigns.upsert(
            Campaign(
                dyn_id=str(7000 + i),
                up_uid="up",
                title="过期",
                kind=CampaignKind.SHARE,
                deadline=box.clock.now() - timedelta(days=1),
            )
        )
        for i in range(20)
    ]
    active = box.repos.campaigns.upsert(
        Campaign(dyn_id="8000", up_uid="up", title="未截止", kind=CampaignKind.SHARE)
    )
    box.repos.pool.save(
        1, [PoolItem(cid, i) for i, cid in enumerate([*expired, active])], now=box.clock.now()
    )
    seen = []
    box._enroll = lambda _: SimpleNamespace(
        run=lambda owner, dyn, **kw: seen.append(dyn) or EnrollReport(dyn, active, 1, Stage.SETTLED)
    )
    result = box.tasks.run(
        1, "participate", config(client, selection="catalog", limit=1), apply=True
    )
    assert result["ok"] and result["written"] == 1 and seen == ["8000"]
    assert box.repos.pool.states(1) == []
