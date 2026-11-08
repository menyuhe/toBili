"""清理保护名单。

名单只有一个方向：保护。没有"强制清理"——想删一条被保护的东西，得先把它从
名单里移出去，那是又一个需要人动手的动作。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tobili.adapters.sqlite import Repositories
from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.tidy import KeepKind, Outcome
from tobili.ports.models import AccountProfile
from tobili.usecases.plan_tidy import PlanTidy

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
PROFILE = AccountProfile(uid="1001", nickname="玖仟", level=6)


class _Clock:
    def now(self) -> datetime:
        return NOW


def _seed(repos: Repositories, *, dyn_ids: tuple[str, ...] = ("d1",)) -> tuple[int, list[int]]:
    account_id = repos.accounts.upsert(PROFILE, "ref")
    ids: list[int] = []
    for index, dyn in enumerate(dyn_ids):
        campaign_id = repos.campaigns.upsert(
            Campaign(
                dyn_id=dyn,
                up_uid=f"up{index}",
                up_name=f"UP{index}",
                title=f"活动 {dyn}",
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
            decided_at=NOW - timedelta(days=2),
            evidence="测试",
        )
        ids.append(campaign_id)
    return account_id, ids


def _preview(repos: Repositories, account_id: int):
    return PlanTidy(verdicts=repos.verdicts, tidy=repos.tidy, clock=_Clock(), keep=repos.keep).run(
        account_id, persist=False
    )


def test_up_in_keep_list_is_not_cleanable(repos: Repositories) -> None:
    account_id, _ = _seed(repos)
    repos.keep.add(account_id, kind=KeepKind.UP.value, value="up0", note="喜欢这个 UP", now=NOW)

    preview = _preview(repos, account_id)
    assert len(preview.lines) == 1
    assert preview.lines[0].allowed is False
    assert "保护名单" in preview.lines[0].reason
    assert "喜欢这个 UP" in preview.lines[0].reason


def test_campaign_in_keep_list_is_not_cleanable(repos: Repositories) -> None:
    account_id, _ = _seed(repos, dyn_ids=("d1", "d2"))
    repos.keep.add(account_id, kind=KeepKind.CAMPAIGN.value, value="d2", note="", now=NOW)

    preview = _preview(repos, account_id)
    allowed = {line.dyn_id: line.allowed for line in preview.lines}
    assert allowed == {"d1": True, "d2": False}


def test_up_beats_campaign_in_reason(repos: Repositories) -> None:
    """两个都命中时说"按 UP 保护"，因为对一个 UP 有偏好比某一条活动更常是原因。"""
    account_id, _ = _seed(repos)
    repos.keep.add(account_id, kind=KeepKind.UP.value, value="up0", note="", now=NOW)
    repos.keep.add(account_id, kind=KeepKind.CAMPAIGN.value, value="d1", note="", now=NOW)

    preview = _preview(repos, account_id)
    assert "按 UP 保护" in preview.lines[0].reason


def test_keeping_everything_is_not_possible(repos: Repositories) -> None:
    """名单是逐条加的，没有"全部保护"的写法 —— 那等于关掉功能却让人以为清干净了。"""
    account_id, _ids = _seed(repos)
    repos.keep.add(account_id, kind=KeepKind.UP.value, value="up0", note="", now=NOW)
    repos.keep.remove(account_id, kind=KeepKind.UP.value, value="up0")
    assert repos.keep.all_of(account_id) == []

    preview = _preview(repos, account_id)
    assert preview.lines[0].allowed is True


def test_keep_list_is_per_account(repos: Repositories) -> None:
    """名单跟着账号走。换个号不该继承别人的偏好。"""
    account_a, _ = _seed(repos)
    account_b = repos.accounts.upsert(
        AccountProfile(uid="1002", nickname="另一个人", level=3), "ref-b"
    )
    repos.keep.add(account_a, kind=KeepKind.UP.value, value="up0", note="", now=NOW)

    assert len(repos.keep.all_of(account_a)) == 1
    assert repos.keep.all_of(account_b) == []


def test_add_twice_updates_note(repos: Repositories) -> None:
    account_id, _ = _seed(repos)
    repos.keep.add(account_id, kind=KeepKind.UP.value, value="up0", note="旧备注", now=NOW)
    repos.keep.add(account_id, kind=KeepKind.UP.value, value="up0", note="新备注", now=NOW)

    rows = repos.keep.all_of(account_id)
    assert len(rows) == 1
    assert rows[0]["note"] == "新备注"
