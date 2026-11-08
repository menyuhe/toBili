"""Independent account task periods, sharing the normal write and account fences."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from ...container import Container
from ...runtime.lane import BusyAccount
from ...runtime.taskbook import TASKS
from ..deps import get_container
from .schedule import owner

router = APIRouter(prefix="/api/tasks", tags=["schedule"])
TaskKey = Literal["topics", "collections", "watch", "results", "messages", "participate", "cleanup"]


class Period(BaseModel):
    key: TaskKey
    enabled: bool = False
    minutes: int = Field(default=60, ge=5, le=10080)

    @model_validator(mode="after")
    def paced(self):
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
    tasks: list[Period] = Field(min_length=len(TASKS), max_length=len(TASKS))

    @model_validator(mode="after")
    def complete(self):
        if {t.key for t in self.tasks} != set(TASKS):
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
    writes = (
        body.running
        and body.apply
        and any(t.key == "participate" and t.enabled for t in body.tasks)
    )
    if writes and not body.confirmed:
        raise HTTPException(409, "请确认真实参与的账号、范围与每轮上限")
    if writes and box.check_writer:
        box.check_writer(key)
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
    apply = body.apply and body.key == "participate"
    if apply and not body.confirmed:
        raise HTTPException(409, "请确认本次真实参与的账号、范围与上限")
    return box.tasks.run(key, body.key, body.model_dump(), apply=apply)


@router.delete("")
def stop(account_id: int, box: Container = Depends(get_container)):
    key = owner(box, account_id)
    box.tasks.pause(key)
    return box.tasks.view(key)
