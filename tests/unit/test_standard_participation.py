from types import SimpleNamespace

import pytest

from tobili.adapters.bili import endpoints
from tobili.adapters.bili.prober import BiliProber
from tobili.adapters.bili.writer import BiliWriter
from tobili.domain.action import Action
from tobili.domain.action import ActionKind as A
from tobili.domain.campaign import CampaignKind as K
from tobili.domain.enrollment import Stage
from tobili.domain.extract import kind_from
from tobili.domain.probe import ProbeState as P
from tobili.domain.receipt import Receipt
from tobili.runtime.journal import Journal, Outcome
from tobili.usecases.enroll import Enroll


@pytest.mark.parametrize(
    "text",
    [
        "每日更新好物，关注+评论+转发抽键盘",
        "今天来评论区打卡，关注并转发本动态抽奖",
        "关注+评论+转发，即可参与抽奖",
        "每日更新抽奖，评论区打卡并转发",
    ],
)
def test_normal_interactions_are_not_checkin(text):
    assert kind_from(text) in (K.COMMENT, K.SHARE)


@pytest.mark.parametrize(
    "text", ["连续签到7天领取抽奖机会", "每天签到送积分，抽奖", "进入活动任务页打卡专区抽奖"]
)
def test_actual_checkin_task_remains_manual(text):
    assert kind_from(text) is K.CHECK_IN


def standard(repos, gateway, clock):
    return Enroll(
        gateway=gateway,
        campaigns=repos.campaigns,
        enrollments=repos.enrollments,
        journal=Journal(gateway=gateway, intents=repos.intents, clock=clock),
        clock=clock,
        standard_actions=True,
    )


def test_normal_flow_has_all_five_actions_and_six_progress_steps(repos, gateway, clock, account_id):
    gateway.detail_text = "转发抽奖：送一个鼠标"
    usecase = standard(repos, gateway, clock)
    events = []
    usecase._events = SimpleNamespace(
        publish=lambda topic, payload: events.append((topic, payload))
    )
    result = usecase.run(account_id, "123456")
    assert result.stage is Stage.SETTLED
    assert [a.kind for a in gateway.writes] == [A.LIKE, A.FOLLOW, A.FAVORITE, A.SHARE, A.COMMENT]
    steps = [p["message"] for topic, p in events if topic == "participation.step"]
    assert len(steps) == 6 and "5/6 核对本人转发" in steps[4]


def test_follow_not_confirmed_stops_before_repost(repos, gateway, clock, account_id):
    gateway.detail_text = "评论抽奖送鼠标"

    def perform(action):
        gateway.writes.append(action)
        return Receipt.unclear("关注状态未确认") if action.kind is A.FOLLOW else Receipt.success()

    gateway.perform = perform
    result = standard(repos, gateway, clock).run(account_id, "123456")
    assert result.stage is Stage.BLOCKED
    assert [a.kind for a in gateway.writes] == [A.LIKE, A.FOLLOW]


def test_no_favorite_entry_does_not_stop_required_actions(repos, gateway, clock, account_id):
    gateway.detail_text = "转发抽奖送一个鼠标"
    gateway.did = lambda action, **kw: P.UNKNOWN if action is A.FAVORITE else P.ABSENT
    result = standard(repos, gateway, clock).run(account_id, "123456")
    assert result.stage is Stage.SETTLED
    assert [a.kind for a in gateway.writes] == [A.LIKE, A.FOLLOW, A.SHARE, A.COMMENT]
    assert result.steps[2][1].outcome is Outcome.NOT_APPLICABLE


@pytest.mark.parametrize(
    "attribute,expected",
    [
        (0, P.ABSENT),
        (4, P.ABSENT),
        (2, P.PRESENT),
        (6, P.PRESENT),
        (128, P.UNKNOWN),
        (True, P.UNKNOWN),
    ],
)
def test_relation_direction_is_not_confused(attribute, expected):
    probe = BiliProber(
        SimpleNamespace(fetch=lambda *a, **kw: {"code": 0, "data": {"attribute": attribute}}), None
    )
    assert probe.did(A.FOLLOW, dyn_id="1", payload={"up_uid": "2"}) is expected


def test_successful_follow_is_verified_after_submission():
    wire = SimpleNamespace(
        csrf="test",
        submit=lambda *a, **k: {"code": 0},
        fetch=lambda *a, **k: {"code": 0, "data": {"attribute": 4}},
    )
    receipt = BiliWriter(wire).perform(Action(A.FOLLOW, "1", {"up_uid": "2"}))
    assert receipt.ambiguous and not receipt.ok
    wire.fetch = lambda *a, **k: {"code": 0, "data": {"attribute": 2}}
    assert BiliWriter(wire).perform(Action(A.FOLLOW, "1", {"up_uid": "2"})).ok


def test_removed_follow_can_be_repaired_but_uncertain_send_is_not_retried(
    repos, gateway, clock, account_id
):
    from tobili.domain.campaign import Campaign

    cid = repos.campaigns.upsert(Campaign(dyn_id="123456", up_uid="2", title="奖品"))
    journal = Journal(gateway=gateway, intents=repos.intents, clock=clock)
    action = Action(A.FOLLOW, "123456", {"up_uid": "2"})
    journal.attempt(account_id=account_id, campaign_id=cid, action=action)
    gateway.probe = P.ABSENT
    gateway.receipt = Receipt.unclear("关注未确认")
    second = journal.attempt(account_id=account_id, campaign_id=cid, action=action)
    third = journal.attempt(account_id=account_id, campaign_id=cid, action=action)
    assert second.outcome is third.outcome is Outcome.UNKNOWN
    assert len(gateway.writes) == 2


def test_favorite_uses_dynamic_endpoint_and_verifies_state():
    calls = []
    wire = SimpleNamespace(
        csrf="test",
        submit_json=lambda *a, **kw: calls.append((a, kw)) or {"code": 0},
        fetch=lambda *a, **kw: {
            "code": 0,
            "data": {"item": {"modules": [{"module_stat": {"favorite": {"status": True}}}]}},
        },
    )
    result = BiliWriter(wire).perform(Action(A.FAVORITE, "123456", {"target": "opus"}))
    assert result.ok
    assert calls[0][0][0] == endpoints.OPUS_ACTION
    assert calls[0][0][1]["entity"]["object_id_str"] == "123456"


def test_legacy_checkin_repair_preserves_prize_and_other_reviews(repos, clock, account_id):
    from tobili.domain.campaign import Campaign
    from tobili.usecases.participation_repair import reclassify_checkins

    ids = [
        repos.campaigns.upsert(
            Campaign(
                dyn_id=str(n),
                up_uid="2002",
                title="活动",
                reward="人工奖品",
                kind=K.CHECK_IN,
                body_text=body,
                deadline=clock.now(),
            )
        )
        for n, body in enumerate(["每日更新，关注评论转发抽奖", "连续签到7天抽奖"])
    ]
    with repos.db.write() as db:
        db.execute("UPDATE campaign SET reward_locked=1 WHERE id=?", (ids[0],))
        db.execute(
            "INSERT INTO pool_item VALUES(?,?,?,?,?,?)",
            (
                account_id,
                ids[0],
                0,
                "review",
                "打卡活动需要手动完成指定任务，请查看原动态",
                clock.now().isoformat(),
            ),
        )
    assert reclassify_checkins(SimpleNamespace(repos=repos, db=repos.db)) == 1
    first = repos.campaigns.get(ids[0])
    assert (
        first["kind"] in ("comment", "share")
        and first["reward"] == "人工奖品"
        and first["reward_locked"] == 1
    )
    assert first["deadline_at"] == clock.now().isoformat()
    assert repos.pool.items(account_id)[0]["message"] == "参与类型已更正，等待按常规流程复核"
    assert repos.campaigns.get(ids[1])["kind"] == "check_in"
    assert reclassify_checkins(SimpleNamespace(repos=repos, db=repos.db)) == 0


def test_historical_follow_repair_never_reposts_and_skips_expired(
    repos, gateway, clock, account_id
):
    from datetime import timedelta

    from tobili.domain.campaign import Campaign
    from tobili.usecases.participation_repair import reconcile_follows

    journal = Journal(gateway=gateway, intents=repos.intents, clock=clock)
    for n in range(5):
        cid = repos.campaigns.upsert(
            Campaign(
                dyn_id=str(n + 100),
                up_uid="2002",
                title="奖品",
                deadline=clock.now() + timedelta(days=-1 if n == 4 else 1),
            )
        )
        journal.attempt(
            account_id=account_id, campaign_id=cid, action=Action(A.SHARE, str(n + 100))
        )
    gateway.writes.clear()
    gateway.did = lambda action, **kw: P.PRESENT if action is A.SHARE else P.ABSENT
    box = SimpleNamespace(
        clock=clock,
        db=repos.db,
        repos=repos,
        gateway_for=lambda owner: gateway,
        journal=lambda g, o: journal,
        quota=lambda owner: SimpleNamespace(check=lambda o: SimpleNamespace(allowed=True)),
        bus=SimpleNamespace(publish=lambda *a: None),
    )
    assert reconcile_follows(box, account_id) == {"checked": 3, "repaired": 3}
    assert [a.kind for a in gateway.writes] == [A.FOLLOW] * 3
    assert all(a.dyn_id != "104" for a in gateway.writes)
    assert reconcile_follows(box, account_id) == {"checked": 1, "repaired": 1}
    assert reconcile_follows(box, account_id) == {"checked": 0, "repaired": 0}
    assert len(gateway.writes) == 4


def test_unverified_historical_share_never_causes_follow(repos, gateway, clock, account_id):
    from tobili.domain.campaign import Campaign
    from tobili.usecases.participation_repair import reconcile_follows

    journal = Journal(gateway=gateway, intents=repos.intents, clock=clock)
    cid = repos.campaigns.upsert(Campaign(dyn_id="123456", up_uid="2002", title="奖品"))
    journal.attempt(account_id=account_id, campaign_id=cid, action=Action(A.SHARE, "123456"))
    gateway.writes.clear()
    gateway.did = lambda action, **kw: P.UNKNOWN if action is A.SHARE else P.ABSENT
    box = SimpleNamespace(
        clock=clock,
        db=repos.db,
        repos=repos,
        gateway_for=lambda owner: gateway,
        journal=lambda g, o: journal,
        bus=SimpleNamespace(publish=lambda *a: None),
    )
    assert reconcile_follows(box, account_id) == {"checked": 1, "repaired": 0}
    assert gateway.writes == []
