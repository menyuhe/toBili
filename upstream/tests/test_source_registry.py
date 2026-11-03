from unittest.mock import patch, MagicMock
import pytest
import httpx
from src import source_registry as r
from src.db.snapshots import save_ds_check_dict
from src.sources.common import load_previous_output
from web.activity_service import get_summary
from web.actions import run_action


@pytest.fixture(autouse=True)
def sandbox(isolated_home, monkeypatch):
    send = httpx.Client.send
    def local_only(client, request, **kw):
        if request.url.host != "testserver":
            pytest.fail("Unexpected network")
        return send(client, request, **kw)
    monkeypatch.setattr(httpx.Client,"send",local_only)


def test_add_edit_delete_persist_and_deleted_defaults_stay_deleted():
    r.remove_source("DS-2")
    item=r.save_source(dict(name="新 UP",mid="987654",backup=True))
    assert item in r.catalog()
    renamed=r.save_source(dict(name="重命名",mid="987654",backup=False),item["id"])
    assert renamed["id"]==item["id"]
    changed=r.save_source(dict(name="不同账号",mid="987655",backup=False),item["id"])
    assert changed["id"]!=item["id"]
    r.remove_source(changed["id"])
    assert all(x["id"] not in {"DS-2",changed["id"]} for x in r.catalog())


def test_duplicate_and_invalid_uid():
    with pytest.raises(ValueError):r.save_source(dict(name="重复",mid="100680137"))
    with pytest.raises(ValueError):r.save_source(dict(name="UP",mid="../cookies.txt"))


def test_order_persists_in_summary_and_rejects_stale_or_duplicate_lists():
    original = r.catalog()
    ids = [row["id"] for row in reversed(original)]
    r.reorder_sources(ids)
    assert r.catalog() == list(reversed(original))
    assert [row["id"] for row in get_summary()["sources"]] == ids
    for invalid in (ids[:-1], [ids[0]] * len(ids), [*ids[:-1], "DS-missing"]):
        with pytest.raises(ValueError):
            r.reorder_sources(invalid)
        assert [row["id"] for row in r.catalog()] == ids
    r.save_source(dict(name="新增合集",mid="998877"))
    with pytest.raises(ValueError):
        r.reorder_sources(ids)


def test_order_route_is_not_treated_as_source_id():
    from fastapi.testclient import TestClient
    from web.app import app
    ids = [row["id"] for row in reversed(r.catalog())]
    with TestClient(app) as client:
        response = client.put("/api/collections/order", json={"source_ids":ids})
        assert response.status_code == 200
        assert [row["id"] for row in response.json()["items"]] == ids


def test_custom_snapshot_and_summary(isolated_home):
    item=r.save_source(dict(name="我的合集",mid="987654",backup=False))
    save_ds_check_dict(dict(source_id=item["id"],activity_links=[],title="最新合集",checked_at=200))
    result=load_previous_output(isolated_home/'data/output'/f"ds{item['id'].split('-')[1]}_latest.json")
    assert result["title"]=="最新合集"
    summary=next(s for s in get_summary()["sources"] if s["id"]==item["id"])
    assert summary["name"]=="我的合集" and summary["mid"]=="987654"


def test_removed_source_cannot_run_and_empty_catalog_is_safe():
    for row in r.catalog():r.remove_source(row["id"])
    with pytest.raises(ValueError):run_action("refresh_source",{"source_id":"DS-2"})
    assert run_action("refresh_all")["ok"]


def test_custom_source_api_accepts_and_remove_preserves_history():
    from fastapi.testclient import TestClient
    from web.app import app
    item=r.save_source(dict(name="自定义",mid="987654",backup=False))
    with TestClient(app) as client, patch("web.app.get_account_profile",return_value={"logged_in":True}),patch("web.api_errors.is_llm_ready",return_value=True),patch("web.app.runner.try_start",return_value=1):
        assert client.post("/api/jobs",json={"action":"refresh_source","params":{"source_id":item["id"]}}).status_code==200
        assert client.get("/api/collections").json()["items"]
