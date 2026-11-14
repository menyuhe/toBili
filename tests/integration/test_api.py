"""接口层冒烟。只验证装配正确、未登录时不放行，不触碰网络。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tobili.api.app import create_app
from tobili.ports.models import AccountProfile


@pytest.fixture
def client(tmp_path):  # type: ignore[no-untyped-def]
    app = create_app(home=str(tmp_path / "home"))
    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}


def test_overview_before_login(client: TestClient) -> None:
    body = client.get("/api/overview").json()
    assert body["signed_in"] is False
    assert body["account"] is None
    assert body["campaigns"] == {"total": 0, "open": 0}


def test_feeds_require_login(client: TestClient) -> None:
    assert client.post("/api/feeds", json={"kind": "space", "target": "1"}).status_code == 401
    assert client.post("/api/feeds/scan").status_code == 401


def test_feed_list_is_empty_without_account(client: TestClient) -> None:
    assert client.get("/api/feeds").json() == {"items": []}


def test_sign_out_is_idempotent(client: TestClient) -> None:
    # 没人登录时退出也不该报错。`current` 是"退完之后还剩哪个号"，多账号下才非空。
    body = client.delete("/api/auth/session").json()
    assert body["state"] == "signed_out"
    assert body["current"] is None
    assert client.delete("/api/auth/session").status_code == 200


def test_openapi_has_no_legacy_paths(client: TestClient) -> None:
    """接口路径是本项目的原创面，不该出现旧式命名。"""
    paths = client.get("/openapi.json").json()["paths"]
    forbidden = ("lottery", "participation", "repost", "maintenance")
    for path in paths:
        assert not any(word in path for word in forbidden), path


def test_tidy_requires_login(client: TestClient) -> None:
    """清理是破坏性入口，未登录必须一个都不放行。"""
    assert client.post("/api/tidy/plans").status_code == 401
    assert client.get("/api/tidy/plans/1").status_code == 401
    assert client.post("/api/tidy/plans/1/confirm").status_code == 401
    assert client.post("/api/tidy/plans/1/run", json={"apply": True}).status_code == 401
    assert client.post("/api/tidy/items/1/undo").status_code == 401
    assert client.get("/api/tidy/gate").status_code == 401
    assert client.post("/api/tidy/pause", json={"reason": "x"}).status_code == 401
    assert client.post("/api/tidy/resume").status_code == 401
    assert client.post("/api/tidy/ack").status_code == 401


def test_tidy_select_requires_login(client: TestClient) -> None:
    assert client.get("/api/tidy/plans").status_code == 401
    assert client.post("/api/tidy/plans/1/items/select", json={"item_ids": []}).status_code == 401


def test_events_stream_is_registered(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """SSE 只验证协议头与首帧。真跑时长任务在别处测，这里不睡十秒。"""
    from tobili.api.routes import events

    monkeypatch.setattr(events, "HEARTBEAT_SECONDS", 0.05)
    with client.stream("GET", "/api/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert next(response.iter_lines()).startswith(": connected")


def test_ui_is_served_from_bundle(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """有构建产物时同源托管，刷新前端路由不该 404。"""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>toBili</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    app = create_app(home=str(tmp_path / "home"), bundle=dist)
    with TestClient(app) as client:
        assert client.get("/").text == "<html>toBili</html>"
        assert client.get("/tidy").text == "<html>toBili</html>"  # 前端路由回退
        assert client.get("/assets/app.js").text == "console.log(1)"
        assert client.get("/api/health").json() == {"status": "ok"}  # 兜底不能吃掉 API


def test_index_html_is_never_cached(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """index.html 必须每次回源。

    内嵌窗口是 WebView2：响应没有缓存头时，它按 Last-Modified 猜一个新鲜期，
    于是换了程序、窗口里仍是上一版的页面（asset 文件名都指向旧包）。现场症状是
    浏览器 Ctrl+F5 能看到新版本，桌面窗口怎么重开都停在旧版本号。
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>toBili</html>", encoding="utf-8")

    app = create_app(home=str(tmp_path / "home"), bundle=dist)
    with TestClient(app) as client:
        for path in ("/", "/tidy", "/version.json"):
            headers = client.get(path).headers
            assert "no-store" in headers.get("cache-control", ""), path


def test_api_works_without_bundle(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """没有构建产物时 API 照常起 —— 否则只跑后端的人第一步就卡住。"""
    app = create_app(home=str(tmp_path / "home"), bundle=tmp_path / "nope")
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/tidy").status_code == 404


def test_new_endpoints_require_login(client: TestClient) -> None:
    """改判、活动库、白名单全都要登录。改判尤其不能匿名可达。"""
    assert client.get("/api/campaigns").status_code == 401
    assert client.get("/api/campaigns/1").status_code == 401
    assert client.get("/api/tidy/keep").status_code == 401
    assert client.post("/api/tidy/keep", json={"kind": "up", "value": "2002"}).status_code == 401
    assert client.delete("/api/tidy/keep/up/2002").status_code == 401
    assert client.put("/api/results/1", json={"outcome": "won", "note": "x"}).status_code == 401


def test_override_rejects_unknown_outcome(client: TestClient) -> None:
    """pending 不是结论，pending 表示还没判。接口在 schema 层就挡住。"""
    response = client.put("/api/results/1", json={"outcome": "pending"})
    assert response.status_code == 422


def test_history_reads_from_persisted_events(client: TestClient) -> None:
    """历史落库之后，界面刷新还能看到刚才发生了什么。

    事件总线以前只留在内存 deque 里，刷新即空 —— 这是"任务历史"没有持久化的
    全部症状。
    """
    box = client.app.state.container  # type: ignore[attr-defined]
    box.bus.publish("write", {"action": "drop_share", "dyn_id": "d1", "ok": True})
    box.bus.publish("probe", {"state": "absent"})  # 默认集合不含 probe

    body = client.get("/api/history").json()
    assert body["items"], "刚发的事件应当能读回来"
    assert body["items"][0]["topic"] == "write"
    assert body["items"][0]["payload"]["dyn_id"] == "d1"
    assert all(item["topic"] != "probe" for item in body["items"])


def test_history_can_ask_for_noisy_topics(client: TestClient) -> None:
    box = client.app.state.container  # type: ignore[attr-defined]
    box.bus.publish("probe", {"state": "absent"})
    body = client.get("/api/history", params={"topics": "probe"}).json()
    assert body["items"][0]["topic"] == "probe"


def test_event_seq_survives_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """重启后序号接着往下发。

    如果从 1 重来，浏览器带着旧 Last-Event-ID 重连会把新事件全部丢掉，界面就
    一直停在重启前的样子。
    """
    home = str(tmp_path / "home")
    app = create_app(home=home)
    with TestClient(app) as client:
        client.app.state.container.bus.publish("write", {"n": 1})  # type: ignore[attr-defined]
        first = client.app.state.container.bus.last_seq  # type: ignore[attr-defined]

    app2 = create_app(home=home)
    with TestClient(app2) as client2:
        box = client2.app.state.container  # type: ignore[attr-defined]
        assert box.bus.last_seq == first
        event = box.bus.publish("write", {"n": 2})
        assert event.seq == first + 1


def test_settings_roundtrip(client: TestClient) -> None:
    """设置能存能读。密钥只回"设了没"，永远不回显。"""
    body = client.put(
        "/api/settings/text", json={"mode": "llm", "custom": "来看{up}", "fallback": "备用"}
    ).json()
    assert body["mode"] == "llm"
    assert body["custom"] == "来看{up}"

    body = client.put(
        "/api/settings/llm",
        json={"base_url": "https://api.example.com", "model": "m1", "api_key": "sk-very-secret"},
    ).json()
    assert body["llm"]["key_set"] is True
    assert "sk-very-secret" not in str(body)
    assert client.get("/api/settings").json()["llm"]["model"] == "m1"


def test_llm_test_without_key_is_refused(client: TestClient) -> None:
    """配置不全时不该发请求出去，直接告诉人还缺什么。"""
    response = client.post("/api/settings/llm/test")
    assert response.status_code == 409


def test_text_preview_requires_login(client: TestClient) -> None:
    assert client.post("/api/settings/text/preview", json={}).status_code == 401


def test_bad_mode_is_rejected(client: TestClient) -> None:
    assert client.put("/api/settings/text", json={"mode": "乱填"}).status_code == 422


def test_tidy_gate_with_lost_credential_is_401(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """账号记录还在、凭据文件丢了（清缓存/换机/过期），闸门接口要给 401。

    这时 `_account()` 是能过的，炸的是 inspect() 里取 cookie 那一步；
    漏了这个 try 就会冒成 500，页面上只显示"服务器错误"，人看不出要重新登录。
    """
    app = create_app(home=str(tmp_path / "home"))
    with TestClient(app) as client:
        container = app.state.container
        container.repos.accounts.upsert(AccountProfile(uid="12345678", nickname="测试"), "ref")
        response = client.get("/api/tidy/gate")
        assert response.status_code == 401
        assert "登录" in response.json()["detail"]


def test_unknown_api_path_is_404_json(client: TestClient) -> None:
    """未知接口必须回 404，不能回一页 HTML。

    兜底路由把 `/api/accounts` 吃成 200 index.html 那次，看起来像"路由配错了"，
    实际是"这个进程太旧、没有这个接口" —— 一个错的诊断会让人往错的方向查很久。
    """
    response = client.get("/api/typo")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_api_prefix_alone_is_not_served_as_html(client: TestClient) -> None:
    assert client.get("/api").status_code == 404


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "ui" / "dist" / "index.html").is_file(),
    reason="没有前端构建产物，托管根本没挂上",
)
def test_deep_link_still_serves_the_app(client: TestClient) -> None:
    # 修 404 的时候别把前端路由一起修坏了：刷新 /tidy 仍然要拿到 index.html。
    response = client.get("/tidy")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# ---- 只接受本机访问 ----
def test_non_loopback_host_is_refused(client: TestClient) -> None:
    """DNS 重绑定：攻击者的域名解析到 127.0.0.1，浏览器就以为同源了。"""
    response = client.get("/api/health", headers={"host": "evil.example.com"})
    assert response.status_code == 403


def test_foreign_origin_is_refused(client: TestClient) -> None:
    """别的网页顺手发过来的请求。读接口没有预检挡着，得自己看 Origin。"""
    response = client.get("/api/inbox", headers={"origin": "https://evil.example.com"})
    assert response.status_code == 403


def test_loopback_origin_is_fine(client: TestClient) -> None:
    response = client.get("/api/health", headers={"origin": "http://127.0.0.1:5173"})
    assert response.status_code == 200


def test_ipv6_loopback_host_is_fine(client: TestClient) -> None:
    assert client.get("/api/health", headers={"host": "[::1]:8848"}).status_code == 200


def test_bad_host_blocks_static_too(client: TestClient) -> None:
    # 中间件在最外层：这个进程里没有哪条路径是给外人看的。
    assert client.get("/tidy", headers={"host": "evil.example.com"}).status_code == 403


def test_scheduled_round_lands_in_its_own_category(client: TestClient) -> None:
    """一轮话题更新不该出现在「参与」里。

    以前 schedule.round 整个主题被归到参与，于是"话题更新"和"参与活动"在日志里
    长得一模一样，排查时会直接把人带偏。
    """
    box = client.app.state.container  # type: ignore[attr-defined]
    box.bus.publish(
        "schedule.round",
        {"account_id": None, "task": "topics", "title": "话题更新 · 更新 1 个来源，识别 1 条活动"},
    )
    box.bus.publish(
        "schedule.round",
        {"account_id": None, "task": "participate", "title": "参与活动 · 已完成 2 条"},
    )
    sources = client.get("/api/history", params={"category": "sources"}).json()["items"]
    participation = client.get("/api/history", params={"category": "participation"}).json()["items"]
    assert any("话题更新" in item["payload"].get("title", "") for item in sources)
    assert all("话题更新" not in item["payload"].get("title", "") for item in participation)
    assert any(item["label"] == "话题" for item in sources)


def test_dismissed_issue_disappears_from_history(client: TestClient) -> None:
    """异常可以被「知道了」压掉，但动作本身留痕。"""
    box = client.app.state.container  # type: ignore[attr-defined]
    box.bus.publish("settle", {"ok": False, "problems": ["读不到官方开奖结果"]})
    items = client.get("/api/history", params={"issues_only": True}).json()["items"]
    assert items, "刚发的失败事件应当在异常里"
    seq = items[0]["seq"]
    assert client.post("/api/history/dismiss", json={"seq": seq}).status_code == 200
    after = client.get("/api/history", params={"issues_only": True}).json()["items"]
    assert all(item["seq"] != seq for item in after)
    kept = (
        box.db.connect().execute("SELECT note FROM event_dismissal WHERE seq=?", (seq,)).fetchone()
    )
    assert kept is not None
