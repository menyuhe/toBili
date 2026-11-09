"""预选池：一个有顺序的待参与队列。

它和 `enrollment` 不是一回事，这一点值得先说清楚：

- `enrollment` 是**账**："这个活动我报了，现在到哪一步了。"
- 预选池是**意图**："我打算按这个顺序报这几条。"

所以成功的行会从池里消失（账已经记到 enrollment 里了），失败的留着 ——
否则"暂停之后还能看到是哪条卡住了"这件事就没了。

三个刻意的规则：

1. **顺序是人工的，不按截止时间重排。** 自动排序是扫描的事；人手把某一条挪到
   第一位，是因为他知道那条今天开奖。系统不要在这种事上自作聪明。
2. **单条证据不足转入待处理。** 保留原因、不占待参与容量；
   账号或平台级异常仍暂停队列，避免把同一个错误继续扩散。
3. **只读历史核查可以续查。** 每轮最多续查三条，证据完整才继续原参与；
   已发送但结果未知、业务拒绝和人工任务仍须手动处理，不自动重发。

本模块是纯函数：读进来一批行，算出新的一批。落库在 `PoolRepo`，跑在 `RunPool`。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

# 200 和专栏、批量参与用的是同一个数量级。再多也不会有人一条条看。
POOL_LIMIT = 200


class PoolState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"
    REVIEW = "review"


# 这两个状态意味着"这条已经不需要再动了"。跑完就删，不留历史 ——
# 历史在 enrollment 和运行记录里，池子只放还没办的事。
FINISHED = frozenset({PoolState.SUCCESS, PoolState.SKIPPED})


class PoolOp(StrEnum):
    ADD = "add"
    REMOVE = "remove"
    MOVE = "move"
    RETRY = "retry"
    CLEAR_DONE = "clear_done"
    PAUSE = "pause"
    RESUME = "resume"


class PoolRejected(ValueError):
    """操作不合法。文案直接给人看。"""


@dataclass(frozen=True, slots=True)
class PoolItem:
    campaign_id: int
    position: int = 0
    state: PoolState = PoolState.PENDING
    message: str = ""

    @property
    def waiting(self) -> bool:
        return self.state is PoolState.PENDING


def renumber(rows: Sequence[PoolItem]) -> list[PoolItem]:
    """位置永远连续地从 0 开始。

    挪动之后如果留着空洞，界面上的"第 3 个"和数组下标就对不上，
    下一次移动就会挪错行。
    """
    return [replace(row, position=index) for index, row in enumerate(rows)]


def apply_op(
    rows: Sequence[PoolItem],
    op: PoolOp,
    *,
    campaign_ids: Sequence[int] = (),
    campaign_id: int = 0,
    direction: int = 0,
    check: Callable[[int], str] | None = None,
) -> list[PoolItem]:
    """算出一个新队列。不落库、不改传进来的东西。

    `check` 是"这条能不能加"的判断（活动还在不在、参没参加过、截没截止）。
    它由调用方给，因为这个模块不该知道活动长什么样。
    """
    items = list(rows)

    if op is PoolOp.ADD:
        known = {row.campaign_id for row in items}
        added = 0
        for cid in campaign_ids:
            if cid in known:
                continue
            reason = check(cid) if check else ""
            if reason:
                raise PoolRejected(reason)
            items.append(PoolItem(campaign_id=cid))
            known.add(cid)
            added += 1
        if sum(row.waiting for row in items) > POOL_LIMIT:
            raise PoolRejected(f"预选池最多 {POOL_LIMIT} 条，请先清理已完成项")
        if not added and campaign_ids:
            raise PoolRejected("这些活动都已经在预选池里了")
    elif op is PoolOp.REMOVE:
        if not any(row.campaign_id == campaign_id for row in items):
            raise PoolRejected("这条不在预选池里")
        items = [row for row in items if row.campaign_id != campaign_id]
    elif op is PoolOp.MOVE:
        if direction not in (-1, 1):
            raise PoolRejected("移动方向无效")
        index = next((i for i, row in enumerate(items) if row.campaign_id == campaign_id), -1)
        if index < 0:
            raise PoolRejected("这条不在预选池里")
        target = index + direction
        # 到头了就当无事发生，不报错 —— 连点几下"上移"不该弹一个红条。
        if 0 <= target < len(items):
            items[index], items[target] = items[target], items[index]
        return renumber(items)
    elif op is PoolOp.RETRY:
        retryable = lambda row: (row.campaign_id == campaign_id and row.state in (PoolState.FAILED, PoolState.REVIEW)) if campaign_id else row.state is PoolState.FAILED
        if not any(retryable(row) for row in items):
            raise PoolRejected("没有失败的活动需要重试")
        if campaign_id and check and (reason := check(campaign_id)):
            raise PoolRejected(reason)
        items = [
            replace(row, state=PoolState.PENDING, message="")
            if retryable(row)
            else row
            for row in items
        ]
    elif op is PoolOp.CLEAR_DONE:
        items = [row for row in items if row.state not in FINISHED]
    elif op in (PoolOp.PAUSE, PoolOp.RESUME):
        return renumber(items)
    else:  # pragma: no cover - 枚举走不到这里
        raise PoolRejected("不支持的预选操作")

    return renumber(items)


def waiting(rows: Sequence[PoolItem]) -> list[PoolItem]:
    """待参与的，按人工排好的顺序。"""
    return [row for row in sorted(rows, key=lambda r: r.position) if row.waiting]


def summarize(rows: Sequence[PoolItem]) -> dict[str, int]:
    counts = {state.value: 0 for state in PoolState}
    for row in rows:
        counts[row.state.value] += 1
    return counts
