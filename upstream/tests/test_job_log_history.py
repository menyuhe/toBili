import time

import pytest

from src.job_store import get_job, insert_running_job, finish_job, list_recent_jobs, prune_old_jobs
from web.job_runner import JobRunner, JobStatus


@pytest.mark.parametrize("outcome", ["success", "error", "exception"])
def test_terminal_keeps_progress_and_summary(isolated_home, monkeypatch, outcome):
    job_id = insert_running_job(action="refresh_discovery", label="B站话题", source="ui", params={})
    runner = JobRunner()
    runner._status = JobStatus(id=job_id, action="refresh_discovery", state="running")
    def action(*args, on_progress, **kwargs):
        on_progress(step=0, total=2, message="读取", log_append="第一步：发现 20 条")
        on_progress(step=1, total=2, message="分类", log_append="第二步：识别 3 条")
        if outcome == "exception":
            raise RuntimeError("网络断开")
        return {"ok": outcome == "success", "message": "汇总", "log": "最终结果：3 条"}
    monkeypatch.setattr("web.job_runner.run_action", action)
    runner._run_worker(job_id, "refresh_discovery", {}, None)
    saved = get_job(job_id)
    assert "第一步：发现 20 条" in saved["log_summary"]
    assert "第二步：识别 3 条" in saved["log_summary"]
    assert ("网络断开" if outcome == "exception" else "最终结果：3 条") in saved["log_summary"]
    assert runner.get_status().log == saved["log_summary"]


def test_history_paginates_and_retains_8_day_old_job(isolated_home):
    ids = [insert_running_job(action="test", label="test", source="ui", params={}) for _ in range(4)]
    finish_job(ids[0], state="success", message="done", log_summary="保留过程",
               finished_at=int(time.time()) - 8 * 86400)
    assert prune_old_jobs() == 0
    assert [r["id"] for r in list_recent_jobs(2)] == ids[-2:][::-1]
    assert [r["id"] for r in list_recent_jobs(2, before_id=ids[2])] == ids[:2][::-1]


def test_scheduler_refreshes_new_source_first(monkeypatch):
    from web.auto_scheduler import AutoScheduler
    scheduler = AutoScheduler()
    actions = []
    monkeypatch.setattr(scheduler, "_click_and_wait", lambda action, **kwargs: actions.append(action))
    monkeypatch.setattr(scheduler, "_schedule_auto_snapshot", lambda **kwargs: None)
    scheduler._run_refresh_batch("test")
    assert actions == ["refresh_discovery", "refresh_all", "refresh_watch", "refresh_status"]
