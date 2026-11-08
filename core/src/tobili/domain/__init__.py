"""领域层。

这里只有数据类、枚举与纯函数。不 import 网络、存储、系统库，也不读环境变量。
所有规则都可以在测试里直接构造对象验证，不需要 mock。
"""

from .action import Action, ActionKind, digest
from .campaign import Campaign, CampaignKind
from .enrollment import Enrollment, IllegalTransition, Stage
from .errors import AmbiguousWrite, Fault, MissingCredential, RemoteError
from .probe import ProbeState, describe, may_write, merge
from .receipt import Receipt
from .requirement import Requirement, RequirementSet
from .throttle import Budget, Usage, assess
from .throttle import Verdict as BudgetVerdict
from .tidy import (
    Judgement,
    Outcome,
    TidyAction,
    TidyCandidate,
    TidyPolicy,
    judge,
    plan_order,
)

__all__ = [
    "Action",
    "ActionKind",
    "AmbiguousWrite",
    "Budget",
    "BudgetVerdict",
    "Campaign",
    "CampaignKind",
    "Enrollment",
    "Fault",
    "IllegalTransition",
    "Judgement",
    "MissingCredential",
    "Outcome",
    "ProbeState",
    "Receipt",
    "RemoteError",
    "Requirement",
    "RequirementSet",
    "Stage",
    "TidyAction",
    "TidyCandidate",
    "TidyPolicy",
    "Usage",
    "assess",
    "describe",
    "digest",
    "judge",
    "may_write",
    "merge",
    "plan_order",
]
