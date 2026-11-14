from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tobili.api.app import create_app
from tobili.domain.inbox import Message
from tobili.ports.models import AccountProfile, MessageStat
from tobili.runtime.taskbook import TaskBook
from tobili.usecases.inbox import Inbox


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(TaskBook, "start", lambda _: None)
    app = create_app(home=str(tmp_path))
    with TestClient(app) as client:
        box = app.state.container
        a = box.repos.accounts.upsert(AccountProfile(uid="11", nickname="甲"), "a")
        b = box.repos.accounts.upsert(AccountProfile(uid="22", nickname="乙"), "b")
        box.vault.write("a", "test")
        box.vault.write("b", "test")
        box.repos.accounts.mark_current(a)
        yield client, box, a, b


def test_unrelated_message_needs_no_campaign_is_account_scoped_and_content_scoped(hub):
    client, box, a, b = hub
    message = Message(kind="chat", key="chat:1", who="社区中心", text="恭喜你中奖了，请提供地址")
    reader = SimpleNamespace(inbox=lambda **_: [message], unread=lambda: MessageStat())
    inbox = Inbox(gateway=reader, seen=box.repos.inbox)
    box.inbox = lambda _owner=None: inbox
    row = client.get("/api/inbox").json()["items"][0]
    body = {"account_id": a, "key": row["review_key"], "decision": "irrelevant"}
    assert client.post("/api/inbox/review", json=body).is_success
    result = client.get("/api/inbox").json()
    assert not result["items"][0]["prize"] and result["items"][0]["reviewed"]
    assert result["counts"]["prize"] == 0
    assert inbox.fetch(b).counts["prize"] == 1
    message = replace(message, text="新的活动恭喜你中奖了，请提供地址")
    # 收件箱有 2 分钟缓存，这里要看的是"同一条私信换了内容就不再算已核对"，
    # 所以得强制回源，否则读到的是上一份结果。
    assert client.get("/api/inbox?refresh=1").json()["counts"]["prize"] == 1
    box.repos.accounts.mark_current(b)
    assert client.post("/api/inbox/review", json=body).status_code == 409
    assert (
        client.post("/api/inbox/seen", json={"account_id": a, "keys": ["chat:1"]}).status_code
        == 409
    )
    box.repos.accounts.mark_current(a)
    assert client.post("/api/inbox/review", json={**body, "decision": "auto"}).is_success
    assert not box.repos.inbox.irrelevant(a)
    assert box.db.connect().execute("SELECT count(*) FROM verdict").fetchone()[0] == 0


def test_hub_lists_all_accounts_without_switch_or_remote_reads(hub):
    client, box, a, b = hub
    config = box.tasks.config(a)
    config["running"] = True
    for task in config["tasks"]:
        task["enabled"] = task["key"] == "messages"
    box.tasks.save(a, config)
    box.tasks.active[a] = {
        "key": "messages",
        "since": "2026-11-08T00:00:00+00:00",
        "note": "读消息",
    }
    box.source_progress[b] = {"key": "source", "label": "订阅更新", "note": "乙的订阅"}

    def forbidden(*args):
        raise AssertionError("Hub must not contact remote services")

    box.gateway_for = forbidden
    rows = client.get("/api/standby").json()["items"]
    assert len(rows) == 2 and box.account()[0] == a
    assert rows[0]["active"]["label"] == "中奖消息"
    assert rows[1]["active"]["note"] == "乙的订阅"
    assert rows[0]["running"] and not rows[1]["running"]
    assert "cookie" not in str(rows).lower()


def test_browser_endpoint_captures_owner_and_never_exposes_credentials(hub, monkeypatch):
    client, box, a, b = hub
    calls = []
    from tobili.desktop import account_browser as browser

    monkeypatch.setattr(
        browser, "launch", lambda home, owner, url: calls.append((owner, url)) or "a" * 32
    )
    url = "https://message.bilibili.com/"
    result = client.post("/api/account-browser", json={"account_id": a, "url": url})
    assert result.json() == {"ticket": "a" * 32} and calls == [(a, url)]
    box.repos.accounts.mark_current(b)
    assert (
        client.post("/api/account-browser", json={"account_id": a, "url": url}).status_code == 409
    )
    assert len(calls) == 1
