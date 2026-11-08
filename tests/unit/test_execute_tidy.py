"""清理执行。

这些测试围绕一个信念：**默认什么都不做**。所以断言里大量出现"没有写"、"被拦住"、
"计划还开着"。真正会删东西的那几条，也都在验证"删的是不是我以为的那条"。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tobili.adapters.sqlite import Repositories
from tobili.domain.action import ActionKind
from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.errors import Fault
from tobili.domain.probe import ProbeState
from tobili.domain.receipt import Receipt
from tobili.domain.tidy import Outcome, ShareRef
from tobili.ports.models import AccountProfile, MessageStat
from tobili.runtime.gate import CleaningGate
from tobili.runtime.journal import Journal
from tobili.usecases.execute_tidy import ExecuteTidy, TidyBlocked, TidyRejected

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment

    def now(self) -> datetime:
        return self.moment


def _campaign(repos: Repositories, dyn_id: str, up_uid: str = "2002") -> int:
    return repos.campaigns.upsert(
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


def _plan(
    repos: Repositories,
    account_id: int,
    *,
    action: str = "drop_share",
    dyn_ids: tuple[str, ...] = ("d1",),
) -> int:
    plan_id = repos.tidy.create_plan(account_id, now=NOW)
    for index, dyn_id in enumerate(dyn_ids):
        campaign_id = _campaign(repos, dyn_id, up_uid=f"200{index}")
        repos.enrollments.create(account_id, campaign_id, now=NOW)
        if action == "drop_share":
            repos.verdicts.record(
                campaign_id=campaign_id,
                account_id=account_id,
                outcome=Outcome.LOST,
                decided_at=NOW - timedelta(hours=30),
                evidence="开奖公告未提及你",
            )
        repos.tidy.add_item(plan_id, campaign_id=campaign_id, action=action, snapshot="{}")
    repos.tidy.mark_previewed(plan_id, now=NOW)
    return plan_id


def _executor(repos: Repositories, gateway, clock: _Clock, snapshot=None) -> ExecuteTidy:  # type: ignore[no-untyped-def]
    gate = CleaningGate(
        gateway=gateway, intents=repos.intents, settings=repos.settings, clock=clock
    )
    journal = Journal(gateway=gateway, intents=repos.intents, clock=clock)
    return ExecuteTidy(
        gateway=gateway,
        tidy=repos.tidy,
        journal=journal,
        gate=gate,
        clock=clock,
        snapshot=snapshot,
    )


@pytest.fixture
def account(repos: Repositories) -> int:
    return repos.accounts.upsert(AccountProfile(uid="1001", nickname="测试账号"), "ref")


@pytest.fixture
def mine() -> ShareRef:
    return ShareRef(dyn_id="mine-1", author_uid="1001", orig_dyn_id="d1", published_at=NOW)


# ---- 阶段顺序 ----
def test_previewed_plan_refuses_to_run(
    repos: Repositories, gateway, clock: _Clock, account: int
) -> None:
    """看过不等于同意。没确认的计划一律拒绝执行。"""
    plan_id = _plan(repos, account)
    with pytest.raises(TidyRejected):
        _executor(repos, gateway, clock).run(plan_id, apply=True)
    assert gateway.writes == []
    assert repos.tidy.state_of(plan_id) == "previewed"


def test_confirm_advances_the_plan(
    repos: Repositories, gateway, clock: _Clock, account: int
) -> None:
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock)
    assert executor.confirm(plan_id).value == "confirmed"
    assert repos.tidy.state_of(plan_id) == "confirmed"


def test_finished_plan_cannot_be_run_again(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    gateway.share = mine
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)
    executor.run(plan_id, apply=True)
    with pytest.raises(TidyRejected):
        executor.run(plan_id, apply=True)


# ---- 预演不写 ----
def test_dry_run_plans_without_writing(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    gateway.share = mine
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)

    report = executor.run(plan_id, apply=False)
    assert gateway.writes == []
    assert report.planned == 1
    assert "预演" in report.note
    assert repos.tidy.state_of(plan_id) == "confirmed"


# ---- 真的删 ----
def test_apply_deletes_my_own_share(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    gateway.share = mine
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)

    report = executor.run(plan_id, apply=True)
    assert report.done == 1
    assert len(gateway.writes) == 1
    assert gateway.writes[0].kind is ActionKind.DELETE_DYN
    assert gateway.writes[0].dyn_id == "mine-1"  # 删的是我那条，不是活动那条

    items = repos.tidy.items_of(plan_id)
    assert items[0]["state"] == "done"
    assert items[0]["target"] == "mine-1"
    assert repos.tidy.state_of(plan_id) == "done"


def test_missing_share_is_skipped_not_guessed(
    repos: Repositories, gateway, clock: _Clock, account: int
) -> None:
    gateway.share = None
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)

    report = executor.run(plan_id, apply=True)
    assert report.skipped == 1
    assert gateway.writes == []
    assert "没找到" in repos.tidy.items_of(plan_id)[0]["reason"]


def test_someone_elses_share_is_skipped(
    repos: Repositories, gateway, clock: _Clock, account: int
) -> None:
    gateway.share = ShareRef(dyn_id="other-1", author_uid="2002", orig_dyn_id="d1")
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)

    report = executor.run(plan_id, apply=True)
    assert report.skipped == 1
    assert gateway.writes == []


def test_share_lookup_failure_skips_instead_of_writing(
    repos: Repositories, gateway, clock: _Clock, account: int
) -> None:
    gateway.share_lookup_fails = True
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)

    report = executor.run(plan_id, apply=True)
    assert report.skipped == 1
    assert gateway.writes == []


def test_already_gone_counts_as_done(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    """探测说"这条已经没了"，就是已完成 —— 不该报失败，也不该再发一次删除。"""
    gateway.share = mine
    gateway.probe = ProbeState.PRESENT
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)

    report = executor.run(plan_id, apply=True)
    assert report.done == 1
    assert gateway.writes == []


# ---- 随时能停 ----
def test_unread_message_blocks_before_any_write(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    gateway.share = mine
    gateway.unread_stat = MessageStat(chat=1)
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)

    with pytest.raises(TidyBlocked):
        executor.run(plan_id, apply=True)
    assert gateway.writes == []
    assert repos.tidy.state_of(plan_id) == "confirmed"


def test_unreadable_messages_block(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    gateway.share = mine
    gateway.unread_fails = True
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)

    with pytest.raises(TidyBlocked):
        executor.run(plan_id, apply=True)
    assert gateway.writes == []


def test_paused_gate_blocks(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    gateway.share = mine
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)

    gate = CleaningGate(
        gateway=gateway, intents=repos.intents, settings=repos.settings, clock=clock
    )
    gate.pause(account, "手工停下")

    with pytest.raises(TidyBlocked):
        executor.run(plan_id, apply=True)
    assert gateway.writes == []


def test_unknown_result_stops_the_whole_run(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    """第一次写就"不知道结果"时，第二条绝不能继续 —— 现场已经不清楚了。"""
    gateway.share = mine
    gateway.receipt = Receipt.unclear("请求已发出但没有响应")
    plan_id = _plan(repos, account, dyn_ids=("d1", "d2"))
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)

    report = executor.run(plan_id, apply=True)
    assert report.failed == 1
    assert len(gateway.writes) == 1
    assert repos.tidy.state_of(plan_id) == "aborted"
    pending = repos.tidy.pending_items(plan_id)
    assert len(pending) == 1


def test_risk_rejection_pauses_the_gate(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    gateway.share = mine
    gateway.receipt = Receipt.rejected(Fault.RISK, "风控校验失败")
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)

    report = executor.run(plan_id, apply=True)
    assert report.failed == 1

    gate = CleaningGate(
        gateway=gateway, intents=repos.intents, settings=repos.settings, clock=clock
    )
    assert gate.is_paused(account) is True


# ---- 取关与回滚 ----
def test_unfollow_records_a_rollback(
    repos: Repositories, gateway, clock: _Clock, account: int
) -> None:
    plan_id = _plan(repos, account, action="unfollow")
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)

    report = executor.run(plan_id, apply=True)
    assert report.done == 1
    assert gateway.writes[0].kind is ActionKind.UNFOLLOW

    item_id = int(repos.tidy.items_of(plan_id)[0]["id"])
    record = repos.tidy.undo_of(item_id)
    assert record is not None
    assert record["action"] == "follow_back"
    assert record["used_at"] is None


def test_undo_follows_back(repos: Repositories, gateway, clock: _Clock, account: int) -> None:
    plan_id = _plan(repos, account, action="unfollow")
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)
    executor.run(plan_id, apply=True)

    item_id = int(repos.tidy.items_of(plan_id)[0]["id"])
    gateway.writes.clear()
    gateway.probe = ProbeState.ABSENT

    result = executor.undo(item_id)
    assert result.ok is True
    assert gateway.writes[0].kind is ActionKind.FOLLOW
    assert repos.tidy.undo_of(item_id)["used_at"] is not None  # type: ignore[index]

    again = executor.undo(item_id)
    assert again.ok is False
    assert "用过了" in again.message


def test_undo_without_record_is_refused(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    """删转发没有后悔药。这条要明确说出来，而不是默默成功。"""
    gateway.share = mine
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)
    executor.run(plan_id, apply=True)

    item_id = int(repos.tidy.items_of(plan_id)[0]["id"])
    result = executor.undo(item_id)
    assert result.ok is False
    assert "回滚记录" in result.message


# ---- 逐条勾选 ----
def _ids(repos: Repositories, plan_id: int) -> list[int]:
    return [int(row["id"]) for row in repos.tidy.items_of(plan_id)]  # type: ignore[arg-type]


def test_deselected_items_are_not_executed(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    """勾掉的那几条一个请求都不该发出去。这是界面上"我只要这两条"的全部含义。"""
    gateway.share = mine
    plan_id = _plan(repos, account, dyn_ids=("d1", "d2", "d3"))
    executor = _executor(repos, gateway, clock)
    keep = _ids(repos, plan_id)[:1]

    report = executor.select(plan_id, keep)
    assert report.selected == 1
    assert report.dropped == 2

    executor.confirm(plan_id)
    outcome = executor.run(plan_id, apply=True)
    assert len(gateway.writes) == 1
    assert outcome.done == 1
    assert outcome.failed == 0
    assert repos.tidy.state_of(plan_id) == "done"


def test_select_is_a_set_not_a_delta(
    repos: Repositories, gateway, clock: _Clock, account: int
) -> None:
    """传全集：先全选、再只选一条，结果就是一条，不是"三条减两次"。"""
    plan_id = _plan(repos, account, dyn_ids=("d1", "d2"))
    executor = _executor(repos, gateway, clock)
    ids = _ids(repos, plan_id)

    executor.select(plan_id, ids)
    assert repos.tidy.counts_of(plan_id).get("pending") == 2

    report = executor.select(plan_id, ids[:1])
    assert report.selected == 1
    assert report.dropped == 1
    assert report.revived == 0


def test_reselect_revives_a_dropped_item(
    repos: Repositories, gateway, clock: _Clock, account: int
) -> None:
    """去掉又勾回来要能恢复。做不到的话，一次手滑就得重新预演。"""
    plan_id = _plan(repos, account, dyn_ids=("d1", "d2"))
    executor = _executor(repos, gateway, clock)
    ids = _ids(repos, plan_id)

    executor.select(plan_id, [])
    assert repos.tidy.counts_of(plan_id).get("dropped") == 2

    report = executor.select(plan_id, ids)
    assert report.revived == 2
    assert repos.tidy.counts_of(plan_id).get("pending") == 2
    assert repos.tidy.items_of(plan_id)[0]["reason"] == ""


def test_select_after_confirm_is_refused(
    repos: Repositories, gateway, clock: _Clock, account: int
) -> None:
    """确认之后清单必须锁死，否则"确认时看到 5 条、执行 8 条"就成立了。"""
    plan_id = _plan(repos, account, dyn_ids=("d1", "d2"))
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)
    with pytest.raises(TidyRejected):
        executor.select(plan_id, [])


def test_select_ignores_unknown_and_finished_items(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    """已执行过的条目不能被"重新勾选"改写状态，那是事后取证。"""
    gateway.share = mine
    plan_id = _plan(repos, account, dyn_ids=("d1",))
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)
    executor.run(plan_id, apply=True)

    done_id = _ids(repos, plan_id)[0]
    plan2 = _plan(repos, account, dyn_ids=("d1",))
    report = executor.select(plan2, [done_id, 999999])
    # 传的是全集：剩下的那条不在集合里，于是被取消；陌生 ID 不产生任何影响。
    assert report.selected == 0
    assert report.dropped == 1
    assert repos.tidy.items_of(plan_id)[0]["state"] == "done"


def test_empty_selection_still_closes_the_plan(
    repos: Repositories, gateway, clock: _Clock, account: int
) -> None:
    """全部勾掉再点执行，计划要收尾。停在 confirmed 会让人以为按钮坏了。"""
    plan_id = _plan(repos, account, dyn_ids=("d1",))
    executor = _executor(repos, gateway, clock)
    executor.select(plan_id, [])
    executor.confirm(plan_id)

    report = executor.run(plan_id, apply=True)
    assert report.done == 0
    assert gateway.writes == []
    assert repos.tidy.state_of(plan_id) == "done"
    assert "没有勾选" in report.note


def test_recent_plans_carry_counts(
    repos: Repositories, gateway, clock: _Clock, account: int
) -> None:
    plan_id = _plan(repos, account, dyn_ids=("d1", "d2"))
    _executor(repos, gateway, clock).select(plan_id, _ids(repos, plan_id)[:1])

    rows = repos.tidy.recent_plans(account)
    assert rows[0]["id"] == plan_id
    assert rows[0]["total"] == 2
    assert rows[0]["pending"] == 1
    assert rows[0]["state"] == "previewed"


# ---- 执行前备份 ----
def test_apply_takes_one_snapshot_before_writing(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    """删转发回不来，所以执行前必须留一份"当时库长什么样"。"""
    gateway.share = mine
    plan_id = _plan(repos, account)
    taken: list[str] = []

    def snapshot() -> str:
        taken.append("shot")
        return "/tmp/tobili-shot.db"

    executor = _executor(repos, gateway, clock, snapshot=snapshot)
    executor.confirm(plan_id)
    report = executor.run(plan_id, apply=True)

    assert taken == ["shot"]
    assert report.snapshot == "/tmp/tobili-shot.db"


def test_dry_run_takes_no_snapshot(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    # 预演不写任何东西，没必要占一份备份；界面上也不该显示"已备份"让人误会。
    gateway.share = mine
    plan_id = _plan(repos, account)
    taken: list[str] = []
    executor = _executor(repos, gateway, clock, snapshot=lambda: taken.append("x") or "p")
    executor.confirm(plan_id)

    report = executor.run(plan_id, apply=False)

    assert taken == []
    assert report.snapshot == ""


def test_snapshot_failure_stops_before_any_write(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    """备份不成功就别动手。宁可这次不清理，也不能删完才发现没有凭据。"""

    def boom() -> str:
        raise OSError("磁盘满了")

    gateway.share = mine
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock, snapshot=boom)
    executor.confirm(plan_id)

    with pytest.raises(TidyRejected) as caught:
        executor.run(plan_id, apply=True)

    assert "备份失败" in str(caught.value)
    assert "磁盘满了" in str(caught.value)
    assert gateway.writes == []
    # 计划不能停在 running：那会让下一次执行以为"上一轮还在跑"。
    assert repos.tidy.state_of(plan_id) == "confirmed"


def test_without_a_snapshot_hook_nothing_changes(
    repos: Repositories, gateway, clock: _Clock, account: int, mine: ShareRef
) -> None:
    # CLI 之外的调用方（测试、脚本）不传 snapshot 也得能跑，不能强制依赖。
    gateway.share = mine
    plan_id = _plan(repos, account)
    executor = _executor(repos, gateway, clock)
    executor.confirm(plan_id)
    assert executor.run(plan_id, apply=True).snapshot == ""
