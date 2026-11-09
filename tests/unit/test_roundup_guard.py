from dataclasses import replace
from datetime import timedelta

from tobili.domain.campaign import CampaignKind
from tobili.domain.extract import kind_from
from tobili.domain.participation import is_roundup
from tobili.runtime.journal import Journal
from tobili.usecases.batch_enroll import BatchEnroll
from tobili.usecases.enroll import Enroll

ROUNDUP = """吃瓜Menbro｜精选14个抽奖活动 2026-11-09 开奖
关注我，抽奖不迷路
本期 14 个活动 · 开奖区间：2026-11-09
互动抽奖 · 7 个
1. 激活码 ×2
开奖：2026-11-09 00:48 · https://t.bilibili.com/1266446136019779587
2. 648元红包 ×50
开奖：2026-11-09 03:00 · https://t.bilibili.com/1235178907264614400
"""


def test_roundup_is_not_an_independent_draw():
    assert is_roundup(ROUNDUP)
    assert kind_from(ROUNDUP) is CampaignKind.UNKNOWN
    assert not is_roundup("精选14个奖品，关注并转发本动态，11月20日开奖，抽取两人")
    assert not is_roundup("本期14个活动的介绍，另抽2个键盘；评论区留言参与")


def test_roundup_is_blocked_before_any_action_and_next_item_runs(repos, gateway, clock, account_id):
    original = gateway.detail

    def detail(dyn):
        d = original(dyn)
        return replace(
            d,
            text=ROUNDUP if dyn == "1" else "关注并转发本动态，送键盘，11月20日开奖",
            deadline=clock.now() + timedelta(days=10),
        )

    gateway.detail = detail
    from tobili.domain.campaign import Campaign

    aid = account_id
    for dyn in ("1", "2"):
        repos.campaigns.upsert(
            Campaign(
                dyn_id=dyn,
                up_uid="2002",
                title="test",
                kind=CampaignKind.SHARE,
                deadline=clock.now() + timedelta(days=10),
                confidence=0.95,
                seen_at=clock.now(),
            )
        )
    enroll = Enroll(
        gateway=gateway,
        campaigns=repos.campaigns,
        enrollments=repos.enrollments,
        journal=Journal(gateway=gateway, intents=repos.intents, clock=clock),
        clock=clock,
        standard_actions=True,
    )
    batch = BatchEnroll(campaigns=repos.campaigns, enroll=enroll, clock=clock)
    report = batch.run(aid, limit=2, dry_run=False, stop_on_failure=True)
    assert report.ok and report.blocked == 1 and report.settled == 1
    assert "汇总文章" in report.lines[0].note
    assert all(a.dyn_id == "2" for a in gateway.writes)
    assert repos.campaigns.by_dyn("1")[1].draw.state == "unsupported"
