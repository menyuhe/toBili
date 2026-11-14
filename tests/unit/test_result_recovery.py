from datetime import UTC, datetime, timedelta
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
    # 两条都是 -9999：这是"这条动态没有抽奖公告"的固定回答，不是故障。
    # 当成 `None` 交回去，让调用方走"没有公告"的推断路径。
    reader._transport.fetch = lambda *a, **k: {"code": -9999, "message": "服务系统错误"}
    assert reader.lottery_notice("99", 10) is None
    # 真正的故障仍然要抛：否则"没登记"和"没查成"就混成一件事了。
    reader._transport.fetch = lambda *a, **k: {"code": -500, "message": "服务器错误"}
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
        # -9999 是"这条没有公告"，不该再触发整轮冷却；这里用真正的风控码验证。
        raise RemoteError(Fault.RISK, "请求被拦截", code=-412)

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


def test_missing_notice_keeps_checking_instead_of_cooling_down(repos):
    """官方索引没有这条活动时要继续核验，而不是当成服务故障把整轮停掉。"""
    owner, cid, _ = _seed(repos)
    with repos.db.write() as con:
        con.execute(
            "UPDATE campaign SET draw_business_id='99',draw_business_type=10 WHERE id=?", (cid,)
        )
    calls = []

    def empty(business_id, business_type=1):
        calls.append((business_id, business_type))
        return None

    gateway = SimpleNamespace(
        lottery_notice=empty,
        scan=lambda **k: FeedSlice(items=(), cursor=""),
    )
    report = Settle(
        gateway=gateway,
        enrollments=repos.enrollments,
        verdicts=repos.verdicts,
        clock=_Clock(NOW),
        settings=repos.settings,
    ).run(owner, profile=PROFILE, dry_run=False)
    assert calls == [("99", 10)]
    # 关键：没有"本轮已停止 / 冷却"这种整轮放弃的结论。
    assert all("冷却" not in p for p in report.problems)
    assert repos.settings.get(owner, "results.cooldown", "0") == "0"


def test_waiting_draw_is_scheduled_at_its_announced_time(repos):
    """公告已经发出、但还没到开奖时间：等到那个点再看，别按 30 分钟一直问。

    预约类活动最容易踩这个：预约截止时公告里只有"什么时候开奖"，名单要等真正的
    开奖时刻才有。按固定节奏重试，等于几十条一起空转。
    """
    from tobili.adapters.sqlite.db import parse_stamp
    from tobili.domain.tidy import Outcome

    owner, cid, _ = _seed(repos)
    with repos.db.write() as con:
        con.execute(
            "UPDATE campaign SET draw_business_id='99',draw_business_type=10 WHERE id=?", (cid,)
        )
    draw_at = int(NOW.timestamp()) + 3600
    notice = {
        "lottery_id": 7,
        "business_id": "99",
        "business_type": "10",
        "lottery_time": draw_at,
    }
    report = Settle(
        gateway=SimpleNamespace(lottery_notice=lambda *a: notice),
        enrollments=repos.enrollments,
        verdicts=repos.verdicts,
        clock=_Clock(NOW),
        settings=repos.settings,
    ).run(owner, profile=PROFILE, dry_run=False)
    assert report.items and report.items[0].outcome is Outcome.PENDING
    row = (
        repos.db.connect()
        .execute("SELECT retry_at FROM result_probe WHERE campaign_id=?", (cid,))
        .fetchone()
    )
    retry_at = parse_stamp(row["retry_at"])
    assert retry_at >= datetime.fromtimestamp(draw_at, UTC)
