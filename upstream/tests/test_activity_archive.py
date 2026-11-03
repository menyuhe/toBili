import time
from fastapi.testclient import TestClient
from src.activity_store import load_activities, replace_all_activities
from src.activity_archive import archived_activity_ids, restore_archived_activity
from web.activity_service import archive_ended_activities, get_summary, list_activities
from web.app import app


def seed_archive_test():
    now = int(time.time())
    items = [
        {"dynamic_id": "1111111111111111111", "lottery_type": "互动抽奖", "status_classified": True,
         "activity_status": "未参加", "draw_status": "active", "lottery_time": now + 86400},
        {"dynamic_id": "2222222222222222222", "lottery_type": "互动抽奖", "status_classified": True,
         "activity_status": "已参加", "draw_status": "active", "lottery_time": now + 86400},
        {"dynamic_id": "3333333333333333333", "lottery_type": "互动抽奖", "status_classified": True,
         "activity_status": "已结束", "draw_status": "ended", "lottery_time": now - 1000},
    ]
    replace_all_activities(items)
    return items


def test_archive_keeps_history_and_excludes_live_activities(isolated_home, monkeypatch):
    monkeypatch.setattr("web.activity_service.load_participations", lambda: {})
    monkeypatch.setattr("web.activity_service._load_participation_actions", lambda: {})
    seed_archive_test()
    assert archive_ended_activities() == 1
    assert archive_ended_activities() == 0
    assert len(load_activities()) == 3
    assert archived_activity_ids() == {"3333333333333333333"}
    assert get_summary()["total_count"] == 2
    assert get_summary()["archived_count"] == 1
    assert list_activities()["total"] == 2
    archived = list_activities(archived=True, page=99)
    assert archived["page"] == 1
    assert archived["total"] == 1
    assert archived["items"][0]["archived"]
    assert not archived["items"][0]["can_participate"]
    assert "triple_targets" not in archived


def test_archive_survives_sync_and_can_be_restored(isolated_home, monkeypatch):
    monkeypatch.setattr("web.activity_service.load_participations", lambda: {})
    monkeypatch.setattr("web.activity_service._load_participation_actions", lambda: {})
    original = seed_archive_test()
    archive_ended_activities()
    replace_all_activities(original)
    assert list_activities(archived=True)["total"] == 1
    assert restore_archived_activity("3333333333333333333")
    assert not restore_archived_activity("3333333333333333333")
    assert list_activities(archived=True)["total"] == 0
    assert len(load_activities()) == 3
    assert get_summary()["total_count"] == 3


def test_archive_api_is_local_and_reversible(isolated_home, monkeypatch):
    monkeypatch.setattr("web.activity_service.load_participations", lambda: {})
    monkeypatch.setattr("web.activity_service._load_participation_actions", lambda: {})
    seed_archive_test()
    with TestClient(app) as client:
        response = client.post("/api/activities/archive-ended")
        assert response.status_code == 200
        assert response.json()["archived_count"] == 1
        rows = client.get("/api/activities?archived=true").json()
        assert rows["total"] == 1
        assert client.post("/api/activities/3333333333333333333/restore").json()["restored"]
        assert client.post("/api/activities/not-an-id/restore").status_code == 400


def test_archive_covers_more_than_one_page(isolated_home, monkeypatch):
    monkeypatch.setattr("web.activity_service.load_participations", lambda: {})
    monkeypatch.setattr("web.activity_service._load_participation_actions", lambda: {})
    items = seed_archive_test()
    ended = items[-1]
    items.extend([{**ended, "dynamic_id": str(4444444444444444400 + i)} for i in range(25)])
    replace_all_activities(items)
    assert archive_ended_activities() == 26
    assert list_activities()["total"] == 2
    assert list_activities(archived=True)["pages"] == 2
    assert len(load_activities()) == 28


def test_manual_removal_unknown_deadline_and_pool_exclusion(isolated_home, monkeypatch):
    from types import SimpleNamespace
    from web.app import api_archive_activity, api_restore_archived
    from web.pool_routes import candidates
    monkeypatch.setattr('web.app.runner.get_status', lambda: SimpleNamespace(state='idle'))
    items = seed_archive_test()
    items[0]['lottery_time'] = None
    replace_all_activities(items)
    did = items[0]['dynamic_id']
    assert did in candidates()
    assert api_archive_activity(did)['archived_count'] == 1
    assert api_archive_activity(did)['archived_count'] == 0
    assert did not in candidates()
    assert len(load_activities()) == 3
    assert api_restore_archived(did)['restored']
    assert did in candidates()


def test_manual_removal_refuses_running_task(isolated_home, monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from web.app import api_archive_activity
    import pytest
    items = seed_archive_test()
    monkeypatch.setattr('web.app.runner.get_status', lambda: SimpleNamespace(state='running'))
    with pytest.raises(HTTPException) as error:
        api_archive_activity(items[0]['dynamic_id'])
    assert error.value.status_code == 409
    assert not archived_activity_ids()
