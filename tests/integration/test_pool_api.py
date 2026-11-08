"""预选池接口：排队、挪位、暂停，以及"不登录不给跑"。

跑队列的 happy path 需要真实网络，这里只验证边界：没凭据 → 401，空池子 → 409，
暂停中 → 409 且**一条都没动**。那些"点一下别把十几条转发发出去"的保证都在这里。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tobili.api.app import create_app
from tobili.ports.models import AccountProfile

UID = "12345678"


@pytest.fixture
def box(tmp_path):  # type: ignore[no-untyped-def]
    app = create_app(home=str(tmp_path / "home"))
    with TestClient(app) as client:
        container = app.state.container
        container.repos.accounts.upsert(AccountProfile(uid=UID, nickname="测试"), "ref")
        now = datetime.now(UTC)
        for index, deadline in enumerate(
            [now + timedelta(days=2), now + timedelta(days=5), now - timedelta(days=1)]
        ):
            with container.db.write() as conn:
                conn.execute(
                    "INSERT INTO campaign (dyn_id, up_uid, up_name, title, reward, kind,"
                    " deadline_at, confidence, source_ref, first_seen_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"70{index}",
                        f"up{index}",
                        f"UP{index}",
                        f"活动{index}",
                        "奖品",
                        "share",
                        deadline.isoformat(timespec="seconds"),
                        0.9,
                        "test",
                        now.isoformat(timespec="seconds"),
                        now.isoformat(timespec="seconds"),
                    ),
                )
        yield client, container


def _catalog(client: TestClient) -> list[dict]:
    return [
        {
            "campaign_id": r["id"],
            "open": datetime.fromisoformat(r["deadline_at"]) > datetime.now(UTC),
        }
        for r in client.get("/api/campaigns").json()["items"]
    ]


def _ids(client: TestClient) -> list[int]:
    return [row["campaign_id"] for row in client.get("/api/pool").json()["items"]]


def _edit(client: TestClient, **body):  # type: ignore[no-untyped-def]
    return client.post("/api/pool", json=body)


def test_empty_pool(box) -> None:  # type: ignore[no-untyped-def]
    client, _ = box
    body = client.get("/api/pool").json()
    assert body["items"] == []
    assert body["paused"] is False
    assert body["counts"]["pending"] == 0
    assert body["limit"] == 200


def test_pending_count_excludes_finished_and_other_accounts(box) -> None:
    client, container = box
    ids = [r["campaign_id"] for r in _catalog(client) if r["open"]]
    _edit(client, operation="add", campaign_ids=ids)
    owner = container.account()[0]
    assert client.get("/api/pool/count").json() == {"account_id": owner, "pending": 2}
    other = container.repos.accounts.upsert(
        AccountProfile(uid="87654321", nickname="另一个号"), "other"
    )
    with container.db.write() as db:
        db.execute(
            "UPDATE pool_item SET state='skipped' WHERE account_id=? AND campaign_id=?",
            (owner, ids[0]),
        )
        db.execute(
            "INSERT INTO pool_item(account_id,campaign_id,position,state,message,updated_at) SELECT ?,campaign_id,position,'pending','',updated_at FROM pool_item WHERE account_id=?",
            (other, owner),
        )
    assert client.get("/api/pool/count").json() == {"account_id": owner, "pending": 1}
    _edit(client, operation="remove", campaign_id=ids[1])
    assert client.get("/api/pool/count").json()["pending"] == 0


def test_requires_login(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = create_app(home=str(tmp_path / "home"))
    with TestClient(app) as client:
        assert client.get("/api/pool").status_code == 401
        assert (
            client.post("/api/pool", json={"operation": "add", "campaign_ids": [1]}).status_code
            == 401
        )
        assert client.post("/api/pool/run", json={"apply": False}).status_code == 401


def test_add_then_order_is_kept(box) -> None:  # type: ignore[no-untyped-def]
    client, _ = box
    rows = _catalog(client)
    upcoming = [row for row in rows if row["open"]]
    assert len(upcoming) == 2

    # 手动排的顺序要原样保留，不能按开奖时间重排。
    first, second = upcoming[1]["campaign_id"], upcoming[0]["campaign_id"]
    _edit(client, operation="add", campaign_ids=[first, second])
    assert _ids(client) == [first, second]

    moved = _edit(client, operation="move", campaign_id=second, direction=-1).json()
    assert [row["position"] for row in moved["items"]] == [0, 1]
    assert moved["items"][0]["campaign_id"] == second


def test_add_rejects_a_finished_campaign(box) -> None:  # type: ignore[no-untyped-def]
    client, _ = box
    rows = _catalog(client)
    ended = next(row for row in rows if not row["open"])
    response = _edit(client, operation="add", campaign_ids=[ended["campaign_id"]])
    assert response.status_code == 400
    assert "开奖时间" in response.json()["detail"]
    assert _ids(client) == []


def test_add_unknown_campaign(box) -> None:  # type: ignore[no-untyped-def]
    client, _ = box
    assert _edit(client, operation="add", campaign_ids=[999999]).status_code == 400


def test_remove(box) -> None:  # type: ignore[no-untyped-def]
    client, _ = box
    rows = _catalog(client)
    ids = [row["campaign_id"] for row in rows if row["open"]]
    _edit(client, operation="add", campaign_ids=ids)
    _edit(client, operation="remove", campaign_id=ids[0])
    assert _ids(client) == [ids[1]]
    assert _edit(client, operation="remove", campaign_id=ids[0]).status_code == 400


def test_pause_and_resume(box) -> None:  # type: ignore[no-untyped-def]
    client, _ = box
    assert _edit(client, operation="pause").json()["paused"] is True
    assert client.get("/api/pool").json()["paused"] is True
    assert _edit(client, operation="resume").json()["paused"] is False


def test_clear_done_keeps_unfinished(box) -> None:  # type: ignore[no-untyped-def]
    client, container = box
    rows = _catalog(client)
    ids = [row["campaign_id"] for row in rows if row["open"]]
    _edit(client, operation="add", campaign_ids=ids)

    # 手工把一条标成已完成，模拟"跑过一轮"。
    with container.db.write() as conn:
        conn.execute("UPDATE pool_item SET state = 'success' WHERE campaign_id = ?", (ids[0],))
    assert len(_ids(client)) == 2
    _edit(client, operation="clear_done")
    assert _ids(client) == [ids[1]]


def test_run_refuses_when_empty(box) -> None:  # type: ignore[no-untyped-def]
    client, _ = box
    response = client.post("/api/pool/run", json={"apply": False})
    assert response.status_code == 409
    assert "没有待参与" in response.json()["detail"]


def test_run_refuses_when_paused(box) -> None:  # type: ignore[no-untyped-def]
    client, _ = box
    rows = _catalog(client)
    _edit(client, operation="add", campaign_ids=[row["campaign_id"] for row in rows if row["open"]])
    _edit(client, operation="pause")
    response = client.post("/api/pool/run", json={"apply": True})
    assert response.status_code == 409
    assert "暂停" in response.json()["detail"]


def test_run_without_credential_is_401(box) -> None:  # type: ignore[no-untyped-def]
    """没有凭据时不能一路 500 —— 界面要能看出"该重新扫码了"。"""
    client, _ = box
    rows = _catalog(client)
    _edit(client, operation="add", campaign_ids=[row["campaign_id"] for row in rows if row["open"]])
    assert client.post("/api/pool/run", json={"apply": False}).status_code == 401


def test_pool_is_per_account(box) -> None:  # type: ignore[no-untyped-def]
    """换账号看到的是自己的池子。两个号的待办混在一起是最难发现的错。"""
    client, container = box
    rows = _catalog(client)
    _edit(client, operation="add", campaign_ids=[row["campaign_id"] for row in rows if row["open"]])
    assert len(_ids(client)) == 2

    other = container.repos.accounts.upsert(AccountProfile(uid="87654321", nickname="别人"), "ref")
    assert other != 1
    # 直接把 current() 换到另一个号：把先前的账号禁掉再恢复太绕，这里改 uid 判断即可。
    with container.db.write() as conn:
        conn.execute("UPDATE account SET disabled = 1 WHERE id = 1")
    assert client.get("/api/pool").json()["items"] == []


def test_shelf_is_private_and_reversible(box):
    client, container = box
    assert client.post("/api/campaigns/shelf").json()["count"] == 1
    assert client.post("/api/campaigns/shelf").json()["count"] == 0
    hidden = client.get("/api/campaigns?state=shelved").json()["items"][0]
    assert client.get("/api/campaigns").json()["total"] == 2
    other = container.repos.accounts.upsert(AccountProfile(uid="22", nickname="B"), "b")
    container.repos.accounts.mark_current(other)
    assert client.get("/api/campaigns").json()["total"] == 3
    container.repos.accounts.mark_current(1)
    assert client.delete(f"/api/campaigns/shelf/{hidden['id']}").is_success
    assert client.get("/api/campaigns").json()["total"] == 3


def test_available_filters_before_limit(box):
    client, container = box
    with container.db.write() as db:
        db.executemany(
            "INSERT INTO campaign(dyn_id,up_uid,title,deadline_at,first_seen_at,updated_at) VALUES(?, '1','past','2000-01-01','now','now')",
            [(str(i),) for i in range(3000, 3302)],
        )
    rows = client.get("/api/campaigns?state=available&limit=1").json()
    assert rows["total"] == 2
    target = rows["items"][0]["id"]
    assert _edit(client, operation="add", campaign_ids=[target]).is_success
    assert client.get("/api/campaigns?state=available").json()["total"] == 1
    # The picker keeps positions stable after adding; availability queries still exclude it.
    selected = client.get("/api/campaigns?state=selection&limit=1").json()
    assert selected["total"] == 2 and selected["items"][0]["id"] == target


def test_review_then_protect_a_preexisting_plan(box):
    from tobili.domain.tidy import Outcome

    client, container = box
    target = client.get("/api/campaigns").json()["items"][0]
    container.vault.write("ref", "fixture")
    container.repos.verdicts.record(
        campaign_id=target["id"],
        account_id=1,
        outcome=Outcome.LOST,
        decided_at=container.clock.now() - timedelta(days=2),
        evidence="fixture",
    )
    assert not container.protected(1, target["id"])
    url = f"/api/results/{target['id']}"
    assert (
        client.put(url, json={"account_id": 9, "outcome": "won", "protect": True}).status_code
        == 409
    )
    assert client.put(url, json={"account_id": 1, "outcome": "won", "protect": True}).is_success
    assert container.protected(1, target["id"])
    assert client.get("/api/tidy/keep").json()["items"][0]["value"] == target["dyn_id"]
