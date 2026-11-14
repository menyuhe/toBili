import threading

import pytest
from test_taskbook_api import setup as setup

from tobili.domain.errors import Fault, MissingCredential, RemoteError
from tobili.ports.models import AccountProfile


@pytest.mark.parametrize(
    "failure,state",
    [
        (MissingCredential("expired"), "login_required"),
        (RemoteError(Fault.AUTH, "expired", code=-101), "login_required"),
        (RemoteError(Fault.TIMEOUT, "timeout"), "unavailable"),
        (RemoteError(Fault.RISK, "412"), "unavailable"),
        (OSError("keychain"), "unavailable"),
    ],
)
def test_login_failure_classification_preserves_profile_and_plan(setup, failure, state):
    client, box = setup
    box.repos.settings.put(1, "tasks.config", '{"running":true,"tasks":[]}')
    box.account_health.read_profile = lambda _: (_ for _ in ()).throw(failure)
    box.account_health.check(1)
    row = client.get("/api/accounts").json()["items"][0]
    assert row["session"]["state"] == state
    assert box.repos.settings.get(1, "tasks.config") == '{"running":true,"tasks":[]}'
    assert box.repos.accounts.credential_ref(1) == "a"


def test_profile_check_updates_same_account_without_switch(setup):
    _client, box = setup
    other = box.repos.accounts.upsert(AccountProfile(uid="22", nickname="B"), "b")
    box.repos.accounts.mark_current(1)
    box.account_health.read_profile = lambda _: AccountProfile(uid="22", nickname="B2", level=4)
    box.account_health.check(other)
    assert box.account()[0] == 1
    assert box.repos.accounts.by_id(other).nickname == "B2"
    assert box.account_health.view(other)["state"] == "valid"
    box.account_health.check(1)
    assert box.account_health.view(1)["state"] == "login_required"
    assert box.repos.accounts.by_id(1).nickname == "A"


def test_slow_check_returns_immediately_and_does_not_block_other_account(setup, monkeypatch):
    client, box = setup
    other = box.repos.accounts.upsert(AccountProfile(uid="22"), "b")
    release = threading.Event()
    calls = []

    def read(owner):
        calls.append(owner)
        if owner == 1:
            release.wait(5)
        return AccountProfile(uid="11" if owner == 1 else "22")

    box.account_health.read_profile = read
    try:
        assert client.post("/api/accounts/1/check", json={}).json()["checking"]
        assert client.post("/api/accounts/1/check", json={}).json()["checking"]
        box.account_health.check(other)
        assert box.account_health.view(other)["state"] == "valid"
        box.account_health.started[1] -= 21
        assert box.account_health.view(1)["state"] == "unavailable"
        assert calls.count(1) == 1
        assert client.get("/api/accounts").is_success
        assert client.post("/api/accounts/999/check", json={}).status_code == 404
    finally:
        release.set()
        box.account_health.workers[1].join(2)


def test_old_check_cannot_override_new_login(setup):
    from types import SimpleNamespace

    _, box = setup
    box.account_health.workers[1] = SimpleNamespace(is_alive=lambda: True)
    box.account_health.started[1] = 0
    box.account_health.signed_in(1)
    box.account_health.read_profile = lambda _: (_ for _ in ()).throw(MissingCredential("old"))
    box.account_health.check(1, generation=0)
    assert box.account_health.view(1)["state"] == "valid"


def test_logged_out_task_keeps_plan_and_continues_after_relogin(setup):
    from types import SimpleNamespace

    from test_coordination_retry import enabled

    client, box = setup
    cfg = enabled(client, box)
    box.account_health.record(1, "login_required", "expired")
    failed = box.tasks.run(1, "participate", cfg, apply=True, scheduled=True)
    assert failed["login_required"] and box.tasks.config(1) == cfg
    box.account_health.signed_in(1)
    box.run_pool = lambda _: SimpleNamespace(
        run=lambda *a, **k: SimpleNamespace(failed=0, attempted=0, joined=0, note="已恢复")
    )
    assert box.tasks.run(1, "participate", cfg, apply=True, scheduled=True)["ok"]
    assert box.tasks.view(1)["states"]["participate"]["latest"]["note"] == "已恢复"
