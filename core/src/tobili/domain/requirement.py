"""参与要求：一个活动要求你做什么。

拆成独立的值对象，而不是塞进一个 JSON blob，是为了让"这条活动到底要做什么"
可以被查询、被预览、被权限控制——预演界面上那一行行复选框就来自这里。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

from .action import ActionKind


@dataclass(frozen=True, slots=True)
class Requirement:
    action: ActionKind
    payload: Mapping[str, str] = field(default_factory=dict)
    optional: bool = False

    def __post_init__(self) -> None:
        if self.action is ActionKind.COMMENT and not self.payload.get("text"):
            raise ValueError("评论类要求必须带正文")
        if self.action is ActionKind.RESERVE and not self.payload.get("reserve_id"):
            raise ValueError("预约类要求必须带预约 ID")


@dataclass(frozen=True, slots=True)
class RequirementSet:
    items: tuple[Requirement, ...] = ()

    def __iter__(self) -> Iterator[Requirement]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    @property
    def required(self) -> tuple[Requirement, ...]:
        return tuple(r for r in self.items if not r.optional)

    def kinds(self) -> tuple[ActionKind, ...]:
        return tuple(r.action for r in self.items)

    def merge(self, *more: Requirement) -> RequirementSet:
        """按 (动作, 正文) 去重后追加，保持原有顺序。"""
        seen = {(r.action, r.payload.get("text", "")) for r in self.items}
        added = [r for r in more if (r.action, r.payload.get("text", "")) not in seen]
        return RequirementSet(self.items + tuple(added))

    def blocking(self) -> tuple[ActionKind, ...]:
        """必须做、且不可撤销的动作 —— 清理阶段要重点保护的对象。"""
        return tuple(r.action for r in self.required if r.action.undo_kind is None)
