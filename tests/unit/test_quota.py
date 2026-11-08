"""写配额门禁。

配额的存在意义是"宁可少做，不要触发风控" —— 风控一旦命中，接下来几个小时所有
请求都会失败。所以这里断言的是保守方向：数据不全时选择等待，而不是放行。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tobili.adapters.sqlite import Repositories
from tobili.domain.action import ActionKind
from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.throttle import Budget, Verdict
from tobili.ports.models import AccountProfile
from tobili.runtime.quota import Quota

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now(self) -> datetime:
        return self.moment


def _seed(repos: Repositories, count: int = 0, *, seconds_ago: float = 60) -> int:
    account_id = repos.accounts.upsert(AccountProfile(uid="1001"), "ref")
    campaign_id = repos.campaigns.upsert(
        Campaign(
            dyn_id="d1",
            up_uid="2002",
            title="转发抽奖",
            kind=CampaignKind.SHARE,
            confidence=0.9,
            seen_at=NOW,
        )
    )
    moment = NOW - timedelta(seconds=seconds_ago)
    for index in range(count):
        repos.intents.reserve(
            account_id=account_id,
            campaign_id=campaign_id,
            action=ActionKind.SHARE,
            fingerprint=f"f{index}",
            content="c",
            now=moment,
        )
    return account_id


def test_first_write_is_allowed(repos: Repositories) -> None:
    account_id = _seed(repos)
    gate = Quota(intents=repos.intents, clock=_Clock(NOW)).check(account_id)
    assert gate.verdict is Verdict.GO
    assert gate.idle_seconds is None


def test_recent_write_forces_a_pause(repos: Repositories) -> None:
    account_id = _seed(repos, count=1, seconds_ago=1)
    gate = Quota(intents=repos.intents, clock=_Clock(NOW)).check(account_id)
    assert gate.verdict is Verdict.HOLD
    assert gate.wait_seconds > 0
    assert "等" in gate.explain()


def test_hourly_cap_holds(repos: Repositories) -> None:
    account_id = _seed(repos, count=2, seconds_ago=600)
    quota = Quota(
        intents=repos.intents,
        clock=_Clock(NOW),
        budget=Budget(per_hour=2, per_day=10, min_gap_seconds=0),
    )
    assert quota.check(account_id).verdict is Verdict.HOLD


def test_daily_cap_stops(repos: Repositories) -> None:
    account_id = _seed(repos, count=3, seconds_ago=600)
    quota = Quota(
        intents=repos.intents,
        clock=_Clock(NOW),
        budget=Budget(per_hour=3, per_day=3, min_gap_seconds=0),
    )
    gate = quota.check(account_id)
    assert gate.verdict is Verdict.STOP
    assert "明天" in gate.explain()


def test_old_writes_do_not_count(repos: Repositories) -> None:
    """昨天写过不算今天的额度，否则一天之后就再也写不动了。"""
    account_id = _seed(repos, count=5, seconds_ago=25 * 3600)
    quota = Quota(
        intents=repos.intents,
        clock=_Clock(NOW),
        budget=Budget(per_hour=10, per_day=10, min_gap_seconds=0),
    )
    assert quota.check(account_id).last_day == 0
