"""Simulated platform only: never reads a real cookie or performs network I/O."""
from __future__ import annotations

import json
from types import SimpleNamespace
import httpx
import pytest
from sqlmodel import select

from src import maintenance as m
from src.cleanup_api import CleanupApi, MaintenanceBlocked
from src.db.models import ActivityRow, ParticipationRow, ParticipationLinkRow, DrawOutcomeRow, CleanupStepRow
from src.db.session import session_scope
from src.task_control import TaskCancelled

NOW = 2_000_000_000
UID = "123"


@pytest.fixture(autouse=True)
def sandbox(isolated_home, monkeypatch):
    monkeypatch.setattr(m.time, "time", lambda: NOW)
    monkeypatch.setattr(m, "safe_enqueue", lambda *a, **k: None)
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("Unexpected network request"))


def notice(winner="456", count=1):
    return {"status": 2, "first_prize": count, "second_prize": 0, "third_prize": 0,
            "lottery_result": {"first_prize_result": [{"uid": winner}]}}


def seed(did="10", up="900", origin="new", followed=True, lost=True, age=86401, uid=UID):
    with session_scope() as s:
        if not s.get(ActivityRow, did):
            s.add(ActivityRow(dynamic_id=did, activity_status="已结束", lottery_type="互动抽奖",
                payload_json=json.dumps({"sender_uid": up, "lottery_time": NOW - 100})))
        s.add(ParticipationRow(uid=uid, dynamic_id=did, user_status="已参加"))
        s.add(ParticipationLinkRow(uid=uid, dynamic_id=did, up_uid=up, repost_id="8"+did,
            repost_recorded=True, origin=origin, follow_added=followed, followed_before=not followed))
        s.add(DrawOutcomeRow(uid=uid, dynamic_id=did, outcome="lost" if lost else "unknown",
            source="official" if lost else "none", lost_at=NOW-age if lost else 0))
    m.save_settings(uid, {"enabled": True, "delete_reposts": True, "unfollow": True})


class FakeApi:
    def __init__(self):
        from tests.test_message_review import Messages
        self.client = Messages()
        self.results = {}
        self.deleted, self.unfollowed = [], []
        self.existing = {"810": "10", "811": "11"}
        self.follows = {"900", "901"}
        self.delete_error = False
        self.timeout_after_delete = False
        self.unfollow_error = False
        self.calls = 0

    def verify_login(self): pass
    def notice(self, item):
        self.calls += 1
        v = self.results.get(item["dynamic_id"], notice())
        if isinstance(v, Exception): raise v
        return v(self.calls) if callable(v) else v
    def dynamic(self, did):
        if did not in self.existing: return None
        return {"type": "DYNAMIC_TYPE_FORWARD", "id_str": did,
                "modules": {"module_author": {"mid": UID}}, "orig": {"id_str": self.existing[did]}}
    def following(self, up): return up in self.follows
    def delete_repost(self, did):
        if self.delete_error: raise RuntimeError("simulated rejection")
        self.deleted.append(did)
        del self.existing[did]
        if self.timeout_after_delete: raise TimeoutError()
    def unfollow(self, up):
        if self.unfollow_error: raise TimeoutError()
        self.unfollowed.append(up)
        self.follows.discard(up)


@pytest.mark.parametrize("result,expected", [
    (None,"unknown"), ({"status": 0},"unknown"),
    ({"status": 0,"lottery_time": NOW+20},"pending"),
    (notice(UID, 100),"won"), (notice(),"lost"), (notice(count=2),"unknown"),
    ({"status":2,"lottery_result":{}},"unknown"),
    ({**notice(),"second_prize":1},"unknown"),
    ({**notice(),"lottery_result":{"first_prize_result":[{"uid":"456"}],"extra":[]}},"unknown"),
    ({**notice(),"first_prize":0,"lottery_result":{}},"unknown"),
])
def test_results_require_complete_evidence(result, expected):
    assert m.evaluate_notice(result, UID, now=NOW)[0] == expected


def test_expired_participated_activity_checked_and_cleanup_once():
    seed()
    api = FakeApi()
    result = m.run_maintenance(UID, clean=True, api=api)
    assert result["result"] == {"checked":1,"deleted":1,"unfollowed":1}
    m.run_maintenance(UID, clean=True, api=api)
    assert api.deleted == ["810"] and api.unfollowed == ["900"]
    assert m.activities_for_uid(UID)["10"]  # local record preserved


@pytest.mark.parametrize("age", [0, 86399])
def test_grace_period(age):
    seed(age=age)
    api = FakeApi()
    m.run_maintenance(UID, clean=True, api=api)
    assert not api.deleted and not api.unfollowed


def test_result_change_on_final_check_retains_repost():
    seed()
    api = FakeApi()
    api.results["10"] = lambda calls: notice() if calls == 1 else notice(UID)
    m.run_maintenance(UID, clean=True, api=api)
    assert not api.deleted and m.get_outcome(UID,"10")["outcome"] == "won"


@pytest.mark.parametrize("variant", ["historical", "keep", "disabled", "unknown", "error", "ownership"])
def test_cleanup_guard(variant):
    seed(origin="historical" if variant == "historical" else "new")
    api = FakeApi()
    if variant == "keep": m.mark_keep(UID,"10",True)
    if variant == "disabled": m.save_settings(UID,{"enabled":False})
    if variant == "unknown": api.results["10"] = None
    if variant == "error": api.results["10"] = RuntimeError("offline")
    if variant == "ownership": api.existing["810"] = "999"
    m.run_maintenance(UID, clean=True, api=api)
    assert not api.deleted and not api.unfollowed


@pytest.mark.parametrize("variant", ["original", "protected", "other_won", "other_unknown", "other_future", "unknown_up"])
def test_unfollow_guards(variant):
    seed(followed=variant != "original")
    api = FakeApi()
    if variant == "protected": m.save_settings(UID, {"protected_ups":["900"]})
    if variant.startswith("other") or variant == "unknown_up":
        seed("11", up="" if variant == "unknown_up" else "900")
        api.results["11"] = {"other_won":notice(UID), "other_unknown":None,
            "other_future":{"status":0,"lottery_time":NOW+100}}.get(variant, notice())
    m.run_maintenance(UID, clean=True, api=api)
    assert not api.unfollowed


def test_two_same_up_deletes_then_one_unfollow():
    seed(); seed("11")
    api = FakeApi()
    m.run_maintenance(UID, clean=True, api=api)
    assert set(api.deleted) == {"810","811"} and api.unfollowed == ["900"]


def test_delete_timeout_state_probe_prevents_duplicate():
    seed()
    api = FakeApi(); api.timeout_after_delete = True
    m.run_maintenance(UID, clean=True, api=api)
    assert m.state_for(UID,"delete","810") == "unknown" and not api.unfollowed
    m.run_maintenance(UID, clean=True, api=api)
    assert api.deleted == ["810"] and api.unfollowed == ["900"]


def test_delete_fail_blocks_unfollow_and_unfollow_failure_retries_separately():
    seed()
    api = FakeApi(); api.delete_error = True
    m.run_maintenance(UID, clean=True, api=api)
    assert not api.unfollowed
    api.delete_error = False; api.unfollow_error = True
    m.run_maintenance(UID, clean=True, api=api)
    assert api.deleted == ["810"] and m.state_for(UID,"unfollow","900") == "unknown"
    api.unfollow_error = False
    m.run_maintenance(UID, clean=True, api=api)
    assert api.deleted == ["810"] and api.unfollowed == ["900"]


@pytest.mark.parametrize("exc", [MaintenanceBlocked("rate limit"), TaskCancelled("cancelled")])
def test_cancel_and_risk_stop_batch(exc):
    seed(); seed("11")
    api = FakeApi(); api.results["10"] = exc; api.results["11"] = exc
    with pytest.raises(type(exc)): m.run_maintenance(UID, clean=True, api=api)
    assert not api.deleted and not api.unfollowed


def test_accounts_and_defaults_are_isolated():
    seed()
    assert m.activities_for_uid("124") == {}
    assert not m.settings("124")["enabled"]
    with pytest.raises(ValueError): m.mark_outcome("124","10","lost")


def test_manual_regular_draw_rechecked_but_network_failure_retains():
    seed(lost=False)
    api = FakeApi(); api.results["10"] = None
    m.mark_outcome(UID,"10","lost")
    m.check_outcome(api,UID,"10",m.activities_for_uid(UID)["10"])
    assert m.get_outcome(UID,"10")["source"] == "manual"
    assert m.get_outcome(UID,"10")["lost_at"] == NOW
    api.results["10"] = RuntimeError("offline")
    m.check_outcome(api,UID,"10",m.activities_for_uid(UID)["10"])
    assert m.get_outcome(UID,"10")["outcome"] == "unknown"


def test_original_follow_and_returned_repost_recorded_immediately(monkeypatch):
    monkeypatch.setattr("src.bilibili_auth.require_login",lambda:("csrf",123))
    monkeypatch.setattr(m,"CleanupApi",lambda *a: SimpleNamespace(following=lambda up:False))
    with m.track_participation(True):
        uid = m.begin_link(None,"10",900)
        m.record_follow(uid,"10",SimpleNamespace(ok=True,metadata={"created":True}))
        m.record_repost(None,uid,"10",SimpleNamespace(ok=True,metadata={"repost_id":"810"}))
    link = m.link_rows(UID)["10"]
    assert link["follow_added"] and link["repost_recorded"] and link["repost_id"] == "810"
    assert "10" in m.activities_for_uid(UID)  # survives later participation failure


def test_missing_repost_id_only_suggested(monkeypatch):
    seed()
    with session_scope() as s:
        link=s.get(ParticipationLinkRow,(UID,"10")); link.repost_id=""; link.repost_recorded=False
    monkeypatch.setattr(m,"CleanupApi",lambda *a:SimpleNamespace(find_reposts=lambda did:["810"]))
    m.record_repost(None,UID,"10",SimpleNamespace(ok=True,metadata=None))
    assert m.link_rows(UID)["10"]["repost_id"] == "810"
    assert not m.link_rows(UID)["10"]["repost_recorded"]


def test_adapter_no_write_retries_and_http_rate_limit(monkeypatch):
    monkeypatch.setattr("src.cleanup_api.require_login",lambda:("csrf",123))
    monkeypatch.setattr("src.cleanup_api.get_login_uid",lambda:123)
    calls=[]
    client=SimpleNamespace(post_form=lambda *a,**k:(calls.append(k) or {"code":0}))
    api=CleanupApi(client,UID)
    api.delete_repost("810"); api.unfollow("900")
    assert all(c["retries"] == 0 for c in calls)
    def blocked(*a,**k):
        resp=httpx.Response(429,request=httpx.Request("GET","https://example.invalid"))
        resp.raise_for_status()
    client.request_json=blocked
    with pytest.raises(MaintenanceBlocked): api.verify_login()
    monkeypatch.setattr("src.cleanup_api.get_login_uid",lambda:124)
    with pytest.raises(MaintenanceBlocked): api.delete_repost("810")
    assert len(calls)==2


def test_historical_link_requires_correct_author_and_forward():
    seed(origin="historical")
    api=FakeApi()
    with pytest.raises(ValueError): m.confirm_link(api,UID,"10","900","810",True)
    original=api.dynamic
    api.dynamic=lambda did: {"modules":{"module_author":{"mid":900}}} if did=="10" else original(did)
    m.confirm_link(api,UID,"10","900","810",False)
    assert m.link_rows(UID)["10"]["origin"] == "confirmed"
    assert not m.link_rows(UID)["10"]["follow_added"]


def test_worker_defers_busy_runner_and_restarts_after_interval(monkeypatch):
    from web.maintenance_worker import MaintenanceWorker
    monkeypatch.setattr("web.maintenance_worker.get_login_uid",lambda:UID)
    monkeypatch.setattr("web.auto_scheduler.auto_scheduler.get_status",lambda:{})
    starts=[]
    runner=SimpleNamespace(is_running=lambda:True,try_start=lambda *a,**k:starts.append(a) or 1)
    w=MaintenanceWorker()
    assert not w.tick(runner,NOW) and not starts
    runner.is_running=lambda:False
    assert w.tick(runner,NOW)
    assert not w.tick(runner,NOW+1799)
    assert w.tick(runner,NOW+1800)


def test_upgrade_backs_up_old_database(isolated_home):
    import sqlite3
    from src.db.schema import init_db
    from src.db.engine import db_path
    with sqlite3.connect(db_path()) as conn:
        conn.execute("UPDATE schema_meta SET version=2")
    init_db()
    backups=list(db_path().parent.glob("binggo.pre-v7.*.db"))
    assert len(backups)==1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("SELECT version FROM schema_meta").fetchone()[0]==2
    assert not m.settings(UID)["enabled"]


def test_discover_unique_repost_is_only_a_candidate():
    seed(origin="historical",followed=False,lost=False)
    with session_scope() as s:
        row=s.get(ParticipationLinkRow,(UID,"10"));row.repost_recorded=False;row.repost_id=""
    api=FakeApi()
    api.assert_account=lambda:None
    api.repost_page=lambda offset:{"items":[{"id_str":"810","type":"DYNAMIC_TYPE_FORWARD","modules":{"module_author":{"mid":UID}},"orig":{"id_str":"10","modules":{"module_author":{"mid":"900"}}}}],"has_more":False,"offset":""}
    result=m.discover_associations(api,UID)
    row=m.link_rows(UID)["10"]
    assert result["result"]["matched"]==1
    assert row["repost_id"]=="810" and row["origin"]=="historical"
    assert not row["repost_recorded"] and not row["follow_added"]


def test_cancel_after_remote_delete_recovers_without_second_delete():
    from web.maintenance_worker import recover_cleanup_steps
    seed()
    api=FakeApi()
    delete=api.delete_repost
    def interrupted(did):
        delete(did)
        raise TaskCancelled("simulated shutdown after remote success")
    api.delete_repost=interrupted
    with pytest.raises(TaskCancelled): m.run_maintenance(UID,clean=True,api=api)
    assert m.state_for(UID,"delete","810")=="running"
    recover_cleanup_steps()
    assert m.state_for(UID,"delete","810")=="unknown"
    m.run_maintenance(UID,clean=True,api=api)
    assert api.deleted==["810"] and api.unfollowed==["900"]


def test_scheduler_waits_for_maintenance_without_collision(monkeypatch):
    from unittest.mock import MagicMock
    from web.auto_scheduler import AutoScheduler
    runner=MagicMock()
    runner.is_running.side_effect=[True,False,False]
    runner.get_status.return_value.action="maintenance_cycle"
    runner.try_start.return_value=42
    scheduler=AutoScheduler(job_runner=runner)
    monkeypatch.setattr(scheduler._stop_event,"wait",lambda duration:False)
    monkeypatch.setattr(scheduler,"_schedule_auto_snapshot",lambda **kwargs:None)
    monkeypatch.setattr(scheduler,"_wait_until_terminal",lambda *a:{"state":"success","message":"done"})
    assert not scheduler._click_and_wait("refresh_status")["skipped"]
    runner.try_start.assert_called_once()
    runner.cancel.assert_not_called()


def test_message_rate_limit_before_delete_reports_reason_and_preserves_repost(monkeypatch):
    from src import message_review
    seed()
    api = FakeApi()
    clock = iter([0, 301, 301])
    monkeypatch.setattr(m.time, "monotonic", lambda: next(clock))
    states = iter([{"blocked": False}, {"blocked": True, "error": "请求过于频繁，请稍后再试"}])
    monkeypatch.setattr(message_review, "check_messages", lambda *args: next(states))
    monkeypatch.setattr(message_review, "review_status", lambda *args: {"blocked": False})
    with pytest.raises(MaintenanceBlocked, match="请求过于频繁"):
        m.run_maintenance(UID, clean=True, manual_clean=False, api=api)
    assert api.deleted == []
    assert api.unfollowed == []


def test_cleanup_checks_remote_messages_once_for_multiple_deletes_and_unfollows(monkeypatch):
    from src import message_review
    seed(); seed("11", up="901")
    api = FakeApi()
    original = message_review.check_messages
    calls = []
    def check(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(message_review, "check_messages", check)
    m.run_maintenance(UID, clean=True, api=api)
    assert len(calls) == 1
    assert api.deleted == ["810", "811"]
    assert api.unfollowed == ["900", "901"]


def test_oldest_batch_then_next_round_skips_completed(monkeypatch):
    seed(); seed("11", up="901")
    with session_scope() as session:
        session.get(ActivityRow, "10").lottery_time = NOW - 100
        session.get(ActivityRow, "11").lottery_time = NOW - 200
    monkeypatch.setattr(m, "CLEANUP_BATCH_LIMIT", 1)
    api = FakeApi()
    m.run_maintenance(UID, clean=True, api=api)
    assert api.deleted == ["811"]
    assert api.unfollowed == ["901"]
    m.run_maintenance(UID, clean=True, api=api)
    assert api.deleted == ["811", "810"]
    assert api.unfollowed == ["901", "900"]


def test_new_local_message_blocks_remaining_batch(monkeypatch):
    from src import accounts
    seed(); seed("11", up="901")
    api = FakeApi()
    original = api.delete_repost
    def delete(did):
        original(did)
        accounts.write_state(UID, "message_review", {"complete": True, "cleanup_hold": True})
    monkeypatch.setattr(api, "delete_repost", delete)
    with pytest.raises(MaintenanceBlocked):
        m.run_maintenance(UID, clean=True, api=api)
    assert api.deleted == ["810"]
    assert api.unfollowed == []
