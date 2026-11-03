"""Versioned, credential-free wire contract shared by relay and desktop."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROTOCOL_VERSION = 1
REMOTE_ACTIONS = {"participate", "refresh_status", "maintenance_check", "maintenance_clean"}
LOCAL_ACTIONS = REMOTE_ACTIONS | {
    "participate_pool", "refresh_all", "refresh_source", "refresh_watch",
    "refresh_discovery", "refresh_discovery_history", "maintenance_cycle", "maintenance_link",
    "maintenance_discover", "maintenance_discover_all", "ticket_run",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Activity(StrictModel):
    dynamic_id: str = Field(pattern=r"^[1-9][0-9]{0,24}$")
    title: str = Field(default="", max_length=1000)
    lottery_type: str = Field(default="", max_length=30)
    lottery_time: int | None = None
    sender_uid: str = Field(default="", pattern=r"^(|[1-9][0-9]{0,19})$")
    business_id: str = Field(default="", max_length=32)
    business_type: str = Field(default="", max_length=10)
    activity_status: str = Field(default="", max_length=30)
    draw_status: str = Field(default="", max_length=30)


class Participation(StrictModel):
    user_status: Literal["已参加", "未参加"]
    updated_at: int = 0


class Link(StrictModel):
    up_uid: str = Field(default="", pattern=r"^(|[1-9][0-9]{0,19})$")
    repost_id: str = Field(default="", pattern=r"^(|[1-9][0-9]{0,24})$")
    followed_before: bool | None = None
    follow_added: bool = False
    origin: Literal["new", "confirmed", "historical"] = "historical"
    repost_recorded: bool = False
    created_at: int = 0


class Outcome(StrictModel):
    outcome: Literal["unknown", "pending", "won", "lost"] = "unknown"
    source: Literal["none", "official", "manual"] = "none"
    checked_at: int = 0
    lost_at: int = 0
    reason: str = Field(default="", max_length=500)


class Keep(StrictModel):
    keep: bool = False


class CleanupSettings(StrictModel):
    enabled: bool = False
    delete_reposts: bool = False
    unfollow: bool = False


class ProtectedUp(StrictModel):
    protected: bool = True


class CleanupStep(StrictModel):
    dynamic_id: str = Field(pattern=r"^[1-9][0-9]{0,24}$")
    state: Literal["pending", "running", "done", "unknown", "blocked"]
    attempts: int = 0
    updated_at: int = 0
    message: str = Field(default="", max_length=500)


class Archive(StrictModel):
    archived_at: int = 0


class Selection(StrictModel):
    ids: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def valid_ids(self):
        import re
        if len(set(self.ids)) != len(self.ids) or any(not re.fullmatch(r"[1-9][0-9]{0,24}", did) for did in self.ids):
            raise ValueError("预选活动标识无效或重复")
        return self


class ParticipationSettings(StrictModel):
    participate_text: str | None = Field(default=None, max_length=233)
    participate_fallback_text: str | None = Field(default=None, max_length=233)
    participate_text_mode: Literal["custom", "random_comment"] | None = None


PAYLOADS = {"activity": Activity, "participation": Participation, "link": Link, "outcome": Outcome,
            "keep": Keep, "cleanup_settings": CleanupSettings, "protected_up": ProtectedUp,
            "cleanup_step": CleanupStep, "archive": Archive, "selection": Selection,
            "participation_settings": ParticipationSettings}


class Change(StrictModel):
    change_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    kind: str
    key: str = Field(min_length=1, max_length=64)
    base_version: int = Field(ge=0)
    deleted: bool = False
    value: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_payload(self):
        import re
        if self.kind not in PAYLOADS:
            raise ValueError("不支持的同步类型")
        pattern = r"^(delete|unfollow):[1-9][0-9]{0,24}$" if self.kind == "cleanup_step" else r"^[1-9][0-9]{0,24}$"
        if self.kind in {"cleanup_settings", "selection", "participation_settings"}:
            if self.key != "main":
                raise ValueError("设置键无效")
        elif not re.fullmatch(pattern, self.key):
            raise ValueError("同步键无效")
        if self.deleted:
            if self.kind not in {"archive", "protected_up"} or self.value:
                raise ValueError("不能删除参与及清理历史")
        else:
            self.value = PAYLOADS[self.kind].model_validate(self.value).model_dump()
            if self.kind == "activity" and self.value["dynamic_id"] != self.key:
                raise ValueError("活动标识不匹配")
        return self


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def task_params(action: str, params: dict, *, remote: bool) -> dict:
    import re
    if action not in (REMOTE_ACTIONS if remote else LOCAL_ACTIONS):
        raise ValueError("不支持的任务类型")
    allowed = {"dynamic_id"} if remote and action == "participate" else set() if remote else {
        "dynamic_id", "source_id", "uid", "mode", "up_uid", "repost_id", "follow_added", "force", "limit",
    }
    if set(params) - allowed or len(canonical(params)) > 2000:
        raise ValueError("任务参数无效")
    for key, value in params.items():
        if key in {"dynamic_id", "uid", "up_uid", "repost_id"}:
            if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,24}", value):
                if key != "repost_id" or value != "":
                    raise ValueError("任务标识无效")
        elif key in {"force", "follow_added"} and type(value) is not bool:
            raise ValueError("任务开关无效")
        elif key == "limit" and (type(value) is not int or not 0 <= value <= 10000):
            raise ValueError("任务数量无效")
        elif key == "source_id" and (not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value)):
            raise ValueError("活动来源标识无效")
        elif key == "mode" and (not isinstance(value, str) or value not in {"recent", "continue"}):
            raise ValueError("扫描模式无效")
    if action == "participate":
        if not re.fullmatch(r"[1-9][0-9]{0,24}", str(params.get("dynamic_id", ""))):
            raise ValueError("缺少有效的活动 ID")
    return params
