from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.bilibili_client import BilibiliClient
from src.job_store import get_job
from src.pipeline.classify_step import ClassifyOutcome
from src.pipeline.refresh_all_pipeline import run_new_links_pipeline, run_refresh_all_pipeline
from src.sources.common import CheckResult, latest_activity_links, normalize_activity_id
from src.task_control import TaskCancelled, cancellable_sleep, check_cancelled, task_cancellation
from web.actions import run_action
from web.job_runner import JobRunner


def links(count=320):
    return [f"https://www.bilibili.com/opus/{1220000000000000000 + n}" for n in range(count)]


def source_result(source_id="DS-2"):
    urls = links()
    return CheckResult(source_id, True, "https://www.bilibili.com/read/cv1", "1", "合集", 1, None,
                       urls + urls[-5:], 2,
                       {normalize_activity_id(url): "互动抽奖" for url in urls})


def wait_done(runner):
    deadline = time.monotonic() + 5
    while runner.is_running() and time.monotonic() < deadline:
        time.sleep(.01)
    assert not runner.is_running()
    return runner.get_status()


def test_latest_twenty_deduplicates_sorts_and_prunes_hints():
    result = source_result()
    expected = list(reversed(links()[-20:]))
    assert result.activity_links == expected
    assert set(result.link_hints) == {normalize_activity_id(url) for url in expected}
    assert latest_activity_links(links(3) + ["bad", links(3)[-1]]) == list(reversed(links(3)))


def test_twenty_limit_is_applied_before_classification(isolated_home):
    seen = []

    def classify(client, dynamic_id):
        seen.append(dynamic_id)
        return ClassifyOutcome(dynamic_id, None, True, "非抽奖活动")

    with patch("src.pipeline.refresh_all_pipeline.BilibiliClient"), patch(
        "src.pipeline.refresh_all_pipeline.classify_new_link", side_effect=classify
    ):
        result = run_refresh_all_pipeline([source_result("DS-2"), source_result("DS-3")])
    assert result.raw_link_count == 40
    assert len(seen) == 20  # Cross-source dedupe still applies.
    assert seen == [normalize_activity_id(url) for url in reversed(links()[-20:])]


def test_cancel_during_classification_stops_next_link_and_does_not_persist(isolated_home):
    event = threading.Event()

    def classify(client, dynamic_id):
        event.set()
        return ClassifyOutcome(dynamic_id, "互动抽奖", False)

    with patch("src.pipeline.refresh_all_pipeline.BilibiliClient"), patch(
        "src.pipeline.refresh_all_pipeline.classify_new_link", side_effect=classify
    ) as classify_mock, patch("src.pipeline.refresh_all_pipeline.append_activities") as persist:
        with task_cancellation(event), pytest.raises(TaskCancelled):
            run_new_links_pipeline(links())
    assert classify_mock.call_count == 1
    persist.assert_not_called()


def test_cancel_propagates_to_queued_enrichment_workers(isolated_home):
    event = threading.Event()
    calls = []

    def classify(client, dynamic_id):
        return ClassifyOutcome(dynamic_id, "互动抽奖", False)

    def enrich(*args, **kwargs):
        calls.append(kwargs["dynamic_id"])
        event.set()
        check_cancelled()

    with patch("src.pipeline.refresh_all_pipeline.BilibiliClient"), patch(
        "src.pipeline.refresh_all_pipeline.classify_new_link", side_effect=classify
    ), patch("src.pipeline.refresh_all_pipeline.enrich_activity", side_effect=enrich), patch(
        "src.pipeline.refresh_all_pipeline.append_activities"
    ) as persist:
        with task_cancellation(event), pytest.raises(TaskCancelled):
            run_new_links_pipeline(links(20), workers=1)
    assert len(calls) == 1
    persist.assert_not_called()


def test_cancel_prevents_http_get_and_post():
    event = threading.Event()
    with BilibiliClient(warmup=False) as client, task_cancellation(event):
        with patch.object(client._client, "get") as get, patch.object(client._client, "post") as post:
            event.set()
            with pytest.raises(TaskCancelled):
                client._http_get("https://example.invalid")
            with pytest.raises(TaskCancelled):
                client._http_post("https://example.invalid")
            get.assert_not_called()
            post.assert_not_called()


def test_cancel_interrupts_retry_wait_without_retrying():
    from src.retry_utils import retry_call
    event = threading.Event()
    began = threading.Event()
    finished = threading.Event()
    calls = []

    def failing():
        calls.append(1)
        began.set()
        raise RuntimeError("network error")

    def worker():
        with task_cancellation(event), pytest.raises(TaskCancelled):
            retry_call(failing, base_delay=60)
        finished.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert began.wait(2)
    event.set()
    assert finished.wait(2)
    thread.join(2)
    assert len(calls) == 1
    check_cancelled()  # Worker cancellation never leaks to unrelated work.


def test_runner_keeps_slot_until_exit_and_accepts_next_job(isolated_home):
    runner = JobRunner()
    began = threading.Event()
    release = threading.Event()

    def action(*args, on_progress, cancel_event, **kwargs):
        on_progress(step=1, total=20, message="提取中", log_append="第一条已完成")
        began.set()
        assert release.wait(3)
        on_progress(step=2, total=20, message="不应覆盖取消提示")
        with task_cancellation(cancel_event):
            return {"ok": True}

    with patch("web.job_runner.run_action", side_effect=action):
        job_id = runner.try_start("refresh_source", {"source_id": "DS-2"})
        assert began.wait(2)
        assert not runner.cancel(job_id + 1)
        assert runner.cancel(job_id)
        assert runner.cancel(job_id)  # Repeated stop is idempotent.
        assert runner.get_status().to_dict()["cancel_requested"]
        assert runner.try_start("refresh_all") is None
        release.set()
        status = wait_done(runner)
    assert status.state == "cancelled"
    assert status.progress_step == 1
    assert "第一条已完成" in status.log
    assert get_job(job_id)["state"] == "cancelled"
    assert not runner.cancel(job_id)
    with patch("web.job_runner.run_action", return_value={"ok": True, "message": "完成"}):
        next_id = runner.try_start("refresh_status")
        assert next_id != job_id
        assert wait_done(runner).state == "success"
        assert not runner.get_status().cancel_requested


def test_cancelled_source_check_never_commits_checkpoint(isolated_home):
    from src.state_store import get_last_container
    event = threading.Event()
    result = source_result()

    def check(**kwargs):
        event.set()
        return result

    save = MagicMock()
    with patch("src.source_registry.handlers", return_value=[({"id":"DS-2", "backup":False}, check, save)]):
        with pytest.raises(TaskCancelled):
            run_action("refresh_source", {"source_id": "DS-2"}, cancel_event=event)
    save.assert_not_called()
    assert get_last_container("DS-2") is None


def test_cancel_api_scopes_request_to_job_id(isolated_home):
    from web.app import app
    with TestClient(app) as client, patch("web.app.runner.cancel", return_value=True) as cancel:
        response = client.post("/api/jobs/cancel?job_id=12")
        assert response.status_code == 200
        cancel.assert_called_once_with(12)
    with TestClient(app) as client, patch("web.app.runner.cancel", return_value=False):
        assert client.post("/api/jobs/cancel?job_id=11").status_code != 200
