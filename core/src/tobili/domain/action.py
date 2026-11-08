"""动作：参与一个活动需要做的具体事情。

`content_hash` 是整个幂等体系的地基。它的语义是：**同一条动态上、同一个动作、
同样的内容**，就只能存在一次。重试、重启、重复点击都会被这条约束吃掉。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class ActionKind(StrEnum):
    FOLLOW = "follow"
    SHARE = "share"
    COMMENT = "comment"
    LIKE = "like"
    FAVORITE = "favorite"
    RESERVE = "reserve"
    # 清理用的两个动作。它们和上面六个的方向相反：上面是"把事做上"，
    # 下面是"把事撤掉"。放在同一个枚举里，是为了共用同一套幂等与探测。
    UNFOLLOW = "unfollow"
    DELETE_DYN = "delete_dyn"

    @property
    def changes_remote(self) -> bool:
        """全部为真。保留这个属性是为了让调用方显式表达"我知道这是写操作"。"""
        return True

    @property
    def undo_kind(self) -> ActionKind | None:
        """可撤销的动作能回到原状；转发和评论撤回成本高，登记为不可撤销。

        取关的撤销是**重新关注**（方向相反，不是同一个 kind）；删转发没有撤销，
        删掉就是删掉 —— 界面必须按这个属性决定要不要显示"后悔药"。
        """
        if self is ActionKind.LIKE:
            return ActionKind.LIKE
        if self is ActionKind.FAVORITE:
            return ActionKind.FAVORITE
        if self is ActionKind.FOLLOW:
            return ActionKind.FOLLOW
        if self is ActionKind.RESERVE:
            return ActionKind.RESERVE
        if self is ActionKind.UNFOLLOW:
            return ActionKind.FOLLOW
        return None

    @property
    def destructive(self) -> bool:
        """不可恢复的动作。执行前必须过闸门，且不允许批量重试。"""
        return self is ActionKind.DELETE_DYN or self is ActionKind.UNFOLLOW

    @property
    def needs_body(self) -> bool:
        return self is ActionKind.COMMENT or self is ActionKind.SHARE


def digest(kind: ActionKind, dyn_id: str, payload: Mapping[str, str]) -> str:
    """动作指纹。相同输入必然相同输出，与进程、时间、顺序无关。"""
    material = json.dumps(
        {
            "action": kind.value,
            "target": str(dyn_id),
            "payload": {k: str(v) for k, v in sorted(payload.items())},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class Action:
    kind: ActionKind
    dyn_id: str
    payload: Mapping[str, str] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return digest(self.kind, self.dyn_id, self.payload)

    def with_payload(self, **extra: str) -> Action:
        return Action(self.kind, self.dyn_id, {**self.payload, **extra})
