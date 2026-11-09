from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_result_ledger import ledger_box as ledger_box

from tobili.domain.probe import ProbeState
from tobili.domain.tidy import ShareRef
from tobili.runtime.journal import Outcome


def setup(ledger_box, monkeypatch, error="缺少 UP 信息", ref=None):
    client, box, a, b, cid = ledger_box
    now = datetime.now(UTC)
    box.repos.enrollments.record_check(
        a, cid, now=now, retry_at=now - timedelta(seconds=1), error=error
    )
    calls = []
    gateway = SimpleNamespace(find_share=lambda *a, **k: ref, did=lambda *a, **k: ProbeState.ABSENT)
    monkeypatch.setattr(box, "check_writer", lambda owner: None)
    monkeypatch.setattr(box, "gateway_for", lambda owner: gateway)
    monkeypatch.setattr(
        box,
        "journal",
        lambda *a: SimpleNamespace(
            attempt=lambda **kw: (
                calls.append(kw) or SimpleNamespace(outcome=Outcome.SETTLED, message="done")
            )
        ),
    )
    return client, box, a, b, cid, calls


def test_explicit_discard_deletes_only_owned_repost_and_deduplicates(ledger_box, monkeypatch):
    c, box, a, _b, cid, calls = setup(
        ledger_box, monkeypatch, ref=ShareRef(dyn_id="888", author_uid="100", orig_dyn_id="111")
    )
    assert c.get("/api/history/attention").json()["results"] == 1
    r = c.post(f"/api/results/{cid}/discard", json={"account_id": a})
    assert r.status_code == 200 and r.json()["state"] == "done"
    assert len(calls) == 1 and calls[0]["action"].dyn_id == "888"
    assert c.get("/api/results").json()["all_total"] == 0
    assert c.get("/api/history/attention").json()["results"] == 0
    assert not box.repos.enrollments.settleable(a, before=datetime.now(UTC))
    c.post(f"/api/results/{cid}/discard", json={"account_id": a})
    assert len(calls) == 1
    assert box.db.connect().execute("SELECT count(*) FROM enrollment").fetchone()[0] == 2
    assert len(c.get("/api/results/discarded").json()["items"]) == 1


@pytest.mark.parametrize(
    "ref",
    [
        None,
        ShareRef(dyn_id="888", author_uid="200", orig_dyn_id="111"),
        ShareRef(dyn_id="888", author_uid="100", orig_dyn_id="wrong"),
    ],
)
def test_missing_or_foreign_repost_is_never_deleted(ledger_box, monkeypatch, ref):
    c, _box, a, _b, cid, calls = setup(ledger_box, monkeypatch, ref=ref)
    r = c.post(f"/api/results/{cid}/discard", json={"account_id": a})
    assert r.json()["state"] in ("not_found", "blocked") and not calls


def test_service_error_is_not_a_delete_request(ledger_box, monkeypatch):
    c, box, a, _b, cid, calls = setup(
        ledger_box, monkeypatch, error="RemoteError: -9999 服务系统错误"
    )
    assert c.post(f"/api/results/{cid}/discard", json={"account_id": a}).status_code == 400
    assert not calls and not box.db.connect().execute("SELECT 1 FROM result_dismissal").fetchone()


def test_winner_and_other_account_are_not_discardable(ledger_box, monkeypatch):
    c, box, a, b, cid, calls = setup(ledger_box, monkeypatch)
    box.repos.verdicts.record(
        campaign_id=cid,
        account_id=a,
        outcome=__import__("tobili.domain.tidy", fromlist=["Outcome"]).Outcome.WON,
        decided_at=datetime.now(UTC),
        evidence="winner",
    )
    assert c.post(f"/api/results/{cid}/discard", json={"account_id": a}).status_code == 400
    assert c.post(f"/api/results/{cid}/discard", json={"account_id": b}).status_code == 409
    assert not calls
