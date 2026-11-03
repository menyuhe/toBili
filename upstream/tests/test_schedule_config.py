from unittest.mock import MagicMock
import pytest
from pydantic import ValidationError
from src.account_context import account_scope
from web.schedule_config import ScheduleConfig, Period, load, save, plan
from web.auto_scheduler import AutoScheduler


def only(action, minutes=5):
    cfg = ScheduleConfig()
    for key in ("refresh_discovery", "refresh_all", "refresh_watch", "refresh_status", "participate_pool"):
        setattr(cfg, key, Period(enabled=key == action, minutes=minutes))
    return cfg


def test_account_settings_are_local_and_isolated(isolated_home):
    with account_scope("101"):
        save(only("refresh_status", 17))
        assert load().refresh_status.minutes == 17
    with account_scope("202"):
        assert load().refresh_status.minutes == 180
        assert not load().nonstop
    with pytest.raises(ValidationError):
        Period(minutes=0)
    with pytest.raises(ValidationError):
        ScheduleConfig(nonstop_minutes=10081)


def scheduler(uid, monkeypatch):
    runner = MagicMock()
    runner.is_running.return_value = False
    with account_scope(uid):
        result = AutoScheduler(runner)
    monkeypatch.setattr(result, "_click_and_wait", MagicMock(return_value={}))
    monkeypatch.setattr(result, "_set_phase", MagicMock())
    return result


def test_switches_periods_and_busy_deferral(isolated_home, monkeypatch):
    with account_scope("101"):
        save(only("refresh_status"))
    s = scheduler("101", monkeypatch)
    s._tick_periodic(1000)
    s._tick_periodic(1299)
    s._click_and_wait.assert_not_called()
    s._runner.is_running.return_value = True
    s._tick_periodic(1300)
    s._click_and_wait.assert_not_called()
    s._runner.is_running.return_value = False
    s._tick_periodic(1301)
    s._click_and_wait.assert_called_once_with("refresh_status")
    s._tick_periodic(1302)
    assert s._click_and_wait.call_count == 1


def test_shared_sources_only_run_once_and_local_status_not_shared(isolated_home, monkeypatch):
    for uid in ("101", "202"):
        with account_scope(uid): save(only("refresh_all"))
    a = scheduler("101", monkeypatch); b = scheduler("202", monkeypatch)
    a._tick_periodic(1000); b._tick_periodic(1000)
    a._tick_periodic(1300); b._tick_periodic(1300)
    a._click_and_wait.assert_called_once_with("refresh_all")
    b._click_and_wait.assert_not_called()
    for uid in ("101", "202"):
        with account_scope(uid): save(only("refresh_status"))
    a._tick_periodic(1400); b._tick_periodic(1400)
    a._tick_periodic(1700); b._tick_periodic(1700)
    b._click_and_wait.assert_called_once_with("refresh_status")


def test_config_change_resets_deadline_and_all_off(isolated_home, monkeypatch):
    with account_scope("101"): save(only("refresh_status"))
    s = scheduler("101", monkeypatch); s._tick_periodic(1000)
    with account_scope("101"): save(only("refresh_status", 10))
    s._tick_periodic(1290); s._tick_periodic(1300)
    s._click_and_wait.assert_not_called()
    with account_scope("101"): save(only("none"))
    s._tick_periodic(2000)
    assert not s._due


def test_nonstop_enables_work_but_does_not_override_cleanup_protections():
    cfg = only("none"); cfg.nonstop = True
    actions = plan(cfg)
    assert set(actions) == {"refresh_all", "refresh_discovery", "refresh_watch", "refresh_status", "participate_all", "maintenance_cycle"}
    assert actions["maintenance_cycle"] == 1800
    assert "maintenance_clean" not in actions


def test_queued_nonstop_maintenance_is_skipped_after_disable(isolated_home, monkeypatch):
    from web.actions import _run_action
    monkeypatch.setattr("src.bilibili_auth.get_login_uid", lambda: "101")
    with account_scope("101"):
        save(only("none"))
        result = _run_action("maintenance_cycle", {"uid": "101", "scheduled_nonstop": True})
    assert result["result"]["skipped"]

