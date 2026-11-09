"""预选池的排序与状态规则。

这些规则都是"人际交互"层面的，不是算法：挪到第一位、跑完的自动消失、一条失败
整队停下。写错了不会崩，只会让人觉得"这东西不听我的话"。
"""

from __future__ import annotations

import pytest

from tobili.domain.pool import (
    POOL_LIMIT,
    PoolItem,
    PoolOp,
    PoolRejected,
    PoolState,
    apply_op,
    renumber,
    summarize,
    waiting,
)


def _item(cid: int, position: int = 0, state: PoolState = PoolState.PENDING) -> PoolItem:
    return PoolItem(campaign_id=cid, position=position, state=state)


def _ids(rows: list[PoolItem]) -> list[int]:
    return [row.campaign_id for row in rows]


def test_add_appends_in_the_given_order() -> None:
    rows = apply_op([], PoolOp.ADD, campaign_ids=[7, 3, 9])
    assert _ids(rows) == [7, 3, 9]
    assert [row.position for row in rows] == [0, 1, 2]


def test_add_skips_duplicates() -> None:
    rows = apply_op([_item(7)], PoolOp.ADD, campaign_ids=[7, 9])
    assert _ids(rows) == [7, 9]


def test_add_rejects_when_nothing_new() -> None:
    """全都已经在池子里时要说一句，否则点了"加入"什么都不发生，像按钮坏了。"""
    with pytest.raises(PoolRejected, match="都已经在预选池里"):
        apply_op([_item(7)], PoolOp.ADD, campaign_ids=[7])


def test_add_asks_the_caller_whether_it_is_allowed() -> None:
    def check(cid: int) -> str:
        return "已经截止了" if cid == 5 else ""

    with pytest.raises(PoolRejected, match="已经截止了"):
        apply_op([], PoolOp.ADD, campaign_ids=[5], check=check)
    # 被拒时一个都不该落进去，不能"加了一半然后报错"。
    rows = apply_op([], PoolOp.ADD, campaign_ids=[7], check=check)
    assert _ids(rows) == [7]


def test_add_respects_the_limit() -> None:
    rows = [_item(cid, cid) for cid in range(POOL_LIMIT)]
    with pytest.raises(PoolRejected, match=str(POOL_LIMIT)):
        apply_op(rows, PoolOp.ADD, campaign_ids=[9999])


def test_remove_keeps_the_order() -> None:
    rows = apply_op([_item(1), _item(2), _item(3)], PoolOp.REMOVE, campaign_id=2)
    assert _ids(rows) == [1, 3]
    assert [row.position for row in rows] == [0, 1]


def test_remove_missing_is_rejected() -> None:
    with pytest.raises(PoolRejected, match="不在预选池里"):
        apply_op([_item(1)], PoolOp.REMOVE, campaign_id=2)


def test_move_up_and_down() -> None:
    rows = [_item(1), _item(2), _item(3)]
    assert _ids(apply_op(rows, PoolOp.MOVE, campaign_id=3, direction=-1)) == [1, 3, 2]
    assert _ids(apply_op(rows, PoolOp.MOVE, campaign_id=1, direction=1)) == [2, 1, 3]


def test_move_at_the_edge_does_nothing() -> None:
    """连点两下"上移"不该弹红条。到头了就是到头了。"""
    rows = [_item(1), _item(2)]
    assert _ids(apply_op(rows, PoolOp.MOVE, campaign_id=1, direction=-1)) == [1, 2]
    assert _ids(apply_op(rows, PoolOp.MOVE, campaign_id=2, direction=1)) == [1, 2]


def test_move_rejects_bad_direction() -> None:
    with pytest.raises(PoolRejected, match="方向"):
        apply_op([_item(1)], PoolOp.MOVE, campaign_id=1, direction=0)


def test_reorder_never_leaves_gaps() -> None:
    """位置必须连续。留空洞的话"第 3 个"和下标对不上，下次移动就挪错行。"""
    rows = renumber([_item(5, 0), _item(6, 7), _item(7, 9)])
    assert [row.position for row in rows] == [0, 1, 2]


def test_retry_brings_failures_back() -> None:
    rows = [
        _item(1, 0, PoolState.FAILED),
        _item(2, 1, PoolState.PENDING),
        _item(3, 2, PoolState.FAILED),
    ]
    rows[0] = PoolItem(campaign_id=1, position=0, state=PoolState.FAILED, message="网络错误")
    after = apply_op(rows, PoolOp.RETRY)
    assert [row.state for row in after] == [PoolState.PENDING] * 3
    assert all(row.message == "" for row in after)


def test_retry_without_failures_is_rejected() -> None:
    with pytest.raises(PoolRejected, match="没有失败"):
        apply_op([_item(1)], PoolOp.RETRY)


def test_clear_done_drops_finished_only() -> None:
    rows = [
        _item(1, 0, PoolState.SUCCESS),
        _item(2, 1, PoolState.SKIPPED),
        _item(3, 2, PoolState.FAILED),
        _item(4, 3, PoolState.PENDING),
    ]
    assert _ids(apply_op(rows, PoolOp.CLEAR_DONE)) == [3, 4]


def test_pause_and_resume_do_not_touch_rows() -> None:
    rows = [_item(1), _item(2)]
    assert _ids(apply_op(rows, PoolOp.PAUSE)) == [1, 2]
    assert _ids(apply_op(rows, PoolOp.RESUME)) == [1, 2]


def test_waiting_is_ordered_and_pending_only() -> None:
    rows = [
        PoolItem(campaign_id=9, position=2),
        PoolItem(campaign_id=4, position=0, state=PoolState.FAILED),
        PoolItem(campaign_id=5, position=1),
    ]
    assert _ids(waiting(rows)) == [5, 9]


def test_summarize_counts_every_state() -> None:
    counts = summarize([_item(1), _item(2, 1, PoolState.FAILED)])
    assert counts["pending"] == 1
    assert counts["failed"] == 1
    assert counts["success"] == 0
    assert set(counts) == {"pending", "running", "success", "skipped", "failed", "review"}


def test_review_requires_targeted_manual_retry():
    rows=[PoolItem(1,0,PoolState.REVIEW,'评论状态无法确认'),PoolItem(2,1,PoolState.REVIEW,'需要人工参与'),PoolItem(3,2)]
    assert _ids(waiting(rows))==[3]
    changed=apply_op(rows,PoolOp.RETRY,campaign_id=1)
    assert _ids(waiting(changed))==[1,3]
    assert changed[0].message=='' and changed[1]==rows[1]


def test_review_does_not_occupy_pending_capacity():
    rows=[PoolItem(i,i,PoolState.REVIEW,'待核对') for i in range(POOL_LIMIT)]
    assert apply_op(rows,PoolOp.ADD,campaign_ids=[999])[-1].waiting


def test_unknown_operation_is_rejected() -> None:
    with pytest.raises(PoolRejected):
        apply_op([], "frobnicate")  # type: ignore[arg-type]
