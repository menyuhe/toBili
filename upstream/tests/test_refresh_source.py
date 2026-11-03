from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from web.actions import DS_HANDLER_BY_ID, REFRESH_SOURCE_TOTAL, run_action
from web.app import app


def test_refresh_source_total_is_four() -> None:
    assert REFRESH_SOURCE_TOTAL == 4
    assert set(DS_HANDLER_BY_ID) == {"DS-2", "DS-3", "DS-4", "DS-6", "DS-7", "DS-8", "DS-9", "DS-10"}


def test_refresh_source_rejects_unknown_id() -> None:
    with pytest.raises(ValueError, match="未知数据源"):
        run_action("refresh_source", {"source_id": "DS-99"})


@pytest.mark.parametrize("source_id", ["DS-0", "DS-1", "DS-5"])
def test_refresh_source_api_rejects_invalid_id(source_id: str) -> None:
    client = TestClient(app)
    with patch("web.app.get_account_profile", return_value={"logged_in": True}), patch(
        "web.api_errors.is_llm_ready", return_value=True
    ), patch("web.app.runner.try_start", return_value=1) as start_mock:
        resp = client.post("/api/jobs", json={"action": "refresh_source", "params": {"source_id": source_id}})
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert "无效" in data["detail"]
    start_mock.assert_not_called()


@pytest.mark.parametrize("source_id", ["DS-3", "DS-7", "DS-8", "DS-9", "DS-10"])
def test_refresh_source_api_accepts_valid_id(source_id) -> None:
    client = TestClient(app)
    with patch("web.app.get_account_profile", return_value={"logged_in": True}), patch(
        "web.api_errors.is_llm_ready", return_value=True
    ), patch("web.app.runner.try_start", return_value=1) as start_mock:
        resp = client.post("/api/jobs", json={"action": "refresh_source", "params": {"source_id": source_id}})
    assert resp.status_code == 200
    start_mock.assert_called_once_with("refresh_source", {"source_id": source_id}, source="ui")


@pytest.mark.parametrize("action", ["refresh_source", "refresh_all"])
def test_unchanged_check_refreshes_summary_and_uses_source_name(isolated_home, monkeypatch, action):
    from src.db.snapshots import save_ds_check_dict
    from src.sources import ds2_fanqiao
    from src.state_store import set_last_container
    from web.activity_service import get_summary, SOURCE_LABELS
    from web.job_runner import _build_label

    source_id = "DS-2"
    url = "https://www.bilibili.com/read/cv123"
    link = "https://www.bilibili.com/opus/1234567890123456789"
    set_last_container(source_id, url)
    save_ds_check_dict({
        "source_id": source_id, "updated": True, "container_url": url,
        "title": "旧合集", "published_at": 100, "checked_at": 200,
        "activity_links": [link], "link_hints": {"1234567890123456789": "互动抽奖"},
    })
    client = MagicMock()
    client.__enter__.return_value = client
    client.get_latest_article.return_value = {"id": 123, "title": "旧合集", "publish_time": 100}
    monkeypatch.setattr(ds2_fanqiao, "BilibiliClient", lambda: client)
    monkeypatch.setattr("web.actions.DS_HANDLERS", [(source_id, ds2_fanqiao.check_update, ds2_fanqiao.save_result)])
    monkeypatch.setattr("src.source_registry.catalog", lambda: [{"id":source_id,"mid":str(ds2_fanqiao.MID),"name":SOURCE_LABELS[source_id],"backup":False}])
    events = []
    with patch("web.actions.run_refresh_all_pipeline") as pipeline:
        for checked_at in (300, 400):
            with patch("src.sources.ds2_fanqiao.time.time", return_value=checked_at):
                result = run_action(action, {"source_id": source_id}, on_progress=lambda **event: events.append(event))
            source = next(item for item in get_summary()["sources"] if item["id"] == source_id)
            assert source["checked_at"] == checked_at
            assert source["updated"] is False
            assert source["link_count"] == 1
            assert source["title"] == "旧合集"
            assert source["container_url"] == url
            assert result["result"]["pipeline_skipped"] is True
        pipeline.assert_not_called()
    client.get_article_detail.assert_not_called()
    assert SOURCE_LABELS[source_id] in result["log"]
    assert source_id not in result["log"]
    assert SOURCE_LABELS[source_id] in _build_label("refresh_source", {"source_id": source_id})
    assert any(SOURCE_LABELS[source_id] in event["message"] for event in events)

    client.get_latest_article.side_effect = RuntimeError("网络错误")
    with pytest.raises(RuntimeError, match="网络错误"):
        run_action(action, {"source_id": source_id})
    source = next(item for item in get_summary()["sources"] if item["id"] == source_id)
    assert source["checked_at"] == 400
