import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from web.api_errors import register_exception_handlers
from web.maintenance_routes import router


@pytest.fixture
def client(isolated_home, monkeypatch):
    from src import notifications
    monkeypatch.setattr(notifications,"settings_path",lambda:isolated_home/"config"/"notifications.json")
    monkeypatch.setattr("web.maintenance_routes.get_login_uid",lambda:123)
    app=FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    return TestClient(app)


def test_settings_api_masks_credentials_and_validates_without_echo(client):
    settings={"audio":False,"dingtalk":True,"webhook":"https://oapi.dingtalk.com/robot/send?access_token=private-value","sign_secret":"SECtest","prefix":"Binggo","events":["won"]}
    r=client.put("/api/notifications/settings",json=settings)
    assert r.status_code==200 and "private-value" not in r.text and "SECtest" not in r.text
    r=client.get("/api/notifications/settings")
    assert "webhook" not in r.json() and r.json()["webhook_hint"]
    r=client.put("/api/notifications/settings",json={**settings,"webhook":"secret"*500})
    assert r.status_code==400 and "secretsecret" not in r.text


def test_cleanup_defaults_off_and_requires_login(client, monkeypatch):
    r=client.get("/api/maintenance/settings")
    assert r.status_code==200 and not any(r.json()[key] for key in ("enabled","delete_reposts","unfollow"))
    assert client.post("/api/maintenance/clean").status_code==400
    assert client.get("/api/maintenance/activities").json()["items"]==[]
    monkeypatch.setattr("web.maintenance_routes.get_login_uid",lambda:None)
    assert client.get("/api/maintenance/settings").status_code==401


def test_cleanup_history_filters_accounts_actions_and_paginates(client):
    from src.db.models import MaintenanceAuditRow, CleanupStepRow
    from src.db.session import session_scope
    with session_scope() as session:
        for i in range(52):
            session.add(MaintenanceAuditRow(uid="123", action="delete", state="done", target_id=str(i), recorded_at=i))
        session.add(MaintenanceAuditRow(uid="456", action="unfollow", state="done", recorded_at=100))
        session.add(MaintenanceAuditRow(uid="123", action="draw_check", state="lost", recorded_at=101))
        session.add(CleanupStepRow(uid="123", action="delete", target_id="1", state="done"))
        session.add(CleanupStepRow(uid="456", action="unfollow", target_id="2", state="done"))
    first = client.get('/api/maintenance/cleanup-history').json()
    assert len(first['items']) == 50 and first['deleted'] == 1 and first['unfollowed'] == 0
    assert all(r['uid'] == '123' and r['action'] == 'delete' for r in first['items'])
    second = client.get(f"/api/maintenance/cleanup-history?before={first['next_before']}").json()
    assert len(second['items']) == 2 and second['next_before'] is None
