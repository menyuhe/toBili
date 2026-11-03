from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src import lottery_discovery as discovery


def card(did, original=None):
    item = {"id_str": str(did), "modules": {"module_author": {"pub_ts": did}}}
    if original is not None:
        item["orig"] = card(original)["dynamic_card_item"]
    return {"dynamic_card_item": item}


@pytest.fixture
def setup_discovery(isolated_home, monkeypatch):
    monkeypatch.setattr(discovery, "TOPICS", ((1, "测试抽奖"),))
    monkeypatch.setattr(discovery, "BilibiliClient", MagicMock())
    monkeypatch.setattr(discovery, "history_cutoff", lambda: 1)
    calls = []
    def pipeline(ids, **kwargs):
        assert all(url.startswith("https://www.bilibili.com/opus/") for url in ids)
        calls.append([url.rsplit("/", 1)[-1] for url in ids])
        return SimpleNamespace(ok=True, persisted_count=len(ids), new_link_count=len(ids))
    monkeypatch.setattr(discovery, "run_new_links_pipeline", pipeline)
    return calls


def test_reposts_are_resolved_sorted_and_deduped():
    assert discovery._candidates([card(30, 10), card(31, 10), card(20), {}]) == ["20", "10"]


def test_backfill_resume_and_newest_priority(setup_discovery, monkeypatch):
    pages = {"": ([card(30, 25)], "p2", True), "p2": ([card(20)], "p3", True),
             "p3": ([card(10)], "", False)}
    reads = []
    def fetch(client, tid, offset):
        reads.append(offset)
        return pages[offset]
    monkeypatch.setattr(discovery, "_page", fetch)
    first = discovery.sync_discovery()
    assert not first["history_complete"] and reads == ["", "p2"]
    assert discovery._load()["topics"]["1"]["history_offset"] == "p3"
    pages[""] = ([card(40), card(30, 25)], "p2", True)
    second = discovery.sync_discovery()
    assert second["history_complete"]
    assert setup_discovery == [["25"], ["20"], ["40"], ["10"]]
    assert discovery.sync_discovery()["persisted_count"] == 0


def test_failed_page_does_not_advance_checkpoint(setup_discovery, monkeypatch):
    monkeypatch.setattr(discovery, "_page", lambda *args: ([card(30)], "next", True))
    monkeypatch.setattr(discovery, "run_new_links_pipeline", MagicMock(side_effect=RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="offline"):
        discovery.sync_discovery()
    saved = discovery._load()
    assert saved["topics"]["1"]["seen"] == []
    assert saved["topics"]["1"]["history_offset"] == ""
    assert saved["last_run"]["state"] == "error"


def test_completed_page_survives_next_page_failure(setup_discovery, monkeypatch):
    def fetch(client, tid, offset):
        if offset:
            raise RuntimeError("offline")
        return [card(30)], "next", True
    monkeypatch.setattr(discovery, "_page", fetch)
    with pytest.raises(RuntimeError, match="offline"):
        discovery.sync_discovery()
    saved = discovery._load()
    assert saved["topics"]["1"]["history_offset"] == "next"
    assert saved["topics"]["1"]["seen"] == ["30"]


def test_multiple_new_head_pages_do_not_overwrite_unfinished_scan(setup_discovery, monkeypatch):
    discovery._save({"history_days": discovery.HISTORY_DAYS, "topics": {"1": {"seen": ["10"], "history_done": True,
        "history_offset": "", "update_offsets": ["old"]}}})
    pages = {"": ([card(100)], "new", True), "old": ([card(30)], "old2", True),
             "old2": ([card(10)], "older", True), "new": ([card(90)], "new2", True)}
    monkeypatch.setattr(discovery, "_page", lambda client, tid, offset: pages[offset])
    discovery.sync_discovery()
    assert discovery._load()["topics"]["1"]["update_offsets"] == ["old2", "new"]
    discovery.sync_discovery()
    assert discovery._load()["topics"]["1"]["update_offsets"] == ["new"]
    discovery.sync_discovery()
    assert ["90"] in setup_discovery


@pytest.mark.parametrize("data", [{}, {"items": [], "has_more": True, "offset": "next"},
    {"items": [card(10)], "has_more": True, "offset": "same"}])
def test_invalid_pagination_does_not_claim_finished(data):
    client = MagicMock()
    client.get_json.return_value = {"code": 0, "data": {"topic_card_list": data}}
    with pytest.raises(RuntimeError):
        discovery._page(client, 1, "same")


def test_feed_links_reach_real_pipeline(isolated_home, monkeypatch):
    from src.pipeline import refresh_all_pipeline as pipeline
    from src.pipeline.classify_step import ClassifyOutcome
    did = 1258668719066841092
    monkeypatch.setattr(discovery, "TOPICS", ((1, "测试"),))
    monkeypatch.setattr(discovery, "BilibiliClient", MagicMock())
    monkeypatch.setattr(discovery, "_page", lambda *args: ([card(did)], "", False))
    monkeypatch.setattr(pipeline, "BilibiliClient", MagicMock())
    classify = MagicMock(return_value=ClassifyOutcome(str(did), None, True, "非抽奖活动"))
    monkeypatch.setattr(pipeline, "classify_new_link", classify)
    result = discovery.sync_discovery()
    assert result["new_link_count"] == 1
    assert classify.call_args.args[1] == str(did)


def test_action_dispatch_and_summary(setup_discovery, monkeypatch):
    from web.actions import run_action
    from web.activity_service import get_summary
    from web.schemas.jobs import ALLOWED_JOB_ACTIONS
    monkeypatch.setattr(discovery, "_page", lambda *args: ([card(30)], "", False))
    progress = []
    assert "refresh_discovery" in ALLOWED_JOB_ACTIONS
    payload = run_action("refresh_discovery", on_progress=lambda **values: progress.append(values))
    assert payload["ok"] and payload["result"]["persisted_count"] == 1
    assert progress[-1]["step"] == progress[-1]["total"] == 1
    assert get_summary()["discovery"]["history_complete"]


def test_continuous_history_finishes_without_repeating_head(setup_discovery, monkeypatch):
    pages = {'': ([card(40)], 'p2', True), 'p2': ([card(30)], 'p3', True),
             'p3': ([card(20)], 'p4', True), 'p4': ([card(10)], '', False)}
    reads = []
    def fetch(client, tid, offset):
        reads.append(offset)
        return pages[offset]
    monkeypatch.setattr(discovery, '_page', fetch)
    from web.actions import run_action
    result = run_action('refresh_discovery_history')
    assert result['ok'] and result['result']['history_complete']
    assert reads == ['', 'p2', 'p3', 'p4']
    status = discovery.discovery_status()
    assert status['topics'][0]['checked_count'] == 4
    assert status['topics'][0]['oldest_pub_ts'] == 10


def test_continuous_history_cancel_resumes_checkpoint(setup_discovery, monkeypatch):
    from src.task_control import TaskCancelled
    def fetch(client, tid, offset):
        if offset == 'p3':
            raise TaskCancelled('任务已取消')
        return ([card(30)], 'p2', True) if not offset else ([card(20)], 'p3', True)
    monkeypatch.setattr(discovery, '_page', fetch)
    with pytest.raises(TaskCancelled):
        discovery.sync_discovery(continuous_history=True)
    saved = discovery._load()
    assert saved['last_run']['state'] == 'cancelled'
    assert saved['topics']['1']['history_offset'] == 'p3'
    assert saved['topics']['1']['seen'] == ['20', '30']
    reads = []
    def resumed(client, tid, offset):
        reads.append(offset)
        return ([card(30)], 'p2', True) if not offset else ([card(10)], '', False)
    monkeypatch.setattr(discovery, '_page', resumed)
    assert discovery.sync_discovery(continuous_history=True)['history_complete']
    assert reads == ['', 'p3']


def test_continuous_history_stops_on_cursor_cycle(setup_discovery, monkeypatch):
    pages = {'': ([card(30)], 'p2', True), 'p2': ([card(20)], 'p3', True),
             'p3': ([card(10)], 'p2', True)}
    monkeypatch.setattr(discovery, '_page', lambda client, tid, offset: pages[offset])
    with pytest.raises(RuntimeError, match='分页未前进'):
        discovery.sync_discovery(continuous_history=True)
    assert discovery._load()['topics']['1']['history_offset'] == 'p3'


def test_recent_window_filters_old_originals_and_stops_at_boundary(setup_discovery, monkeypatch):
    monkeypatch.setattr(discovery, 'history_cutoff', lambda: 100)
    pages = {'': ([card(200), card(190, 40), {'dynamic_card_item': {'id_str': '180'}}], 'p2', True),
             'p2': ([card(99), card(98)], 'must-not-read', True)}
    reads = []
    def fetch(client, tid, offset):
        reads.append(offset)
        return pages[offset]
    monkeypatch.setattr(discovery, '_page', fetch)
    result = discovery.sync_discovery(continuous_history=True)
    assert result['history_complete']
    assert reads == ['', 'p2']
    assert setup_discovery == [['200']]
    assert discovery._load()['publication_times']['40'] == 40


def test_legacy_old_cursor_is_reset_and_old_retries_removed(setup_discovery, monkeypatch):
    discovery._save({'topics': {'1': {'seen': [], 'history_done': False,
        'history_offset': 'years-old', 'update_offsets': ['also-old']}},
        'pending_ids': ['10'], 'publication_times': {'10': 10}})
    monkeypatch.setattr(discovery, 'history_cutoff', lambda: 100)
    def fetch(client, tid, offset):
        assert offset == ''
        return [card(200)], '', False
    monkeypatch.setattr(discovery, '_page', fetch)
    result = discovery.sync_discovery(continuous_history=True)
    assert result['pending_count'] == 0
    assert setup_discovery == [['200']]


def test_one_old_pinned_card_does_not_stop_recent_pagination(setup_discovery, monkeypatch):
    monkeypatch.setattr(discovery, 'history_cutoff', lambda: 100)
    pages = {'': ([card(5), card(200)], 'p2', True), 'p2': ([card(150)], '', False)}
    monkeypatch.setattr(discovery, '_page', lambda client, tid, offset: pages[offset])
    discovery.sync_discovery(continuous_history=True)
    assert setup_discovery == [['200'], ['150']]


def test_seven_day_window_migrates_90_day_cursor_and_filters_before_classification(isolated_home, monkeypatch):
    now = 1800000000
    cutoff = now - 7 * 86400
    monkeypatch.setattr(discovery.time, 'time', lambda: now)
    monkeypatch.setattr(discovery, 'TOPICS', ((1, '测试抽奖'),))
    monkeypatch.setattr(discovery, 'BilibiliClient', MagicMock())
    assert discovery.history_cutoff() == cutoff
    discovery._save({'history_days': 90, 'topics': {'1': {'seen': [], 'history_done': False,
        'history_offset': 'old-90-day-cursor', 'update_offsets': ['old-head']}},
        'pending_ids': ['1', '2', '3'], 'publication_times': {'1': cutoff - 1, '2': cutoff},
        'processed_ids': ['99']})
    classified, reads = [], []
    def pipeline(urls, **kwargs):
        classified.extend(url.rsplit('/', 1)[-1] for url in urls)
        return SimpleNamespace(ok=True, persisted_count=len(urls), new_link_count=len(urls))
    def fetch(client, topic, offset):
        reads.append(offset)
        if not offset:
            return [card(now), card(now - 1, cutoff - 1), card(cutoff)], 'boundary', True
        assert offset == 'boundary'
        return [card(cutoff - 1)], 'must-not-read', True
    monkeypatch.setattr(discovery, 'run_new_links_pipeline', pipeline)
    monkeypatch.setattr(discovery, '_page', fetch)
    result = discovery.sync_discovery(continuous_history=True)
    assert reads == ['', 'boundary']
    assert classified == [str(now), str(cutoff), '2']
    saved = discovery._load()
    assert saved['history_days'] == 7 and saved['pending_ids'] == []
    assert '99' in saved['processed_ids']
    assert result['history_complete'] and discovery.discovery_status()['history_days'] == 7


@pytest.mark.parametrize(("today", "expected"), [
    ("2026-11-01", "2026-10-25"), ("2026-01-01", "2025-12-25"),
    ("2026-03-01", "2026-02-22"), ("2028-03-01", "2028-02-23"),
])
def test_history_window_crosses_calendar_boundaries(monkeypatch, today, expected):
    from datetime import datetime
    from src.lottery_time import BEIJING_TZ
    now = datetime.fromisoformat(today).replace(tzinfo=BEIJING_TZ)
    monkeypatch.setattr(discovery.time, "time", lambda: now.timestamp())
    cutoff = discovery.history_cutoff()
    assert datetime.fromtimestamp(cutoff, BEIJING_TZ).date().isoformat() == expected
    assert discovery._candidates([card(cutoff - 1), card(cutoff), card(int(now.timestamp()))], since=cutoff) == [str(int(now.timestamp())), str(cutoff)]
