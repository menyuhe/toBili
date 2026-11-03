"""Multi-account regressions: only isolated homes and mocked platform calls."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from src import accounts
from src.account_context import account_scope


def cookie(uid):
    return f"SESSDATA=fake-{uid}; bili_jct={'a' * 32}; DedeUserID={uid}"


@pytest.fixture(autouse=True)
def sandbox(isolated_home, monkeypatch):
    import httpx
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda *a, **k: pytest.fail("Unexpected network request"))


def test_independent_credentials_context_threads_and_logout():
    from src.bilibili_auth import get_login_uid
    for uid in ("101", "202"):
        accounts.register_cookie(cookie(uid))
    barrier = threading.Barrier(2)
    def read(uid):
        with account_scope(uid):
            barrier.wait(timeout=3)
            return get_login_uid()
    with ThreadPoolExecutor(2) as executor:
        a = executor.submit(read, "101")
        b = executor.submit(read, "202")
        assert [a.result(), b.result()] == [101, 202]
    accounts.logout("101")
    with account_scope("101"):
        assert get_login_uid() is None
    with account_scope("202"):
        assert get_login_uid() == 202
    with pytest.raises(ValueError):
        accounts.register_cookie(cookie("202"), expected_uid="101")


def test_activity_archive_settings_and_pool_isolated():
    from src.activity_store import append_activities, load_activities, update_activity
    from src.activity_archive import archive_activity_ids, archived_activity_ids
    from src.user_settings import set_participate_text, get_participate_text
    from src.participation_store import set_participation, load_participations
    for uid in ("101", "202"):
        accounts.register_cookie(cookie(uid))
    did = "123456789012345678"
    with account_scope("101"):
        append_activities([{"dynamic_id": did, "draw_status": "active", "platform_participated": True,
                            "reserve_reserved": True, "activity_status": "已参加"}])
        archive_activity_ids({did})
        set_participate_text("A的评论")
        set_participation(did, "已参加")
        assert load_activities()[0]["platform_participated"] is True
    with account_scope("202"):
        item = load_activities()[0]
        assert not item.get("platform_participated")
        assert not item.get("reserve_reserved")
        assert item["activity_status"] != "已参加"
        assert not archived_activity_ids()
        assert not load_participations()
        assert get_participate_text() != "A的评论"
        update_activity(did, {**item, "reserve_reserved": False})
    with account_scope("101"):
        assert load_activities()[0]["reserve_reserved"] is True
        assert archived_activity_ids() == {did}


def test_migration_is_idempotent_and_preserves_unknown_owner(isolated_home):
    from src.activity_store import append_activities, load_activities
    from src.activity_archive import archive_activity_ids, archived_activity_ids
    from src.db.models import ParticipationRow
    from src.db.session import session_scope
    legacy = isolated_home / "config" / "cookies.txt"
    legacy.write_text(cookie("101"), encoding="utf-8")
    append_activities([{"dynamic_id": "123", "platform_participated": True}])
    archive_activity_ids({"123"})
    with session_scope() as session:
        session.add(ParticipationRow(uid="__legacy__", dynamic_id="123", user_status="已参加"))
    accounts.migrate_legacy()
    accounts.migrate_legacy()
    assert len(accounts.list_accounts()) == 1
    assert legacy.with_name("cookies.pre-multi-account.txt").exists()
    accounts.register_cookie(cookie("202"))
    with account_scope("101"):
        assert load_activities()[0]["platform_participated"] is True
        assert archived_activity_ids() == {"123"}
    with account_scope("202"):
        assert not load_activities()[0].get("platform_participated")
        assert not archived_activity_ids()
    with session_scope() as session:
        assert session.get(ParticipationRow, ("__legacy__", "123"))
        assert not session.get(ParticipationRow, ("202", "123"))


def test_jobs_run_concurrently_with_fixed_identity(monkeypatch):
    from web.account_jobs import AccountJobRunner
    from web.job_runner import JobRunner
    from src.bilibili_auth import get_login_uid
    for uid in ("101", "202"):
        accounts.register_cookie(cookie(uid))
    broker = AccountJobRunner(JobRunner)
    barrier = threading.Barrier(2)
    identities = []
    def action(*args, **kwargs):
        identities.append(get_login_uid())
        barrier.wait(timeout=5)
        return {"ok": True, "message": "mock done"}
    monkeypatch.setattr("web.job_runner.run_action", action)
    monkeypatch.setattr("src.cloud_client.load_connection", lambda: None)
    with account_scope("101"):
        a = broker.try_start("participate", {})
    with account_scope("202"):
        b = broker.try_start("participate", {})
    deadline = time.monotonic() + 8
    while broker.any_running() and time.monotonic() < deadline:
        time.sleep(.01)
    assert sorted(identities) == [101, 202]
    assert broker.for_uid("101").get_status().state == "success"
    assert broker.for_uid("202").get_status().state == "success"
    with account_scope("202"):
        with pytest.raises(ValueError):
            broker.resolve_job_status(a)
        assert broker.resolve_job_status(b).uid == "202"


def test_scoped_cookie_ignores_environment(monkeypatch):
    from src.bilibili_auth import get_login_uid
    monkeypatch.setenv("BILI_COOKIE", cookie("999"))
    accounts.register_cookie(cookie("101"))
    with account_scope("101"):
        assert get_login_uid() == 101
    with account_scope(""):
        assert get_login_uid() is None


def test_same_account_queue_is_serial_and_cancellable(monkeypatch):
    from web.account_jobs import AccountJobRunner
    from web.job_runner import JobRunner
    from src.job_store import get_job
    accounts.register_cookie(cookie("101"))
    broker = AccountJobRunner(JobRunner)
    release, entered = threading.Event(), threading.Event()
    order = []
    def action(action, params, **kwargs):
        order.append(params["number"])
        entered.set()
        assert release.wait(5)
        return {"ok": True}
    monkeypatch.setattr("web.job_runner.run_action", action)
    monkeypatch.setattr("src.cloud_client.load_connection", lambda: None)
    with account_scope("101"):
        first = broker.try_start("participate", {"number": 1})
        assert entered.wait(3)
        second = broker.try_start("participate", {"number": 2})
        third = broker.try_start("participate", {"number": 3})
        assert get_job(second)["state"] == "queued"
        assert broker.cancel(third)
    assert order == [1]
    release.set()
    deadline = time.monotonic() + 8
    while broker.any_running() and time.monotonic() < deadline:
        time.sleep(.01)
    assert order == [1, 2]
    assert get_job(second)["state"] == "success"
    assert get_job(third)["state"] == "cancelled"


def test_article_owner_and_candidates_do_not_change_with_selection():
    from web.article_routes import account_uid, article_rows
    from src.activity_archive import archive_activity_ids
    from src.activity_store import append_activities
    for uid in ("101", "202"):
        accounts.register_cookie(cookie(uid))
    with account_scope("101"):
        append_activities([{"dynamic_id": "123", "lottery_type": "转发抽奖", "platform_participated": True}])
        archive_activity_ids({"123"})
        assert account_uid() == "101"
        assert "123" in article_rows()
    with account_scope("202"):
        assert account_uid() == "101"
        assert "123" in article_rows()


def test_api_requires_identity_and_does_not_cross_account_history():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.account_routes import AccountMiddleware
    from src.bilibili_auth import get_login_uid
    for uid in ("101", "202"):
        accounts.register_cookie(cookie(uid))
    app = FastAPI()
    app.add_middleware(AccountMiddleware)
    @app.get("/api/identity")
    def identity():
        return {"uid": get_login_uid()}
    client = TestClient(app)
    assert client.get("/api/identity").status_code == 400
    assert client.get("/api/identity", headers={"X-Account-UID": "999"}).status_code == 404
    for uid in ("101", "202"):
        assert client.get("/api/identity", headers={"X-Account-UID": uid}).json() == {"uid": int(uid)}


def test_account_list_exposes_owned_task_steps_and_shared_actor(monkeypatch):
    from types import SimpleNamespace
    from web.job_runner import JobStatus
    from web.account_routes import get_accounts
    for uid in ("101", "202"):
        accounts.register_cookie(cookie(uid))
    jobs = {
        "101": JobStatus(uid="101", state="running", action="participate_pool", progress_message="正在关注（1/6）", progress_step=2, progress_total=15),
        "202": JobStatus(uid="202"),
        "__global__": JobStatus(uid="202", scope="global", state="running", action="refresh_all", progress_message="更新数据源"),
    }
    broker = SimpleNamespace(for_uid=lambda uid: SimpleNamespace(get_status=lambda: jobs[uid]))
    monkeypatch.setattr("web.job_runner.runner", broker)
    items = {item["uid"]: item for item in get_accounts()["items"]}
    assert items["101"]["job"]["progress_message"] == "正在关注（1/6）"
    assert items["101"]["job"]["progress_total"] == 15
    assert items["202"]["job"]["action"] == "refresh_all"
    assert items["202"]["job"]["scope"] == "global"
    jobs["__global__"].uid = "101"
    items = {item["uid"]: item for item in get_accounts()["items"]}
    assert items["101"]["job"]["action"] == "participate_pool"
    assert items["202"]["job"]["state"] == "idle"
