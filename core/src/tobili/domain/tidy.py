"""开奖结果与清理资格。

清理是唯一会造成**不可恢复损失**的功能：转发删了就是删了，取关之后对方可能再也
不会回关。所以这里把规则写成纯函数，并且允许调用方传入"当前时间"，让每一条规则
都能在没有数据库、没有网络的条件下被单独验证。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Outcome(StrEnum):
    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    VOID = "void"


class TidyAction(StrEnum):
    DROP_SHARE = "drop_share"
    UNFOLLOW = "unfollow"


@dataclass(frozen=True, slots=True)
class TidyPolicy:
    """保守默认值。任何一项被调松，都应该在界面上要求二次确认。"""

    grace_hours: int = 24
    freeze_days: int = 90
    max_per_round: int = 20
    max_per_up: int = 3


@dataclass(frozen=True, slots=True)
class TidyCandidate:
    enrollment_id: int
    dyn_id: str
    up_uid: str
    outcome: Outcome
    decided_at: datetime | None
    already_cleaned: bool = False


class KeepKind(StrEnum):
    """保护名单的两种粒度。

    按 UP 保护是常用的那个：某个人发的东西就算没中奖也想留着。按活动保护只在
    "这一条特别重要"时用，两个都命中时按 UP 优先，理由也更具体。
    """

    UP = "up"
    CAMPAIGN = "campaign"


@dataclass(frozen=True, slots=True)
class KeepEntry:
    kind: KeepKind
    value: str
    note: str = ""

    def label(self) -> str:
        """给预演清单看的一句话。名单里的备注优先，没有就写明是按什么保护的。"""
        if self.note:
            return self.note
        return f"按 UP 保护 {self.value}" if self.kind is KeepKind.UP else "按活动保护"


def keep_hit(entries: list[KeepEntry], *, up_uid: str, dyn_id: str) -> KeepEntry | None:
    """这一条命中了哪条保护。

    UP 优先于活动：同一号人有多条活动时，说"这个 UP 在你的名单里"比"这条活动在
    名单里"更能解释清楚为什么没清。
    """
    for entry in entries:
        if entry.kind is KeepKind.UP and entry.value == up_uid:
            return entry
    for entry in entries:
        if entry.kind is KeepKind.CAMPAIGN and entry.value == dyn_id:
            return entry
    return None


@dataclass(frozen=True, slots=True)
class Judgement:
    allowed: bool
    reason: str
    action: TidyAction | None = None

    def __bool__(self) -> bool:
        return self.allowed


def judge(
    candidate: TidyCandidate,
    *,
    now: datetime,
    policy: TidyPolicy,
    up_deleted_in_round: int = 0,
    action: TidyAction = TidyAction.DROP_SHARE,
    kept: str = "",
) -> Judgement:
    """能否清理这一条。每个否定分支都带一句人话理由，供预演页面直接展示。

    `kept` 是保护名单命中时的人话说明（空字符串表示没命中）。名单放在最前面判断：
    它的语义是"无论条件多符合都不清"，比宽限期、上限这些更像前置门槛。
    """
    if candidate.already_cleaned:
        return Judgement(False, "已经清理过")
    if kept:
        return Judgement(False, f"在保护名单里（{kept}），不会被清理")
    if candidate.outcome is not Outcome.LOST:
        return Judgement(False, "结果不是未中奖，保持原样")
    if candidate.decided_at is None:
        return Judgement(False, "缺少判定时间，无法计算宽限期")

    age = now - candidate.decided_at
    if age < timedelta(hours=policy.grace_hours):
        remain = policy.grace_hours - age.total_seconds() / 3600
        return Judgement(False, f"宽限期内，还需等待 {remain:.1f} 小时")
    if age > timedelta(days=policy.freeze_days):
        return Judgement(
            False,
            f"记录超过 {policy.freeze_days} 天，已冻结；如需清理请手动解冻",
        )
    if up_deleted_in_round >= policy.max_per_up:
        return Judgement(
            False,
            f"同一 UP 本轮已清理 {up_deleted_in_round} 条，达到上限 {policy.max_per_up}",
        )
    return Judgement(True, "符合清理条件", action)


def plan_order(candidates: list[TidyCandidate]) -> list[TidyCandidate]:
    """先删更早的。同一 UP 的连续记录被拆散，避免一次操作里对同一个人反复动手。"""
    return sorted(candidates, key=lambda c: (c.decided_at or datetime.min, c.enrollment_id))


# ---- 计划状态机 ----
class PlanState(StrEnum):
    DRAFT = "draft"
    PREVIEWED = "previewed"
    CONFIRMED = "confirmed"
    RUNNING = "running"
    DONE = "done"
    ABORTED = "aborted"


class IllegalTidy(RuntimeError):
    """状态迁移不合法。出现它说明调用顺序错了，不是数据问题。"""


# 合法的迁移。写成表而不是一串 if，是因为"哪些状态能执行"必须是唯一答案：
# 只有 confirmed / running 能执行，previewed 永远不能 —— 预演是给人看的，
# 不是给程序按的。
_ALLOWED: dict[PlanState, frozenset[PlanState]] = {
    PlanState.DRAFT: frozenset({PlanState.PREVIEWED, PlanState.ABORTED}),
    PlanState.PREVIEWED: frozenset({PlanState.CONFIRMED, PlanState.ABORTED}),
    PlanState.CONFIRMED: frozenset({PlanState.RUNNING, PlanState.DONE, PlanState.ABORTED}),
    PlanState.RUNNING: frozenset({PlanState.DONE, PlanState.ABORTED}),
    PlanState.DONE: frozenset(),
    PlanState.ABORTED: frozenset(),
}

_EXECUTABLE = frozenset({PlanState.CONFIRMED, PlanState.RUNNING})


def advance(current: PlanState, target: PlanState) -> PlanState:
    if target not in _ALLOWED[current]:
        raise IllegalTidy(f"计划不能从 {current.value} 变成 {target.value}")
    return target


def executable(state: PlanState) -> bool:
    """能不能动手。`previewed` 不在其中 —— 看过不等于同意。"""
    return state in _EXECUTABLE


def is_closed(state: PlanState) -> bool:
    return state in {PlanState.DONE, PlanState.ABORTED}


# ---- 删除前的归属校验 ----
@dataclass(frozen=True, slots=True)
class ShareRef:
    """我在自己空间里的一条转发。`orig_dyn_id` 指向被转发的原动态。"""

    dyn_id: str
    author_uid: str
    orig_dyn_id: str
    published_at: datetime | None = None


def may_delete_share(ref: ShareRef | None, *, me: str, campaign_dyn_id: str) -> Judgement:
    """这条转发能不能删。

    三个条件缺一不可，且都在本地可验证：是我发的、转的是目标活动、ID 非空。
    删除是不可恢复的，所以宁可判否。这条函数是"别删错东西"的最后一道闸，
    它不信任任何上游返回的"这是我的动态"，只认字段。
    """
    if not campaign_dyn_id:
        return Judgement(False, "不知道要删哪条活动，拒绝执行")
    if ref is None:
        return Judgement(False, "在我的动态里没找到这条转发")
    if not ref.dyn_id:
        return Judgement(False, "转发记录缺少动态 ID")
    if not me:
        return Judgement(False, "没有当前账号身份，无法确认归属")
    if ref.author_uid != me:
        return Judgement(False, f"这条动态属于 {ref.author_uid[:4]}***，不是本人发的")
    if ref.orig_dyn_id != campaign_dyn_id:
        return Judgement(False, "这条转发指向的不是目标活动")
    return Judgement(True, "确认是本人转发该活动", TidyAction.DROP_SHARE)
