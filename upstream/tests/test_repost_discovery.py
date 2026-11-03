from unittest.mock import patch
import json
import httpx
import pytest

from src import maintenance as m
from src.repost_discovery import discover, associations, scan_status
from src.cleanup_api import CleanupApi
from src.db.models import ActivityRow, ParticipationRow, ParticipationLinkRow
from src.db.session import session_scope
from src.task_control import TaskCancelled


@pytest.fixture(autouse=True)
def sandbox(isolated_home, monkeypatch):
    send = httpx.Client.send
    def local_only(client, request, **kwargs):
        if request.url.host != "testserver":
            pytest.fail("Unexpected platform request")
        return send(client, request, **kwargs)
    monkeypatch.setattr(httpx.Client, "send", local_only)


def seed(uid="123", did="10", legacy=""):
    with session_scope() as s:
        if not s.get(ActivityRow, did):
            s.add(ActivityRow(dynamic_id=did, activity_status="已结束", payload_json=json.dumps({"sender_uid":"900"})))
        s.add(ParticipationRow(uid=uid, dynamic_id=did, user_status="已参加"))
        s.add(ParticipationLinkRow(uid=uid, dynamic_id=did, up_uid="900", repost_id=legacy))


def forward(rid="810", original="10", uid="123", up="900"):
    return {"type":"DYNAMIC_TYPE_FORWARD", "id_str":rid,
            "modules":{"module_author":{"mid":uid,"pub_ts":1000}},
            "orig":{"id_str":original,"modules":{"module_author":{"mid":up}}}}


class Pages:
    def __init__(self, pages): self.pages, self.offsets = pages, []
    def assert_account(self): pass
    def repost_page(self, offset):
        self.offsets.append(offset)
        index = int(offset or 0)
        value = self.pages[index]
        if isinstance(value, Exception): raise value
        return {"items":value,"offset":str(index+1),"has_more":index+1<len(self.pages)}


def state(uid="123"):
    return associations(uid, m.link_rows(uid))["10"]


def test_continue_finds_older_repost_and_does_not_enable_cleanup():
    seed()
    api = Pages([[forward(str(100+i), original="99")] for i in range(5)] + [[forward()]])
    discover(api,"123")
    assert state()["state"] == "not_found"
    assert scan_status("123")["can_continue"]
    assert scan_status("123")["pages"] == 5
    # A fresh API instance represents resume after restart.
    resumed = Pages(api.pages)
    discover(resumed,"123",mode="continue")
    assert resumed.offsets == ["5"]
    assert state()["state"] == "candidate"
    link = m.link_rows("123")["10"]
    assert link["repost_id"] == "810" and link["origin"] == "historical"
    assert not link["repost_recorded"] and not link["follow_added"]
    assert scan_status("123")["finished"]
    with pytest.raises(ValueError): discover(resumed,"123",mode="continue")


def test_duplicates_across_batches_and_legacy_candidates_remain_visible():
    seed(legacy="810")
    assert state()["state"] == "candidate"
    pages = [[forward("810")]] + [[forward(str(100+i), original="99")] for i in range(4)] + [[forward("811")]]
    api = Pages(pages)
    discover(api,"123")
    discover(api,"123",mode="continue")
    assert state()["state"] == "ambiguous"
    assert {r["repost_id"] for r in state()["candidates"]} == {"810","811"}
    assert m.link_rows("123")["10"]["repost_id"] == ""
    discover(Pages([[forward("810")]]),"123")
    assert state()["state"] == "ambiguous"  # a shorter rescan cannot discard a known candidate


@pytest.mark.parametrize("error",[TimeoutError(),TaskCancelled("cancel")])
def test_partial_failure_keeps_committed_page_and_cursor(error):
    seed()
    api = Pages([[forward()],error])
    with pytest.raises(type(error)): discover(api,"123")
    assert scan_status("123")["pages"] == 1
    assert state()["state"] == "candidate"
    api.pages[1] = []
    discover(api,"123",mode="continue")
    assert api.offsets == ["","1","1"]
    assert len(state()["candidates"]) == 1


def test_wrong_author_original_and_up_are_not_silently_accepted():
    seed()
    seed(uid="456")
    discover(Pages([[forward(uid="456"),forward(original="99")]]),"123")
    assert state()["state"] == "not_found"
    assert state("456")["state"] == "unscanned"
    discover(Pages([[forward(up="901")]]),"123")
    assert state()["state"] == "ambiguous"
    assert not m.link_rows("123")["10"]["repost_recorded"]


@pytest.mark.parametrize("data", [{}, {"items":[],"has_more":True,"offset":"a"},
                                   {"items":[{}],"has_more":True,"offset":"a"}])
def test_invalid_feed_does_not_look_like_success(data):
    api = object.__new__(CleanupApi)
    api.uid = "123"
    api.request = lambda *a,**kw:{"code":0,"data":data}
    with pytest.raises(RuntimeError): api.repost_page("a")


def test_filters_counts_and_resume_mode_route():
    seed(legacy="810")
    seed(did="11")
    from fastapi.testclient import TestClient
    from web.app import app
    with TestClient(app) as client, patch("web.maintenance_routes.current_uid",return_value="123"):
        response = client.get("/api/maintenance/activities?association=candidate").json()
        assert response["total"] == 1
        assert response["association_counts"]["candidate"] == 1
        assert response["association_counts"]["unscanned"] == 1
        with patch("web.maintenance_routes.start_job",return_value={"ok":True}) as start:
            assert client.post("/api/maintenance/discover?mode=continue").status_code == 200
            start.assert_called_once_with("maintenance_discover_all", {"mode":"continue"})
        assert client.post("/api/maintenance/discover?mode=invalid").status_code == 400


def test_v4_upgrade_backs_up_database_and_keeps_old_candidates(isolated_home):
    import sqlite3
    from src.db.engine import db_path, get_engine
    from src.db.schema import init_db
    seed(legacy="810")
    get_engine().dispose()
    with sqlite3.connect(db_path()) as connection:
        connection.execute("DROP TABLE repost_scans")
        connection.execute("DROP TABLE repost_discoveries")
        connection.execute("UPDATE schema_meta SET version=4")
    init_db()
    assert state()["state"] == "candidate"
    assert not scan_status("123")["can_continue"]
    backups = list(db_path().parent.glob("binggo.pre-v7.*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 4
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='repost_scans'").fetchone() is None
