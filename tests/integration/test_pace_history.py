import pytest
from fastapi.testclient import TestClient

from tobili.api.app import create_app
from tobili.ports.models import AccountProfile
from tobili.runtime.taskbook import TaskBook


@pytest.fixture
def client_box(tmp_path, monkeypatch):
    monkeypatch.setattr(TaskBook, "start", lambda self: None)
    app = create_app(home=str(tmp_path))
    with TestClient(app) as client:
        box = app.state.container
        box.repos.accounts.upsert(AccountProfile(uid="101", nickname="A"), "a")
        yield client, box


def test_pace_is_account_scoped_and_used_by_quota(client_box):
    client, box = client_box
    assert client.get("/api/settings/pace").json()["interval_seconds"] == 15
    for value in [0, 0.8, 5, 15, 120]:
        assert client.put(
            "/api/settings/pace", json={"account_id": 1, "interval_seconds": value}
        ).is_success
        assert box.quota(1).budget.min_gap_seconds == value
        assert client.get("/api/quota").json()["budget"]["min_gap_seconds"] == value
    for value in [-1, 3601]:
        assert (
            client.put(
                "/api/settings/pace", json={"account_id": 1, "interval_seconds": value}
            ).status_code
            == 422
        )
    other = box.repos.accounts.upsert(AccountProfile(uid="202", nickname="B"), "b")
    box.repos.accounts.mark_current(other)
    assert client.get("/api/settings/pace").json()["interval_seconds"] == 15
    assert (
        client.put("/api/settings/pace", json={"account_id": 1, "interval_seconds": 0}).status_code
        == 409
    )
    assert box.quota(1).budget.min_gap_seconds == 120


def test_older_issue_survives_normal_log_flood_and_account_filter(client_box):
    client, box = client_box
    box.bus.publish(
        "write",
        {"account_id": 1, "ok": False, "message": "评论接口拒绝", "dyn_id": "123456789012345678"},
    )
    issue_seq = box.bus.last_seq
    box.bus.publish("probe", {"account_id": 1, "state": "unknown", "dyn_id": "other"})
    box.bus.publish("write", {"account_id": 2, "ok": False, "message": "Other account"})
    for _ in range(200):
        box.bus.publish("llm", {"ok": True, "title": "模型完成"})
    rows = client.get("/api/history?issues_only=true&limit=100").json()["items"]
    assert len(rows) == 2
    assert rows[-1]["seq"] == issue_seq and rows[-1]["payload"]["message"] == "评论接口拒绝"
    assert (
        len(
            client.get(f"/api/history?issues_only=true&before_seq={rows[0]['seq']}").json()["items"]
        )
        == 1
    )
