"""定时自动化：启停、改间隔、现在跑一次。

界面上的四件事都在这里：当前开着没有、多久一轮、下一轮什么时候、上一轮结果。
"现在跑一次"故意和排期分开 —— 想立刻验证设置的人不该为此把整个定时器重启。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...container import Container
from ..deps import get_container

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


class Selection(BaseModel):
    account_id: int = Field(gt=0)
    selection: Literal["queue", "catalog"] = "queue"
    apply: bool = False
    confirmed: bool = False


class ScheduleIn(Selection):
    interval_min: int = Field(default=60, ge=5, le=1440)


def owner(box: Container, expected: int | None = None) -> int:
    account = box.account()
    if account is None:
        raise HTTPException(401, "请先选择账号")
    if expected is not None and account[0] != expected:
        raise HTTPException(409, "账号已经切换，请重新确认")
    return account[0]


def checked(box: Container, body: Selection) -> int:
    key = owner(box, body.account_id)
    if body.apply and not body.confirmed:
        raise HTTPException(409, "真实参与须确认账号与来源")
    if body.apply and box.check_writer:
        box.check_writer(key)
    return key


@router.get("")
def read_schedule(box: Container = Depends(get_container)) -> dict:
    key = owner(box)
    return {**box.schedule(key).status().as_dict(), "account_id": key, "selection": box.scope(key)}


@router.post("")
def start_schedule(body: ScheduleIn, box: Container = Depends(get_container)) -> dict:
    key = checked(box, body)
    if box.tasks.config(key)["running"]:
        raise HTTPException(409, "请先停止当前账号的定时任务")
    if box.schedule(key).status().running:
        raise HTTPException(409, "先停止此号的排期再修改")
    box.repos.settings.put(key, "schedule.selection", body.selection)
    box.schedule(key).start(interval_min=body.interval_min, apply=body.apply)
    return read_schedule(box)


@router.delete("")
def stop_schedule(account_id: int, box: Container = Depends(get_container)) -> dict:
    box.schedule(owner(box, account_id)).stop()
    return read_schedule(box)


@router.post("/once")
def run_once(body: Selection, box: Container = Depends(get_container)) -> dict:
    key = checked(box, body)
    return (
        box.schedule(key)
        .run_selection(body.apply, lambda apply: box.scheduled_round(key, apply, body.selection))
        .as_dict()
    )
