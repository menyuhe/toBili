"""Full local cycle with fake platform endpoints; networking is forbidden."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import httpx
import pytest
from src.account_context import account_scope, scoped_uid
from src.accounts import write_state
from src import nonstop_participation as nonstop
from src import maintenance, message_review, participation_pool
from web.schedule_config import ScheduleConfig, save
from web.auto_scheduler import AutoScheduler
from web.account_jobs import GLOBAL_ACTIONS
from web import actions
from tests.test_maintenance import FakeApi, seed, NOW
from tests.test_message_review import dm

@pytest.fixture(autouse=True)
def sandbox(isolated_home, monkeypatch):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("NETWORK FORBIDDEN"))
    monkeypatch.setattr(httpx.AsyncClient, "send", lambda *a, **k: pytest.fail("NETWORK FORBIDDEN"))


def test_complete_two_account_nonstop_cycle(isolated_home, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(nonstop.time, "time", lambda: clock[0])
    monkeypatch.setattr(maintenance, "safe_enqueue", lambda *a, **k: None)
    monkeypatch.setattr("src.bilibili_auth.get_login_uid", scoped_uid)
    monkeypatch.setattr("web.account_service.get_account_profile", lambda: {"logged_in": True, "mid": int(scoped_uid())})
    monkeypatch.setattr("web.account_service.get_account_extras", lambda: {})
    # A and B own separate old, verified non-winning activities eligible for cleanup.
    seed(uid="101", did="10"); seed(uid="202", did="11")
    apis = {uid: FakeApi() for uid in ("101", "202")}
    for uid, api in apis.items():
        original = api.dynamic
        def dynamic(did, original=original, uid=uid):
            value = original(did)
            if value: value["modules"]["module_author"]["mid"] = uid
            return value
        api.dynamic = dynamic
    apis["101"].client.messages = [dm(1)]
    apis["101"].client.unread = 1
    original_maintenance = maintenance.run_maintenance
    monkeypatch.setattr(maintenance, "run_maintenance", lambda uid, **kw: original_maintenance(uid, api=apis[uid], **kw))
    from src.activity_store import append_activities
    from src.activity_archive import archive_activity_ids
    from src.participation_store import set_participation, load_participations
    from src.db.session import session_scope
    from src.db.models import ParticipationLinkRow
    ids = [str(123456789012345670+i) for i in range(4)]
    append_activities([{"dynamic_id": did, "lottery_type": "转发抽奖", "activity_status": "未参加", "draw_status": "active", "lottery_time": NOW+86400, "sender_uid": "999", "activity_title": "simulation"} for did in ids])
    for uid, limit in (("101", 2), ("202", 3)):
        with account_scope(uid):
            save(ScheduleConfig(nonstop=True, nonstop_hourly_limit=limit, nonstop_minutes=5, maintenance_minutes=5))
            archive_activity_ids([ids[3] if uid == "101" else ids[0]])
    # A's manually paused selection pool must not constrain activity-list mode.
    participation_pool.edit("101", "pause")
    calls = Counter(); joined = {"101": [], "202": []}
    original_action = actions._run_action
    def dispatch(action, params=None, **kwargs):
        uid = scoped_uid()
        calls[(uid, action)] += 1
        if action in GLOBAL_ACTIONS or action == "refresh_status":
            return {"ok": True, "message": "mock refresh"}
        if action == "participate":
            did = params["dynamic_id"]
            joined[uid].append(did)
            set_participation(did, "已参加")
            with session_scope() as s:
                s.add(ParticipationLinkRow(uid=uid, dynamic_id=did, up_uid="999", repost_recorded=False))
            return {"ok": True}
        return original_action(action, params, **kwargs)
    monkeypatch.setattr(actions, "_run_action", dispatch)
    class ImmediateRunner:
        def is_running(self): return False
        def get_status(self): return SimpleNamespace(to_dict=lambda: {"state":"idle"})
        def try_start(self, action, params, source):
            result = actions.run_action(action, params)
            self.final = {"state": "success" if result.get("ok") else "error", "result": result.get("result", {}), "message": result.get("message", "")}
            return 1
    schedulers = {}
    for uid in ("101", "202"):
        with account_scope(uid):
            runner = ImmediateRunner(); scheduler = AutoScheduler(runner)
            monkeypatch.setattr(scheduler, "_wait_until_terminal", lambda *a, runner=runner: runner.final)
            monkeypatch.setattr(scheduler, "_schedule_auto_snapshot", lambda **kw: None)
            schedulers[uid] = scheduler
            scheduler._tick_periodic(NOW)
    def tick(uid):
        with account_scope(uid): schedulers[uid]._tick_periodic(clock[0])
    clock[0] += 301
    # Both accounts progress concurrently through source update, status, participation, maintenance.
    with ThreadPoolExecutor(max_workers=2) as executor:
        for _ in range(8): list(executor.map(tick, ("101", "202")))
    for action in GLOBAL_ACTIONS.intersection({"refresh_all", "refresh_watch", "refresh_discovery"}):
        assert sum(n for (u,a),n in calls.items() if a == action) == 1
    assert len(joined["101"]) == 2 and len(joined["202"]) == 3
    assert ids[3] not in joined["101"] and ids[0] not in joined["202"]
    assert apis["101"].deleted == [] and apis["101"].unfollowed == []
    assert apis["202"].deleted == ["811"] and apis["202"].unfollowed == ["900"]
    assert message_review.review_status("101")["pending"] == 1
    assert not message_review.review_status("202")["pending"]
    assert participation_pool.snapshot("101")["paused"]
    assert participation_pool.snapshot("202")["items"] == []
    # Restarting a scheduler doesn't reset the rolling-hour allowance.
    with account_scope("101"):
        result = actions.run_action("participate_all", {"uid":"101"})
        assert result["result"]["attempted"] == 0
    message_review.resolve_message("101", "dm:9:1", "irrelevant")
    with account_scope("101"):
        original_maintenance("101", api=apis["101"], clean=True, manual_clean=False)
    assert not apis["101"].deleted and not apis["101"].unfollowed
    clock[0] += 3601
    with account_scope("101"):
        result = actions.run_action("participate_all", {"uid":"101"})
        assert result["result"]["joined"] == 1
    assert len(joined["101"]) == 3
    assert len(joined["202"]) == 3


def test_quota_failure_restart_and_boundary(monkeypatch):
    clock=[10000.0]; monkeypatch.setattr(nonstop.time,"time",lambda:clock[0])
    with account_scope("101"):
        save(ScheduleConfig(nonstop=True,nonstop_hourly_limit=1))
        def fail(*a): raise TimeoutError("simulated")
        with pytest.raises(TimeoutError):
            nonstop.run("101", candidates=lambda:{"10":{"can_participate":True}},participate=fail,progress=lambda **k:None)
        assert nonstop.status("101")["paused"] and nonstop.status("101")["used"] == 1
        nonstop.resume("101")
        assert not nonstop.reserve("101",1)
        assert nonstop.reserve("202",1)
        clock[0]+=3599
        assert not nonstop.reserve("101",1)
        clock[0]+=1
        assert nonstop.reserve("101",1)


def test_parallel_reservation_never_exceeds_limit():
    with ThreadPoolExecutor(max_workers=8) as executor:
        results=list(executor.map(lambda _:nonstop.reserve("101",3),range(20)))
    assert sum(results)==3
    assert nonstop.status("101")["used"]==3
