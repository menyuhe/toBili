"""Independent account task periods, sharing the normal write and account fences."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from ...container import Container
from ...runtime import shared_tasks
from ...runtime.lane import BusyAccount
from ...runtime.taskbook import TASKS
from ..deps import get_container
from .schedule import owner

router = APIRouter(prefix="/api/tasks", tags=["schedule"])
TaskKey = Literal[
    "topics", "collections", "watch", "results", "messages", "participate", "cleanup", "lost_shares"
]


class Period(BaseModel):
    key: TaskKey
    enabled: bool = False
    minutes: int = Field(default=60, ge=5, le=10080)

    @model_validator(mode="after")
    def paced(self):
        if self.key == "lost_shares" and self.minutes > 180:
            raise ValueError("未中奖转发清理间隔最多 180 分钟")
        if self.key in ("topics", "collections", "watch") and self.minutes < 30:
            raise ValueError("来源更新间隔至少 30 分钟")
        return self


class Config(BaseModel):
    account_id: int = Field(gt=0)
    running: bool = False
    apply: bool = False
    confirmed: bool = False
    selection: Literal["queue", "catalog"] = "queue"
    limit: int = Field(default=10, ge=1, le=20)
    hourly_limit: int = Field(default=20, ge=1, le=500)
    tasks: list[Period] = Field(
        min_length=len(TASKS) - len(shared_tasks.COMMON), max_length=len(TASKS)
    )

    @model_validator(mode="after")
    def complete(self):
        if len(self.tasks) == len(TASKS) - 1 and not any(
            t.key == "lost_shares" for t in self.tasks
        ):
            self.tasks.append(Period(key="lost_shares", minutes=30))
        if sorted(t.key for t in self.tasks) not in (
            sorted(TASKS),
            sorted(set(TASKS) - set(shared_tasks.COMMON)),
        ):
            raise ValueError("任务不能重复或缺失")
        if self.running and not any(t.enabled for t in self.tasks):
            raise ValueError("请选择至少一个功能")
        return self


@router.get("")
def view(box: Container = Depends(get_container)):
    return box.tasks.view(owner(box))


@router.put("")
def save(body: Config, box: Container = Depends(get_container)):
    key = owner(box, body.account_id)
    writes = body.running and any(
        t.enabled and (t.key == "lost_shares" or (t.key == "participate" and body.apply))
        for t in body.tasks
    )
    if writes and not body.confirmed:
        raise HTTPException(409, "请确认真实参与的账号、范围与每轮上限")
    if writes and box.check_writer:
        box.check_writer(key)
    shared = shared_tasks.config(box)
    # Older clients submit all eight rows. Import explicitly enabled shared
    # periods while keeping a personal stop or preset scoped to that account.
    if body.running and len(body.tasks) == len(TASKS):
        legacy = {
            t.key: t.model_dump() for t in body.tasks if t.enabled and t.key in shared_tasks.FEEDS
        }
        if legacy:
            with box.tasks.guard:
                shared = shared_tasks.config(box)
                shared_tasks.save(box, [legacy.get(t["key"], t) for t in shared["tasks"]])
    try:
        box.tasks.save(key, body.model_dump(exclude={"account_id", "confirmed"}))
    except BusyAccount as exc:
        raise HTTPException(409, "当前账号仍在执行任务，请稍后保存") from exc
    return box.tasks.view(key)


class Once(BaseModel):
    account_id: int
    key: TaskKey
    selection: Literal["queue", "catalog"] = "queue"
    limit: int = Field(default=10, ge=1, le=20)
    hourly_limit: int = Field(default=20, ge=1, le=500)
    apply: bool = False
    confirmed: bool = False


@router.post("/once")
def once(body: Once, box: Container = Depends(get_container)):
    key = owner(box, body.account_id)
    apply = body.apply and body.key in ("participate", "lost_shares")
    if apply and not body.confirmed:
        raise HTTPException(409, "请确认本次真实参与的账号、范围与上限")
    return box.tasks.run(key, body.key, body.model_dump(), apply=apply)


@router.delete("")
def stop(account_id: int, box: Container = Depends(get_container)):
    key = owner(box, account_id)
    box.tasks.pause(key)
    return box.tasks.view(key)


class SharedConfig(BaseModel):
    tasks: list[Period]

    @model_validator(mode="after")
    def complete(self):
        if sorted(t.key for t in self.tasks) != sorted(shared_tasks.FEEDS):
            raise ValueError("通用来源任务不能重复或缺失")
        return self


@router.get("/shared")
def shared_view(box: Container = Depends(get_container)):
    import json

    config = shared_tasks.config(box)
    return {
        **config,
        "states": {
            key: {
                "next_at": box.tasks.due_at(0, key),
                "latest": next(
                    iter(json.loads(box.repos.settings.get(0, "tasks.history." + key, "[]"))), None
                ),
            }
            for key in shared_tasks.FEEDS
        },
    }


@router.put("/shared")
def shared_save(body: SharedConfig, box: Container = Depends(get_container)):
    with box.tasks.guard:
        shared_tasks.save(box, [t.model_dump() for t in body.tasks])
    return shared_view(box)


@router.post("/shared/once")
def shared_once(body: Period, box: Container = Depends(get_container)):
    from ...runtime.resource_access import allowed

    if body.key not in shared_tasks.FEEDS:
        raise HTTPException(422, "请选择通用来源任务")
    account = next(
        (a for a in box.repos.accounts.ids() if allowed(box, a) and box.has_session(a)), None
    )
    if account is None:
        raise HTTPException(409, "本机没有可执行来源更新的登录账号")
    return box.tasks.run(account, body.key, shared_tasks.config(box))
