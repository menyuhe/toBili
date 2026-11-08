from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tobili.api.app import create_app
from tobili.domain.campaign import Campaign, CampaignKind
from tobili.ports.models import AccountProfile
from tobili.runtime.taskbook import TaskBook
from tobili.usecases.result_ledger import discover


@pytest.fixture
def ledger_box(tmp_path, monkeypatch):
    monkeypatch.setattr(TaskBook, "start", lambda self: None)
    app = create_app(home=str(tmp_path))
    with TestClient(app) as client:
        box = app.state.container
        a = box.repos.accounts.upsert(AccountProfile(uid="100", nickname="A"), "")
        b = box.repos.accounts.upsert(AccountProfile(uid="200", nickname="B"), "")
        box.repos.accounts.mark_current(a)
        now = datetime.now(UTC)
        cid = box.repos.campaigns.upsert(
            Campaign(
                dyn_id="111",
                up_uid="300",
                up_name="UP",
                title="活动",
                kind=CampaignKind.SHARE,
                seen_at=now,
                deadline=now - timedelta(days=2),
                confidence=0.9,
            )
        )
        with box.db.write() as db:
            for owner in (a, b):
                db.execute(
                    "INSERT INTO enrollment(account_id,campaign_id,stage,created_at,updated_at) VALUES(?,?,'settled',?,?)",
                    (owner, cid, now.isoformat(), now.isoformat()),
                )
            db.execute(
                "INSERT INTO verdict(campaign_id,account_id,outcome,decided_at,evidence) VALUES(?,?,'lost',?,'B only')",
                (cid, b, now.isoformat()),
            )
        yield client, box, a, b, cid


def test_persistent_account_results_and_completed_enrollment_is_checked(ledger_box):
    client, box, a, b, _cid = ledger_box
    result = client.get("/api/results").json()
    assert result["all_total"] == 1 and result["outcomes"]["unknown"] == 1
    assert result["items"][0]["evidence"] == ""
    assert len(box.repos.enrollments.settleable(a, before=datetime.now(UTC))) == 1
    assert not box.repos.enrollments.settleable(b, before=datetime.now(UTC))
    assert client.get("/api/results?query=no-match").json()["total"] == 0
    box.repos.accounts.mark_current(b)
    assert client.get("/api/results").json()["outcomes"]["lost"] == 1


def test_result_prizes_are_returned_and_searchable_without_using_dynamic_titles(ledger_box):
    client, box, _a, _b, cid = ledger_box
    with box.db.write() as db:
        db.execute(
            "UPDATE campaign SET reward='无线耳机两副',title='转发福利来了' WHERE id=?", (cid,)
        )
    result = client.get("/api/results", params={"query": "无线耳机"}).json()
    assert result["total"] == 1
    assert result["items"][0]["reward"] == "无线耳机两副"
    assert client.get("/api/results", params={"query": "转发福利来了"}).json()["total"] == 0


def test_can_pause_prize_review_even_when_model_is_not_configured(ledger_box):
    client, box, *_ = ledger_box
    box.repos.settings.put(0, "prizes.enabled", "1")
    response = client.post("/api/campaigns/prizes?enabled=false")
    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_matching_persists_cursor_and_requires_selected_account(ledger_box):
    client, box, a, b, cid = ledger_box
    pages = [
        dict(
            uid="100",
            items=[dict(id="888", original="111", up_uid="300")],
            cursor="next",
            scanned=5,
        ),
        dict(uid="100", items=[dict(id="999", original="111", up_uid="300")], cursor="", scanned=3),
    ]
    box.gateway_for = lambda owner: SimpleNamespace(own_shares=lambda **kw: pages.pop(0))
    progress = discover(box, a, pages=1)
    assert progress["cursor"] == "next"
    result = client.get("/api/results").json()
    assert result["links"]["candidate"] == 1 and result["items"][0]["share_id"] == ""
    assert (
        client.put(
            f"/api/results/{cid}/link", json={"account_id": b, "share_id": "888"}
        ).status_code
        == 409
    )
    assert (
        client.put(
            f"/api/results/{cid}/link", json={"account_id": a, "share_id": "777"}
        ).status_code
        == 409
    )
    discover(box, a, resume=True, pages=1)
    assert client.get("/api/results").json()["links"]["conflict"] == 1
    assert client.put(
        f"/api/results/{cid}/link", json={"account_id": a, "share_id": "888"}
    ).is_success
    assert client.get("/api/results").json()["links"]["verified"] == 1
    with pytest.raises(ValueError):
        discover(box, a, resume=True)


def test_failed_or_wrong_account_page_cannot_change_matches(ledger_box):
    _, box, a, _, _ = ledger_box
    box.repos.settings.put(
        a, "results.scan", '{"cursor":"saved","pages":2,"scanned":9,"finished":false}'
    )
    before = box.repos.settings.get(a, "results.scan")
    box.gateway_for = lambda owner: SimpleNamespace(
        own_shares=lambda **kw: dict(uid="200", items=[], cursor="", scanned=0)
    )
    with pytest.raises(ValueError):
        discover(box, a, resume=True)
    assert box.repos.settings.get(a, "results.scan") == before
    assert not box.db.connect().execute("SELECT * FROM participation_ref").fetchall()


def test_official_result_updates_completed_participation_without_external_writes(ledger_box):
    client, box, _a, _, _ = ledger_box
    calls = []
    with box.db.write() as con:
        con.execute(
            "UPDATE campaign SET draw_business_id='111',draw_business_type=1 WHERE dyn_id='111'"
        )

    def notice(business_id, business_type):
        calls.append((business_id, business_type))
        return {
            "status": 2,
            "first_prize": 1,
            "second_prize": 0,
            "third_prize": 0,
            "lottery_result": {"first_prize_result": [{"uid": "999"}]},
        }

    box.gateway_for = lambda owner: SimpleNamespace(lottery_notice=notice)
    response = client.post("/api/settle?apply=true").json()
    assert response["checked"] == 1 and response["decided"] == 1 and not response["problems"]
    assert calls == [("111", 1)]
    assert client.get("/api/results").json()["outcomes"]["lost"] == 1
    assert not box.db.connect().execute("SELECT * FROM intent").fetchall()
