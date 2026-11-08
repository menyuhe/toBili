"""多账号。

这一组的重点不是"能切换"，而是**切换之后用的真的是另一个号**。
串号是这类工具最贵的错误：界面显示成功、配额也扣了，结果全算在别人头上，
事后只能靠翻 B 站自己的记录才发现。

所以每条测试都盯着"这个账号用的是不是它自己的凭据"，而不是"接口返回 200"。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tobili.api.app import create_app
from tobili.domain.errors import MissingCredential
from tobili.domain.session import LEGACY_SESSION, session_key
from tobili.ports.models import AccountProfile


def _sign_in(box, uid: str, nickname: str, cookie: str) -> int:  # type: ignore[no-untyped-def]
    """模拟"扫码登录了一个号"：写凭据文件 + 落账号行。"""
    key = session_key(uid)
    box.vault.write(key, cookie)
    return box.repos.accounts.upsert(AccountProfile(uid=uid, nickname=nickname, level=6), key)


@pytest.fixture
def box(tmp_path):  # type: ignore[no-untyped-def]
    app = create_app(home=str(tmp_path / "home"))
    with TestClient(app) as client:
        yield client, app.state.container


# ---- 凭据隔离 ----


def test_each_account_has_its_own_cookie(box) -> None:  # type: ignore[no-untyped-def]
    _, container = box
    a = _sign_in(container, "1001", "甲", "cookie-A")
    b = _sign_in(container, "1002", "乙", "cookie-B")

    assert container.cookie(a) == "cookie-A"
    assert container.cookie(b) == "cookie-B"


def test_switching_changes_which_cookie_is_used(box) -> None:  # type: ignore[no-untyped-def]
    _, container = box
    a = _sign_in(container, "1001", "甲", "cookie-A")
    b = _sign_in(container, "1002", "乙", "cookie-B")

    container.switch_account(a)
    assert container.cookie() == "cookie-A"
    container.switch_account(b)
    assert container.cookie() == "cookie-B"


def test_gateway_is_cached_per_account(box) -> None:  # type: ignore[no-untyped-def]
    """两个账号不能共用一个网关实例 —— 共用就是拿 A 的 cookie 写 B。"""
    _, container = box
    a = _sign_in(container, "1001", "甲", "cookie-A")
    b = _sign_in(container, "1002", "乙", "cookie-B")

    first = container.gateway_for(a)
    second = container.gateway_for(b)
    assert first is not second
    # 同一个账号再取一次要复用，别每次请求都新建连接池。
    assert container.gateway_for(a) is first


def test_cookie_change_rebuilds_gateway(box) -> None:  # type: ignore[no-untyped-def]
    """同账号重扫二维码后 cookie 变了，旧网关必须丢掉。"""
    _, container = box
    a = _sign_in(container, "1001", "甲", "cookie-old")
    old = container.gateway_for(a)

    container.vault.write(session_key("1001"), "cookie-new")
    fresh = container.gateway_for(a)
    assert fresh is not old


def test_account_without_credential_is_not_ready(box) -> None:  # type: ignore[no-untyped-def]
    """有账号行但凭据文件没了（清过缓存/换过机器），要如实标成不可用。"""
    _, container = box
    a = _sign_in(container, "1001", "甲", "cookie-A")
    container.vault.erase(session_key("1001"))

    items = {item["id"]: item for item in container.accounts()}
    assert items[a]["ready"] is False

    with pytest.raises(MissingCredential):
        container.cookie(a)


# ---- 切换 ----


def test_switch_is_remembered(box) -> None:  # type: ignore[no-untyped-def]
    client, container = box
    a = _sign_in(container, "1001", "甲", "cookie-A")
    b = _sign_in(container, "1002", "乙", "cookie-B")

    client.post("/api/accounts/switch", json={"account_id": b})
    body = client.get("/api/accounts").json()
    assert body["current"] == b
    assert {item["id"] for item in body["items"] if item["current"]} == {b}

    client.post("/api/accounts/switch", json={"account_id": a})
    assert client.get("/api/accounts").json()["current"] == a


def test_switch_to_unknown_account_is_refused(box) -> None:  # type: ignore[no-untyped-def]
    client, _ = box
    assert client.post("/api/accounts/switch", json={"account_id": 999}).status_code == 409


def test_switch_to_account_without_credential_is_refused(box) -> None:  # type: ignore[no-untyped-def]
    client, container = box
    _sign_in(container, "1001", "甲", "cookie-A")
    b = _sign_in(container, "1002", "乙", "cookie-B")
    container.vault.erase(session_key("1002"))

    response = client.post("/api/accounts/switch", json={"account_id": b})
    assert response.status_code == 409
    assert "扫码" in response.json()["detail"]


def test_overview_follows_the_current_account(box) -> None:  # type: ignore[no-untyped-def]
    """参与数是按账号的：切到空账号，概览不能还写着别人的三十条。"""
    client, container = box
    a = _sign_in(container, "1001", "甲", "cookie-A")
    b = _sign_in(container, "1002", "乙", "cookie-B")

    now = datetime.now(UTC)
    with container.db.write() as conn:
        conn.execute(
            "INSERT INTO campaign (dyn_id, up_uid, up_name, title, reward, kind,"
            " deadline_at, confidence, source_ref, first_seen_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "8001",
                "up",
                "UP",
                "活动",
                "奖品",
                "share",
                (now + timedelta(days=3)).isoformat(timespec="seconds"),
                0.9,
                "test",
                now.isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
            ),
        )
        campaign_id = conn.execute("SELECT id FROM campaign WHERE dyn_id = '8001'").fetchone()["id"]
        conn.execute(
            "INSERT INTO enrollment (account_id, campaign_id, stage, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            (a, campaign_id, "settled", now.isoformat(), now.isoformat()),
        )

    container.switch_account(a)
    assert client.get("/api/overview").json()["enrollments"]["total"] == 1
    container.switch_account(b)
    assert client.get("/api/overview").json()["enrollments"]["total"] == 0


# ---- 摘号 ----


def test_remove_account_keeps_its_data(box) -> None:  # type: ignore[no-untyped-def]
    """摘号只删凭据。参与记录是开奖核验的依据，删了就真没了。"""
    client, container = box
    a = _sign_in(container, "1001", "甲", "cookie-A")
    b = _sign_in(container, "1002", "乙", "cookie-B")
    container.switch_account(a)

    now = datetime.now(UTC)
    with container.db.write() as conn:
        conn.execute(
            "INSERT INTO campaign (dyn_id, up_uid, up_name, title, reward, kind,"
            " confidence, source_ref, first_seen_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "8002",
                "up",
                "UP",
                "活动",
                "奖品",
                "share",
                0.9,
                "test",
                now.isoformat(),
                now.isoformat(),
            ),
        )
        campaign_id = conn.execute("SELECT id FROM campaign WHERE dyn_id = '8002'").fetchone()["id"]
        conn.execute(
            "INSERT INTO enrollment (account_id, campaign_id, stage, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            (a, campaign_id, "settled", now.isoformat(), now.isoformat()),
        )

    body = client.delete(f"/api/accounts/{a}").json()
    # 还有能用的号，就顺手切过去，不把人踹回扫码页。
    assert body["current"] == b
    assert client.get("/api/accounts").json()["current"] == b
    assert not container.vault.exists(session_key("1001"))
    # 记录还在。
    assert (
        container.db.connect()
        .execute("SELECT COUNT(*) AS n FROM enrollment WHERE account_id = ?", (a,))
        .fetchone()["n"]
        == 1
    )


def test_remove_last_account_lands_on_login_screen(box) -> None:  # type: ignore[no-untyped-def]
    client, container = box
    a = _sign_in(container, "1001", "甲", "cookie-A")
    container.switch_account(a)

    assert client.delete(f"/api/accounts/{a}").json()["current"] is None
    assert client.get("/api/overview").json()["signed_in"] is False


def test_remove_unknown_account_is_404(box) -> None:  # type: ignore[no-untyped-def]
    client, _ = box
    assert client.delete("/api/accounts/999").status_code == 404


# ---- 老库迁移 ----


def test_legacy_session_is_migrated(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """单账号时代的 `bilibili-session` 要改名到带 uid 的键下。

    不迁的话，升级之后所有人都看到扫码页 —— 凭据明明还在硬盘上。
    """
    home = str(tmp_path / "home")
    app = create_app(home=home)
    with TestClient(app):
        container = app.state.container
        # 造出"老库"的样子：账号行的 credential_ref 还是旧键名。
        container.vault.write(LEGACY_SESSION, "cookie-legacy")
        container.repos.accounts.upsert(
            AccountProfile(uid="1001", nickname="甲", level=6), LEGACY_SESSION
        )

    # 换一个容器重开，模拟升级后第一次启动。
    app2 = create_app(home=home)
    with TestClient(app2) as client2:
        container2 = app2.state.container
        assert not container2.vault.exists(LEGACY_SESSION)
        assert container2.vault.exists(session_key("1001"))
        assert container2.cookie() == "cookie-legacy"
        assert client2.get("/api/overview").json()["signed_in"] is True
