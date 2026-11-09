from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_settle import NOW, PROFILE, _Clock, _seed

from tobili.adapters.bili.reader import DynamicReader
from tobili.domain.errors import Fault, RemoteError
from tobili.ports.models import FeedItem, FeedSlice
from tobili.runtime.events import EventBus
from tobili.usecases.settle import Settle


def test_transient_official_error_retries_once_and_validates_string_type(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    calls = []

    def fetch(*args, **kw):
        calls.append(kw)
        return (
            {"code": -9999}
            if len(calls) == 1
            else {"code": 0, "data": dict(lottery_id=7, business_id="99", business_type="10")}
        )

    reader = DynamicReader(SimpleNamespace(fetch=fetch))
    assert reader.lottery_notice("99", 10)["lottery_id"] == 7
    assert len(calls) == 2 and calls[0]["referer"]
    reader._transport.fetch = lambda *a, **k: {"code": -9999, "message": "服务系统错误"}
    with pytest.raises(RemoteError):
        reader.lottery_notice("99", 10)


def test_manual_draw_does_not_hit_official_or_infer_loss(repos):
    owner, cid, _ = _seed(repos)
    with repos.db.write() as con:
        con.execute(
            "UPDATE campaign SET draw_business_id='d1',draw_business_type=0 WHERE id=?", (cid,)
        )

    def no_official(*a):
        raise AssertionError("not an official draw")

    gateway = SimpleNamespace(
        lottery_notice=no_official,
        scan=lambda **k: FeedSlice(
            items=(
                FeedItem(
                    dyn_id="88",
                    up_uid="2002",
                    text="d1 开奖名单已公布，恭喜以下小伙伴",
                    published_at=NOW - timedelta(days=2),
                ),
            ),
            cursor="",
            exhausted=True,
        ),
    )
    run = Settle(
        gateway=gateway, enrollments=repos.enrollments, verdicts=repos.verdicts, clock=_Clock(NOW)
    )
    r = run.run(owner, profile=PROFILE, dry_run=False)
    assert r.checked == 1 and r.decided == 0 and not r.problems
    assert not repos.enrollments.settleable(owner, before=NOW)


def test_plain_repost_without_component_does_not_call_official_api(repos):
    owner, cid, _ = _seed(repos)
    with repos.db.write() as db:
        db.execute(
            "UPDATE campaign SET kind='share',draw_business_id='',draw_business_type=0 WHERE id=?",
            (cid,),
        )

    def forbidden(*a):
        raise AssertionError("ordinary repost is not an official lottery")

    gateway = SimpleNamespace(
        lottery_notice=forbidden, scan=lambda **k: FeedSlice(items=(), cursor="", exhausted=True)
    )
    report = Settle(
        gateway=gateway, enrollments=repos.enrollments, verdicts=repos.verdicts, clock=_Clock(NOW)
    ).run(owner, profile=PROFILE, dry_run=False)
    assert report.checked == 1 and not report.problems


def test_service_failure_persists_cooldown_and_never_changes_verdict(repos):
    owner, cid, _ = _seed(repos)
    with repos.db.write() as con:
        con.execute(
            "UPDATE campaign SET draw_business_id='99',draw_business_type=10 WHERE id=?", (cid,)
        )
    calls = []

    def broken(*a):
        calls.append(a)
        raise RemoteError(Fault.REMOTE_BUSINESS, "服务系统错误", code=-9999)

    bus = EventBus()

    def runner():
        return Settle(
            gateway=SimpleNamespace(lottery_notice=broken),
            enrollments=repos.enrollments,
            verdicts=repos.verdicts,
            clock=_Clock(NOW),
            settings=repos.settings,
            events=bus,
        )

    assert runner().run(owner, profile=PROFILE, dry_run=False).problems
    assert "冷却" in runner().run(owner, profile=PROFILE).problems[0]
    assert calls == [("99", 10)]
    assert repos.verdicts.of(cid, owner) is None
    assert bus.since(0)[0].topic == "settle"
