"""清理预演。

预演只产清单，不做任何删除。这里断言的是"保守"：宽限期内、超过冻结期、
同一 UP 超过上限，都必须出现在清单里但标为不可清，并带上人话理由 —— 因为
这些理由会原样显示给用户看。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tobili.adapters.sqlite import Repositories
from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.tidy import Outcome
from tobili.ports.models import AccountProfile
from tobili.usecases.plan_tidy import PlanTidy

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now(self) -> datetime:
        return self.moment


def _lost(
    repos: Repositories,
    account_id: int,
    *,
    dyn_id: str,
    hours_ago: float,
    up_uid: str = "2002",
) -> None:
    campaign_id = repos.campaigns.upsert(
        Campaign(
            dyn_id=dyn_id,
            up_uid=up_uid,
            up_name="某个 UP",
            title="转发抽奖",
            kind=CampaignKind.SHARE,
            deadline=NOW - timedelta(days=2),
            confidence=0.9,
            seen_at=NOW,
        )
    )
    repos.enrollments.create(account_id, campaign_id, now=NOW)
    repos.verdicts.record(
        campaign_id=campaign_id,
        account_id=account_id,
        outcome=Outcome.LOST,
        decided_at=NOW - timedelta(hours=hours_ago),
        evidence="开奖公告未提及你",
    )


def _plan(repos: Repositories) -> PlanTidy:
    return PlanTidy(verdicts=repos.verdicts, tidy=repos.tidy, clock=_Clock(NOW))


def test_nothing_to_clean_when_no_lost_verdicts(repos: Repositories) -> None:
    account_id = repos.accounts.upsert(AccountProfile(uid="1001"), "ref")
    preview = _plan(repos).run(account_id)
    assert preview.lines == ()
    assert preview.persisted is False


def test_within_grace_is_listed_but_blocked(repos: Repositories) -> None:
    account_id = repos.accounts.upsert(AccountProfile(uid="1001"), "ref")
    _lost(repos, account_id, dyn_id="d1", hours_ago=1)

    preview = _plan(repos).run(account_id)
    assert len(preview.lines) == 1
    assert preview.lines[0].allowed is False
    assert "宽限" in preview.lines[0].reason
    assert preview.persisted is False


def test_old_enough_is_clearable(repos: Repositories) -> None:
    account_id = repos.accounts.upsert(AccountProfile(uid="1001"), "ref")
    _lost(repos, account_id, dyn_id="d1", hours_ago=30)

    preview = _plan(repos).run(account_id)
    assert preview.clearable == 1
    assert preview.persisted is True
    assert preview.plan_id > 0
    assert preview.lines[0].reason


def test_plan_records_items(repos: Repositories) -> None:
    account_id = repos.accounts.upsert(AccountProfile(uid="1001"), "ref")
    _lost(repos, account_id, dyn_id="d1", hours_ago=30)

    preview = _plan(repos).run(account_id)
    items = repos.tidy.items_of(preview.plan_id)
    assert len(items) == 1
    assert repos.tidy.state_of(preview.plan_id) == "previewed"


def test_frozen_records_are_blocked(repos: Repositories) -> None:
    account_id = repos.accounts.upsert(AccountProfile(uid="1001"), "ref")
    _lost(repos, account_id, dyn_id="d1", hours_ago=24 * 100)

    preview = _plan(repos).run(account_id)
    assert preview.clearable == 0
    assert "冻结" in preview.lines[0].reason


def test_round_cap_is_respected(repos: Repositories) -> None:
    account_id = repos.accounts.upsert(AccountProfile(uid="1001"), "ref")
    for index in range(5):
        _lost(repos, account_id, dyn_id=f"d{index}", hours_ago=30, up_uid=f"200{index}")

    preview = _plan(repos).run(account_id, limit=2)
    assert preview.clearable == 2
    blocked = [line for line in preview.lines if not line.allowed]
    assert blocked and "上限" in blocked[0].reason


def test_same_up_cap_is_respected(repos: Repositories) -> None:
    """同一 UP 连续删太多容易被对方发现，策略默认只允许 3 条。"""
    account_id = repos.accounts.upsert(AccountProfile(uid="1001"), "ref")
    for index in range(5):
        _lost(repos, account_id, dyn_id=f"d{index}", hours_ago=30 + index, up_uid="2002")

    preview = _plan(repos).run(account_id)
    assert preview.clearable == 3
