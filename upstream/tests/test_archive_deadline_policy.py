import pytest
from src.account_context import account_scope
from src import accounts
from src.activity_archive import (archive_activity_ids, archived_activity_ids,
                                  restore_archived_activity, restore_publication_archives)
from src.activity_store import replace_all_activities

NOW = 2_000_000_000
OLD = NOW - 86400 * 30


def seed():
    items = []
    for did, deadline in (("101", NOW + 100), ("102", NOW - 1), ("103", None)):
        items.append({"dynamic_id": did, "lottery_type": "转发抽奖", "draw_status": "active",
                      "lottery_time": deadline, "published_at": NOW - 86400 * 200})
    replace_all_activities(items)


@pytest.mark.parametrize("uid", [None, "1"])
def test_publication_age_keeps_future_and_unknown_deadlines(isolated_home, uid):
    seed()
    from contextlib import nullcontext
    with account_scope(uid) if uid else nullcontext():
        assert archive_activity_ids({"101", "102", "103"}, reason="publication_age", now=NOW) == 1
        assert archived_activity_ids() == {"102"}
        assert archive_activity_ids({"101"}, reason="ended", now=NOW) == 0
        assert archive_activity_ids({"101"}, now=NOW) == 1
        assert archived_activity_ids() == {"101", "102"}


def test_repair_requires_batch_evidence_and_preserves_manual_other_account_and_ended(isolated_home):
    seed()
    # 104: manual in the same second; 105: subsequently removed again; 106: not in report.
    from src.activity_store import append_activities
    append_activities([{"dynamic_id": str(i), "lottery_type": "转发抽奖", "draw_status": "active",
                       "lottery_time": NOW + 100} for i in (104, 105, 106)])
    ids = [str(i) for i in range(101, 107)]
    for uid in ("1", "2"):
        accounts.write_state(uid, "archives", ids)
        accounts.write_state(uid, "archive_times", {did: OLD if uid == "1" else OLD + 1 for did in ids})
    with account_scope("1"):
        archive_activity_ids({"104"}, now=OLD)  # explicit manual intent even if already archived
        restore_archived_activity("105")
        archive_activity_ids({"105"}, now=OLD + 1)
    evidence = {"time": OLD, "cutoff": OLD - 86400 * 7, "archived_ids": ids[:-1]}
    before = accounts.read_state("1", "archives")
    assert restore_publication_archives("1", evidence, now=NOW) == ["101"]
    assert accounts.read_state("1", "archives") == before  # dry-run is read-only
    assert restore_publication_archives("1", evidence, now=NOW, dry_run=False) == ["101"]
    assert set(accounts.read_state("1", "archives")) == set(ids) - {"101"}
    assert accounts.read_state("2", "archives") == ids
    assert restore_publication_archives("2", evidence, now=NOW, dry_run=False) == []
    assert restore_publication_archives("1", evidence, now=NOW, dry_run=False) == []
    assert len(accounts.read_state("1", "archive_repairs")) == 1
    assert "101" not in accounts.read_state("1", "archive_times")
    with account_scope("1"):
        archive_activity_ids({"101"}, now=OLD)
    assert restore_publication_archives("1", evidence, now=NOW, dry_run=False) == []


def test_bulk_ended_cannot_archive_future_even_with_stale_ended_status(isolated_home, monkeypatch):
    from web.activity_service import archive_ended_activities
    import time
    replace_all_activities([{"dynamic_id": "101", "lottery_type": "转发抽奖", "draw_status": "active",
                            "lottery_time": int(time.time()) + 86400, "activity_status": "已结束", "status_classified": True}])
    monkeypatch.setattr("web.activity_service.load_participations", lambda: {})
    assert archive_ended_activities() == 0
    assert archived_activity_ids() == set()
