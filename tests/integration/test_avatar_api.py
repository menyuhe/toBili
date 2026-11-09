import httpx
from fastapi.testclient import TestClient

from tobili.adapters.avatar import AvatarCache
from tobili.api.app import create_app
from tobili.ports.models import AccountProfile


def test_account_avatar_route_is_local_cache_without_session(tmp_path):
    app = create_app(home=str(tmp_path / "home"))
    with TestClient(app) as client:
        box = app.state.container
        box.avatars = AvatarCache(
            tmp_path / "avatars",
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=b"\x89PNG\r\n\x1a\nimage")
            ),
        )
        aid = box.repos.accounts.upsert(
            AccountProfile(
                uid="synthetic", nickname="测试", avatar="https://i0.hdslb.com/bfs/face/test.jpg"
            ),
            "",
        )
        row = client.get("/api/accounts").json()["items"][0]
        assert row["avatar"].startswith(f"/api/accounts/{aid}/avatar?v=")
        response = client.get(row["avatar"])
        assert response.status_code == 200 and response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"].startswith("private")
        assert client.get("/api/accounts/99999/avatar").status_code == 404
        box.repos.accounts.disable(aid)
        assert client.get(row["avatar"]).status_code == 404
