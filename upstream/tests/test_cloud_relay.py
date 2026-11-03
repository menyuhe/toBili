"""Isolated relay tests; no platform credentials or network requests."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import select

from cloud.app import create_app
from cloud.models import Account, Device, Record, Task

ORIGIN = "https://relay.test"
UID = "123456"
DID = "1234567890123456789"
PASSWORD = "desktop-test-password-123"


@pytest.fixture
def relay(tmp_path):
    app = create_app("sqlite:///" + (tmp_path / "relay.db").as_posix(), ORIGIN, "test-invite")
    yield app
    app.state.store.engine.dispose()


def run(coro):
    return asyncio.run(coro)


async def identity(client, name="tester", kind="windows", register=False):
    response = await client.post("/api/v1/auth/register" if register else "/api/v1/auth/login", json={
        "username": name, "password": PASSWORD, "device_name": kind, "kind": kind, "invite": "test-invite",
    }, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return response.json()


def headers(identity):
    return {"Authorization": "Bearer " + identity["device_token"], "Origin": ORIGIN}


async def setup(client):
    win = await identity(client, register=True)
    mac = await identity(client, kind="mac")
    return win, mac


async def push(client, device, kind="activity", key=DID, value=None, base=0, change_id="change_0001", deleted=False):
    value = value if value is not None else {"dynamic_id": DID, "title": "测试活动", "lottery_type": "互动抽奖"}
    return await client.post("/api/v1/sync/push", json={"bili_uid": UID, "changes": [{
        "change_id": change_id, "kind": kind, "key": key, "base_version": base, "deleted": deleted, "value": value,
    }]}, headers=headers(device))


async def submit(client, device, key="task_test_01", action="refresh_status", params=None):
    return await client.post("/api/v1/tasks", json={"bili_uid": UID, "request_key": key,
                                                 "action": action, "params": params or {}}, headers=headers(device))




def test_accounts_devices_credentials_and_isolation(relay):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=relay), base_url=ORIGIN) as client:
            win, mac = await setup(client)
            outsider = await identity(client, name="outsider", register=True)
            assert (await client.get("/api/v1/accounts", headers=headers(outsider))).json()["items"] == []
            assert (await client.get(f"/api/v1/records?bili_uid={UID}", headers=headers(outsider))).status_code == 410
            assert (await client.post('/api/v1/catalog', json={'items':[{'dynamic_id':DID,'title':'shared activity'}]}, headers=headers(win))).status_code == 200
            assert len((await client.get('/api/v1/catalog',headers=headers(mac))).json()['items']) == 1
            assert (await client.get('/api/v1/catalog',headers=headers(outsider))).json()['items'] == []
            assert (await client.post(f"/api/v1/devices/{win['device_id']}/revoke", headers=headers(outsider))).status_code == 404
            devices = (await client.get("/api/v1/devices", headers=headers(win))).json()
            assert len(devices["items"]) == 2
            assert "token" not in json.dumps(devices)
            assert (await client.post(f"/api/v1/devices/{mac['device_id']}/revoke", headers=headers(win))).status_code == 200
            assert (await client.get("/api/v1/me", headers=headers(mac))).status_code == 401
            mobile = await identity(client, kind="pwa")
            assert "device_token" not in mobile
            assert "HttpOnly" in str(client.cookies) or "tobili_session" in client.cookies
            assert (await client.post("/api/v1/accounts/bind", json={"bili_uid": UID}, headers={"Origin": ORIGIN})).status_code == 410
    run(scenario())








def test_csrf_pwa_allowlist_and_auth_rate_limit(relay):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=relay), base_url=ORIGIN) as client:
            win, _ = await setup(client)
            await identity(client, kind="pwa")
            invalid = {"bili_uid": UID, "request_key": "mobile_bad_task", "action": "login", "params": {}}
            assert (await client.post("/api/v1/tasks", json=invalid, headers={"Origin": ORIGIN})).status_code == 410
            assert (await client.post("/api/v1/auth/logout", json={})).status_code == 403
            assert (await client.post("/api/v1/auth/logout", json={}, headers={"Origin": "https://evil.test"})).status_code == 403
            assert (await client.get("/api/v1/me", headers=headers(win))).headers["cache-control"] == "no-store"
            assert (await client.get("/sw.js")).status_code == 200
            assert (await client.get("/../config/cookies.txt")).status_code == 404
            for _ in range(16):
                response = await client.post("/api/v1/auth/login", json={"username": "nobody", "password": PASSWORD, "device_name": "test", "kind": "pwa"}, headers={"Origin": ORIGIN})
            assert response.status_code == 429
    run(scenario())




