"""定时自动化的接口。

只测"接口把话说清楚"这件事：没登录能不能启、参数越界挡不挡、停止是否幂等。
真正跑一轮的逻辑在 `tests/unit/test_schedule.py`，那里不碰 HTTP。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tobili.api.app import create_app
from tobili.ports.models import AccountProfile


@pytest.fixture
def client(tmp_path):  # type: ignore[no-untyped-def]
    app = create_app(home=str(tmp_path / "home"))
    with TestClient(app) as test_client:
        app.state.container.repos.accounts.upsert(AccountProfile(uid="11", nickname="A"), "a")
        yield test_client


def test_status_starts_stopped(client: TestClient) -> None:
    body = client.get("/api/schedule").json()
    assert body["running"] is False
    assert body["interval_min"] == 60
    # 默认必须是预演。定时 + 自动写是唯一一种"人不在场也会改线上状态"的组合。
    assert body["apply"] is False
    assert body["next_at"] is None
    assert body["runs"] == []


def test_interval_out_of_range_is_rejected(client: TestClient) -> None:
    # 5 分钟以下太密，B 站那边会当成刷；一天以上等于没开。
    assert (
        client.post(
            "/api/schedule", json={"account_id": 1, "interval_min": 1, "apply": False}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/schedule", json={"account_id": 1, "interval_min": 5000, "apply": False}
        ).status_code
        == 422
    )


def test_start_then_stop(client: TestClient) -> None:
    body = client.post(
        "/api/schedule", json={"account_id": 1, "interval_min": 30, "apply": False}
    ).json()
    assert body["running"] is True
    assert body["interval_min"] == 30
    assert body["next_at"] is not None

    stopped = client.delete("/api/schedule?account_id=1").json()
    assert stopped["running"] is False
    assert stopped["next_at"] is None


def test_stop_is_idempotent(client: TestClient) -> None:
    """关一个已经关掉的东西不该报错 —— 界面上的按钮可能被点两次。"""
    assert client.delete("/api/schedule?account_id=1").status_code == 200
    assert client.delete("/api/schedule?account_id=1").status_code == 200


def test_state_survives_a_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """配置落库：重启后还记不记得上次的间隔与模式。"""
    home = str(tmp_path / "home")
    with TestClient(create_app(home=home)) as first:
        first.app.state.container.repos.accounts.upsert(AccountProfile(uid="11", nickname="A"), "a")
        first.app.state.container.vault.write("a", "fixture")
        first.post("/api/schedule", json={"account_id": 1, "interval_min": 45, "apply": False})

    with TestClient(create_app(home=home)) as second:
        body = second.get("/api/schedule").json()
        assert body["interval_min"] == 45
        # 上次开着，重启要自动接上；否则"定时"在重启后就静默失效了。
        assert body["running"] is True
        second.delete("/api/schedule?account_id=1")


def test_once_reports_failure_instead_of_500(client: TestClient) -> None:
    """没有账号时跑一轮应该回一个可读的结果，而不是 500。"""
    body = client.post("/api/schedule/once", json={"account_id": 1}).json()
    assert body["ok"] is False
    assert "凭据" in body["note"] or "账号" in body["note"]


def test_two_owners_and_stale_write(client):
    box = client.app.state.container
    key = box.repos.accounts.upsert(AccountProfile(uid="22", nickname="B"), "b")
    box.repos.accounts.mark_current(1)
    assert client.post(
        "/api/schedule", json={"interval_min": 15, "account_id": 1, "selection": "queue"}
    ).json()["running"]
    assert (
        client.post("/api/schedule/once", json={"account_id": 1, "apply": True}).status_code == 409
    )
    box.repos.accounts.mark_current(key)
    assert not client.get("/api/schedule").json()["running"]
    assert (
        client.post(
            "/api/schedule/once", json={"account_id": 1, "apply": True, "confirmed": True}
        ).status_code
        == 409
    )
    plan = box.repos.tidy.create_plan(1, now=box.clock.now())
    for suffix, body in (
        ("confirm", {}),
        ("run", {"apply": True}),
        ("items/select", {"item_ids": []}),
    ):
        assert client.post(f"/api/tidy/plans/{plan}/{suffix}", json=body).status_code == 404


def test_two_schedules_resume_independently_on_one_machine(tmp_path):
    home = str(tmp_path / "two-accounts")
    with TestClient(create_app(home=home)) as first:
        box = first.app.state.container
        for uid, interval in [("11", 20), ("22", 45)]:
            owner = box.repos.accounts.upsert(AccountProfile(uid=uid, nickname=uid), uid)
            box.vault.write(uid, "test")
            box.repos.accounts.mark_current(owner)
            started = first.post(
                "/api/schedule",
                json={"account_id": owner, "interval_min": interval, "apply": False},
            )
            assert started.json()["running"]
        assert all(box.schedule(i).status().running for i in [1, 2])
    with TestClient(create_app(home=home)) as second:
        box = second.app.state.container
        for owner, minutes in [(1, 20), (2, 45)]:
            timer = box.schedule(owner).status()
            assert timer.running and timer.interval_min == minutes
        box.repos.accounts.mark_current(2)
        second.delete("/api/schedule?account_id=2").raise_for_status()
        assert box.schedule(1).status().running and not box.schedule(2).status().running
