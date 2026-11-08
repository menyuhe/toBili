"""开奖核验用例。

用例本身不做判断，判断在 `domain.result`。这里验证的是接线正确：读哪个源、
判完之后写不写库、单个 UP 出错会不会连带整轮失败。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tobili.adapters.sqlite import Repositories
from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.enrollment import Stage
from tobili.domain.tidy import Outcome
from tobili.ports.models import AccountProfile, FeedItem, FeedSlice
from tobili.usecases.settle import Settle

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
PROFILE = AccountProfile(uid="1001", nickname="玖仟", level=6)


class _Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now(self) -> datetime:
        return self.moment


class _Feed:
    def __init__(self, items: tuple[FeedItem, ...] = (), *, boom: bool = False) -> None:
        self.items = items
        self.boom = boom
        self.calls: list[tuple[str, str]] = []

    def scan(self, *, kind: str, target: str, cursor: str, limit: int) -> FeedSlice:
        self.calls.append((kind, target))
        if self.boom:
            raise RuntimeError("上游挂了")
        return FeedSlice(items=self.items, cursor="", exhausted=True)


def _seed(repos: Repositories, *, hours_past: float = 1.0) -> tuple[int, int, int]:
    account_id = repos.accounts.upsert(PROFILE, "ref")
    campaign_id = repos.campaigns.upsert(
        Campaign(
            dyn_id="d1",
            up_uid="2002",
            up_name="某个 UP",
            title="转发抽奖",
            kind=CampaignKind.SHARE,
            deadline=NOW - timedelta(hours=hours_past),
            confidence=0.9,
            seen_at=NOW,
        )
    )
    enrollment_id = repos.enrollments.create(account_id, campaign_id, now=NOW)
    return account_id, campaign_id, enrollment_id


def _settle(repos: Repositories, feed: _Feed) -> Settle:
    return Settle(
        gateway=feed,  # type: ignore[arg-type]
        enrollments=repos.enrollments,
        verdicts=repos.verdicts,
        clock=_Clock(NOW),
    )


def _item(text: str, *, days_ago: float = 2) -> FeedItem:
    return FeedItem(
        dyn_id="d9",
        up_uid="2002",
        text=text,
        published_at=NOW - timedelta(days=days_ago),
        up_name="某个 UP",
    )


def test_reads_the_up_space_feed(repos: Repositories) -> None:
    account_id, _campaign_id, _enrollment_id = _seed(repos)
    feed = _Feed()
    _settle(repos, feed).run(account_id, profile=PROFILE, dry_run=False)
    assert feed.calls == [("space", "2002")]


def test_no_announcement_leaves_everything_alone(repos: Repositories) -> None:
    account_id, campaign_id, enrollment_id = _seed(repos)
    report = _settle(repos, _Feed()).run(account_id, profile=PROFILE, dry_run=False)

    assert report.checked == 1
    assert report.decided == 0
    assert repos.verdicts.of(campaign_id, account_id) is None
    assert repos.enrollments.get(enrollment_id).stage is Stage.PLANNED


def test_announcement_with_my_name_wins(repos: Repositories) -> None:
    account_id, campaign_id, enrollment_id = _seed(repos)
    feed = _Feed((_item("开奖名单：@玖仟 等 3 人"),))
    report = _settle(repos, feed).run(account_id, profile=PROFILE, dry_run=False)

    assert report.decided == 1
    assert report.items[0].outcome is Outcome.WON
    assert repos.verdicts.of(campaign_id, account_id) is Outcome.WON
    assert repos.enrollments.get(enrollment_id).stage is Stage.SETTLED


def test_settled_announcement_without_me_loses(repos: Repositories) -> None:
    account_id, campaign_id, _enrollment_id = _seed(repos)
    feed = _Feed((_item("开奖结果已公布，恭喜以下小伙伴", days_ago=3),))
    _settle(repos, feed).run(account_id, profile=PROFILE, dry_run=False)
    assert repos.verdicts.of(campaign_id, account_id) is Outcome.LOST


def test_dry_run_reports_without_writing(repos: Repositories) -> None:
    account_id, campaign_id, enrollment_id = _seed(repos)
    feed = _Feed((_item("开奖名单：@玖仟"),))
    report = _settle(repos, feed).run(account_id, profile=PROFILE, dry_run=True)

    assert report.decided == 1
    assert repos.verdicts.of(campaign_id, account_id) is None
    assert repos.enrollments.get(enrollment_id).stage is Stage.PLANNED


def test_failed_up_is_reported_not_fatal(repos: Repositories) -> None:
    account_id, _campaign_id, _enrollment_id = _seed(repos)
    report = _settle(repos, _Feed(boom=True)).run(account_id, profile=PROFILE, dry_run=False)
    assert report.checked == 0
    assert report.problems
    assert not report.ok


def test_open_campaign_is_not_checked_yet(repos: Repositories) -> None:
    """还没截止的活动不该被核验，否则会把"还没开奖"误算成一次检查。"""
    account_id, _campaign_id, _enrollment_id = _seed(repos, hours_past=-5)
    feed = _Feed()
    report = _settle(repos, feed).run(account_id, profile=PROFILE, dry_run=False)
    assert report.checked == 0
    assert feed.calls == []
