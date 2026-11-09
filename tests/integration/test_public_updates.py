from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from services.public_catalog.server import create_app

from tobili.usecases.public_updates import apply_page


def test_retracted_prize_stays_unknown_and_manual_edits_survive(repos, clock):
    from tobili.publicwire import UNVERIFIED_REWARD
    box = SimpleNamespace(db=repos.db, repos=repos, clock=clock)
    p = prize()
    apply_page(box, dict(items=[p], next=1, more=False), 0)
    p['reward'] = UNVERIFIED_REWARD
    apply_page(box, dict(items=[p], next=2, more=False), 1)
    row = repos.db.connect().execute('SELECT reward,reward_status FROM campaign').fetchone()
    assert tuple(row) == ('', 'unknown')
    with repos.db.write() as c:
        c.execute("UPDATE campaign SET reward='人工奖品',reward_status='manual',reward_locked=1")
    apply_page(box, dict(items=[p], next=3, more=False), 2)
    assert repos.db.connect().execute('SELECT reward FROM campaign').fetchone()[0] == '人工奖品'


def prize(**changes):
    return dict(
        dyn_id="12345",
        up_uid="777",
        up_name="公开作者",
        reward="鼠标",
        kind="share",
        deadline_at="2030-01-01T08:00:00Z",
        **changes,
    )


def test_public_service_auth_whitelist_revision_and_html(tmp_path):
    client = TestClient(create_app(tmp_path / "public.sqlite", "x" * 32))
    auth = {"Authorization": "Bearer " + "x" * 32}
    assert client.post("/v1/catalog", json={"items": [prize()]}).status_code == 401
    for field in ("account", "cookie", "participation", "messages", "device", "token", "evidence"):
        assert (
            client.post(
                "/v1/catalog", headers=auth, json={"items": [prize(**{field: "private sentinel"})]}
            ).status_code
            == 422
        )
    assert (
        client.post("/v1/catalog", headers=auth, json={"items": [prize()]}).json()["changed"] == 1
    )
    assert (
        client.post("/v1/catalog", headers=auth, json={"items": [prize()]}).json()["changed"] == 0
    )
    first = client.get("/v1/catalog").json()
    revised = prize()
    revised["reward"] = "<script>test</script>耳机"
    assert (
        client.post("/v1/catalog", headers=auth, json={"items": [revised]}).json()["changed"] == 1
    )
    newer = client.get("/v1/catalog", params={"after": first["next"]}).json()
    assert len(newer["items"]) == 1 and newer["next"] > first["next"]
    assert "<script>" not in client.get("/").text
    for path in ("/v2/revisions", "/v2/authority", "/api/inbox", "/accounts"):
        assert client.get(path).status_code == 404
    assert "private sentinel" not in client.get("/v1/catalog").text


def test_import_revision_preserves_manual_and_private_state(repos, clock):
    box = SimpleNamespace(db=repos.db, repos=repos, clock=clock)
    p = prize()
    assert apply_page(box, dict(items=[p], next=1, more=False), 0)["added"] == 1
    p["reward"] = "键盘"
    p["deadline_at"] = "2030-01-02T08:00:00Z"
    assert apply_page(box, dict(items=[p], next=2, more=False), 1)["updated"] == 1
    con = repos.db.connect()
    assert con.execute("SELECT reward,confidence FROM campaign").fetchone()[:] == ("键盘", 0)
    con.execute(
        "UPDATE campaign SET reward='人工奖品',reward_locked=1,deadline_at='2031-02-01T00:00:00+00:00'"
    )
    p["reward"] = "remote"
    p["deadline_at"] = "2030-01-03T08:00:00Z"
    apply_page(box, dict(items=[p], next=3, more=False), 2)
    assert con.execute("SELECT reward,deadline_at FROM campaign").fetchone()[:] == (
        "人工奖品",
        "2031-02-01T00:00:00+00:00",
    )
    for table in ("account", "enrollment", "pool_item", "intent"):
        assert con.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
    with pytest.raises(ValueError):
        apply_page(box, dict(items=[p], next=3, more=True), 3)


def test_date_only_and_blocked_author(repos, clock):
    box = SimpleNamespace(db=repos.db, repos=repos, clock=clock)
    p = prize()
    p["deadline_at"] = None
    p["deadline_date"] = "2030-02-01"
    apply_page(box, dict(items=[p], next=1, more=False), 0)
    assert repos.db.connect().execute("SELECT deadline_at FROM campaign").fetchone()[0] is None
    assert repos.db.connect().execute("SELECT precision FROM deadline_check").fetchone()[0] == "day"
    repos.db.connect().execute("INSERT INTO up_block(uid,active) VALUES('777',1)")
    p["dyn_id"] = "2222"
    assert apply_page(box, dict(items=[p], next=2, more=False), 1)["added"] == 0


def test_publisher_handover_and_checkpoint_fence(tmp_path):
    key = ["a" * 32]
    client = TestClient(create_app(tmp_path / "public.sqlite", lambda: key[0]))
    old = {"Authorization": "Bearer " + key[0]}
    assert client.post("/v1/catalog", headers=old, json={"items": [prize()]}).status_code == 200
    key[0] = "b" * 32
    new = {"Authorization": "Bearer " + key[0], "X-Catalog-Revision": "12"}
    assert client.post("/v1/catalog", headers=new, json={"items": [prize()]}).status_code == 200
    assert client.post("/v1/catalog", headers=old, json={"items": [prize()]}).status_code == 401
    new["X-Catalog-Revision"] = "11"
    assert client.post("/v1/catalog", headers=new, json={"items": [prize()]}).status_code == 409
