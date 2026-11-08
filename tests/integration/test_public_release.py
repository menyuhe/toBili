from types import SimpleNamespace

import httpx
import pytest

from tobili.api.app import create_app
from tobili.api.routes.public_catalog import PUBLIC_URL, import_page, read_directory


def card(dyn="101", **extra):
    return {
        "dynamic_id": dyn,
        "sender_uid": "111",
        "title": "公开奖品线索",
        "lottery_type": "转发抽奖",
        "lottery_time": 1893456000,
        "draw_status": "active",
        **extra,
    }


def test_directory_read_has_no_credentials_and_preserves_manual_edits(repos, clock):
    box = SimpleNamespace(repos=repos, db=repos.db, clock=clock)
    requests = []

    def serve(request):
        requests.append(request)
        assert str(request.url).startswith(PUBLIC_URL)
        assert "cookie" not in request.headers and "authorization" not in request.headers
        after = request.url.params["after"]
        return httpx.Response(200, json={"items": [card()] if not after else [], "next": "101"})

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        result = read_directory(box, client)
        assert result == {"read": 1, "added": 1, "skipped": 0, "more": False}
        repos.db.connect().execute(
            "UPDATE campaign SET reward='人工奖品',reward_locked=1 WHERE dyn_id='101'"
        )
        repos.db.connect().commit()
        assert read_directory(box, client, restart=True)["added"] == 0
    row = dict(repos.db.connect().execute("SELECT * FROM campaign WHERE dyn_id='101'").fetchone())
    assert row["reward"] == "人工奖品" and row["confidence"] == 0
    assert row["deadline_at"].startswith("2030-01-01")
    assert not repos.db.connect().execute("SELECT * FROM pool_item").fetchall()
    assert not repos.db.connect().execute("SELECT * FROM enrollment").fetchall()
    assert len(requests) == 4


def test_directory_skips_expired_blocked_and_does_not_import_private_fields(repos, clock):
    box = SimpleNamespace(repos=repos, db=repos.db, clock=clock)
    with repos.db.write() as con:
        con.execute("INSERT INTO up_block(uid,active) VALUES('222',1)")
    result = import_page(
        box,
        {
            "items": [
                card("1", lottery_time=1),
                card("2", sender_uid="222"),
                card("3", cookie="fake", confidence=1, reward="耳机"),
            ],
            "next": "3",
        },
        "",
    )
    assert result["added"] == 1
    row = dict(repos.db.connect().execute("SELECT * FROM campaign").fetchone())
    assert row["dyn_id"] == "3" and row["confidence"] == 0 and row["reward"] == "耳机"
    with pytest.raises(ValueError):
        import_page(box, {"items": [card("4")], "next": "3"}, "3")
    assert repos.settings.get(0, "public_catalog.cursor", "") == "3"


def test_public_assembly_has_no_private_routes(tmp_path):
    from importlib.util import find_spec

    if find_spec("tobili.private_pack"):
        pytest.skip("Public-edition boundary is checked in the ordinary build")
    app = create_app(home=str(tmp_path))
    paths = set(app.openapi()["paths"])
    assert "/api/public-catalog/read" in paths
    assert "/api/standby" in paths and "/api/account-browser" in paths
    assert not any(
        p.startswith(
            (
                "/api/exchange",
                "/api/workgroup",
                "/api/article",
                "/api/digest",
                "/api/public-publisher",
            )
        )
        for p in paths
    )
