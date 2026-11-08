"""人工改判。

这条链路上机器判错的代价不对称：判成"中奖"只是少清一条，判成"未中奖"会真的
删掉一条其实中奖的转发。所以这里验证两件事：改判能写进去，以及改完之后自动
核验不许再覆盖。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tobili.adapters.sqlite import Repositories
from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.tidy import Outcome
from tobili.ports.models import AccountProfile, FeedItem, FeedSlice
from tobili.usecases.settle import Settle, SettleRejected

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
PROFILE = AccountProfile(uid="1001", nickname="玖仟", level=6)


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Feed:
    def __init__(self, items: tuple[FeedItem, ...] = ()) -> None:
        self.items = items

    def scan(self, *, kind: str, target: str, cursor: str, limit: int) -> FeedSlice:
        return FeedSlice(items=self.items, cursor="", exhausted=True)


def _seed(repos: Repositories) -> tuple[int, int]:
    account_id = repos.accounts.upsert(PROFILE, "ref")
    campaign_id = repos.campaigns.upsert(
        Campaign(
            dyn_id="d1",
            up_uid="2002",
            up_name="某个 UP",
            title="转发抽奖",
            kind=CampaignKind.SHARE,
            deadline=NOW - timedelta(hours=30),
            confidence=0.9,
            seen_at=NOW,
        )
    )
    repos.enrollments.create(account_id, campaign_id, now=NOW)
    return account_id, campaign_id


def _settle(repos: Repositories, feed: _Feed) -> Settle:
    return Settle(
        gateway=feed,  # type: ignore[arg-type]
        enrollments=repos.enrollments,
        verdicts=repos.verdicts,
        clock=_Clock(),
        grace_hours=12,
    )


def _announcement(text: str) -> FeedItem:
    return FeedItem(
        dyn_id="a1",
        up_uid="2002",
        text=text,
        published_at=datetime(2026, 11, 3, 10, 0, tzinfo=UTC),
        up_name="某个 UP",
    )


def test_override_writes_manual_verdict(repos: Repositories) -> None:
    account_id, campaign_id = _seed(repos)
    report = _settle(repos, _Feed()).override(
        account_id, campaign_id=campaign_id, outcome=Outcome.WON, note="图片名单里其实有我"
    )

    assert report.outcome is Outcome.WON
    assert report.previous is None
    assert report.was_manual is False
    detail = repos.verdicts.detail(campaign_id, account_id)
    assert detail is not None
    assert detail["outcome"] == "won"
    assert detail["source"] == "manual"
    assert detail["note"] == "图片名单里其实有我"
    assert "人工改判" in str(detail["evidence"])


def test_auto_settle_does_not_overwrite_manual(repos: Repositories) -> None:
    """改完之后自动核验写不进去。

    改判会把报名推进到已结算，所以正常流程里自动核验根本不会再碰它 —— 这一条
    验证的是更底层的那道保险：只要 `record(source="auto")` 落在一条 manual 记录
    上，就必须返回 False 并且不改值。报名被手动退回 planned 重新参与时，靠的就是它。
    """
    account_id, campaign_id = _seed(repos)
    _settle(repos, _Feed()).override(
        account_id, campaign_id=campaign_id, outcome=Outcome.WON, note="人工确认中奖"
    )

    written = repos.verdicts.record(
        campaign_id=campaign_id,
        account_id=account_id,
        outcome=Outcome.LOST,
        decided_at=NOW,
        evidence="自动核验：名单里没有你",
    )
    assert written is False
    assert repos.verdicts.of(campaign_id, account_id) is Outcome.WON
    detail = repos.verdicts.detail(campaign_id, account_id)
    assert detail is not None and detail["source"] == "manual"


def test_settled_enrollment_is_not_rechecked(repos: Repositories) -> None:
    """已经结算的报名不再进自动核验队列。改判之后不该被再算一次。"""
    account_id, campaign_id = _seed(repos)
    _settle(repos, _Feed()).override(
        account_id, campaign_id=campaign_id, outcome=Outcome.WON, note="人工确认中奖"
    )
    report = _settle(repos, _Feed((_announcement("开奖结果：恭喜 @别人 中奖"),))).run(
        account_id, profile=PROFILE, dry_run=False
    )
    assert report.checked == 0
    assert repos.verdicts.of(campaign_id, account_id) is Outcome.WON


def test_override_can_flip_back(repos: Repositories) -> None:
    """人改错了还得能再改回来。第二次改判覆盖第一次，并告诉我们之前是人工结论。"""
    account_id, campaign_id = _seed(repos)
    corner = _settle(repos, _Feed())
    corner.override(account_id, campaign_id=campaign_id, outcome=Outcome.LOST, note="第一次")
    report = corner.override(
        account_id, campaign_id=campaign_id, outcome=Outcome.VOID, note="其实是作废"
    )

    assert report.previous is Outcome.LOST
    assert report.was_manual is True
    assert repos.verdicts.of(campaign_id, account_id) is Outcome.VOID


def test_pending_is_not_a_verdict(repos: Repositories) -> None:
    """待定不是一种结论，是"还没判"。允许改判成 pending 等于允许把结论抹掉。"""
    account_id, campaign_id = _seed(repos)
    with pytest.raises(SettleRejected):
        _settle(repos, _Feed()).override(
            account_id, campaign_id=campaign_id, outcome=Outcome.PENDING, note=""
        )


def test_override_moves_enrollment_to_settled(repos: Repositories) -> None:
    """改判之后报名要推进到已结算，否则界面上看不出这条已经有结论了。"""
    account_id, campaign_id = _seed(repos)
    _settle(repos, _Feed()).override(
        account_id, campaign_id=campaign_id, outcome=Outcome.LOST, note=""
    )
    rows = [e for e in repos.enrollments.by_campaign(campaign_id) if e.account_id == account_id]
    assert rows and rows[0].stage.value == "settled"
    assert "人工改判" in rows[0].reason


def test_manual_lost_shows_up_in_tidy_preview(repos: Repositories) -> None:
    """人工判成未中奖的活动，要能进清理预演 —— 否则改判没法用来纠正清理范围。"""
    from tobili.usecases.plan_tidy import PlanTidy

    account_id, campaign_id = _seed(repos)
    _settle(repos, _Feed()).override(
        account_id, campaign_id=campaign_id, outcome=Outcome.LOST, note="确认没中"
    )
    preview = PlanTidy(
        verdicts=repos.verdicts, tidy=repos.tidy, clock=_Clock(), keep=repos.keep
    ).run(account_id, persist=False)

    assert len(preview.lines) == 1
    assert preview.lines[0].dyn_id == "d1"
