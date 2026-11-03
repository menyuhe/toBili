import time

from src.activity_store import append_activities, load_activities, replace_all_activities, update_activity
from src.db.activity_codec import activity_dict_to_row, row_to_activity_dict
from web.activity_service import list_activities


def activity(dynamic_id, *, added_at=0, lottery_time=None, status="未参加"):
    return {
        "dynamic_id": str(dynamic_id), "added_at": added_at,
        "lottery_type": "互动抽奖", "draw_status": "active",
        "activity_status": status, "status_classified": True,
        "lottery_time": lottery_time, "repost_count": int(dynamic_id) % 7,
    }


def test_added_time_survives_updates_and_full_replacement(isolated_home, monkeypatch):
    base = int(time.time())
    first = activity("1111111111111111111", lottery_time=base + 86400)
    monkeypatch.setattr("src.activity_store.time.time", lambda: base)
    append_activities([first])
    monkeypatch.setattr("src.activity_store.time.time", lambda: base + 60)
    second = activity("2222222222222222222", lottery_time=base + 86400)
    append_activities([second])
    update_activity(first["dynamic_id"], {**first, "added_at": base + 999, "repost_count": 999})
    replace_all_activities([first, second])
    stored = {item["dynamic_id"]: item["added_at"] for item in load_activities()}
    assert stored == {first["dynamic_id"]: base, second["dynamic_id"]: base + 60}


def test_default_sort_pending_then_newest_before_pagination_and_triple(isolated_home, monkeypatch):
    monkeypatch.setattr("web.activity_service.load_participations", lambda: {})
    monkeypatch.setattr("web.activity_service._load_participation_actions", lambda: {})
    now = int(time.time())
    items = [activity(1111111111111111111 + i, added_at=now + i, lottery_time=now + 86400) for i in range(25)]
    # Joined activities follow pending ones even across page boundaries.
    items[-1]["activity_status"] = "已参加"
    items[0]["activity_status"] = "已参加"
    replace_all_activities(items)
    expected = [item["dynamic_id"] for item in reversed(items[1:-1])] + [items[-1]["dynamic_id"], items[0]["dynamic_id"]]
    page1, page2 = list_activities(page=1), list_activities(page=2)
    assert [item["dynamic_id"] for item in page1["items"] + page2["items"]] == expected


def test_draw_time_sort_both_directions_unknown_last(isolated_home, monkeypatch):
    monkeypatch.setattr("web.activity_service.load_participations", lambda: {})
    monkeypatch.setattr("web.activity_service._load_participation_actions", lambda: {})
    now = int(time.time())
    items = [
        activity("1111111111111111111", added_at=now, lottery_time=now + 7200),
        activity("2222222222222222222", added_at=now + 1, lottery_time=None),
        activity("3333333333333333333", added_at=now + 2, lottery_time=now + 3600),
    ]
    items[2]["activity_status"] = "已参加"
    replace_all_activities(items)
    for direction, indexes in [("asc", [2, 0, 1]), ("desc", [0, 2, 1])]:
        result = list_activities(sort="lottery_time", order=direction)
        assert [item["dynamic_id"] for item in result["items"]] == [items[i]["dynamic_id"] for i in indexes]


def test_legacy_added_time_falls_back_to_enrichment_not_refresh():
    item = activity("1111111111111111111")
    item["enriched_at"] = 100
    row = activity_dict_to_row(item, updated_at=500)
    assert row_to_activity_dict(row)["added_at"] == 100
