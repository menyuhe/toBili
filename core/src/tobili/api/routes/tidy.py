"""清理端点。

这一组是整个 API 里唯一能造成不可恢复损失的入口，所以它比别处多两道门：

- 执行必须带着一个**已确认的**计划 ID 进来，接口本身不提供"直接执行"。
- 每次请求都回传闸门状态，前端可以据此把按钮画成灰的。判据在服务端，不在前端。

`apply=False` 是默认值，不是隐藏开关。少写一个字段不会让任何东西被删掉。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...container import Container
from ...domain.errors import MissingCredential
from ...runtime.lane import BusyAccount
from ...runtime.redact import safe_exception
from ...usecases.execute_tidy import EditTidy, TidyBlocked, TidyRejected
from ..deps import get_container

router = APIRouter(prefix="/api/tidy", tags=["tidy"])


class RunIn(BaseModel):
    apply: bool = Field(default=False, description="true 才真的删除/取关")
    limit: int | None = Field(default=None, ge=1, le=50)


class PauseIn(BaseModel):
    reason: str = ""


class SelectIn(BaseModel):
    """要执行的条目全集。没列进来的会被取消 —— 传的是状态，不是增量。"""

    item_ids: list[int] = Field(default_factory=list, description="勾选要执行的条目 ID")


class KeepIn(BaseModel):
    """保护名单。kind 只能是 up 或 campaign —— 白名单没有"全部保护"这种写法，
    那等于关掉整个功能，还会让人以为清干净了。"""

    kind: Literal["up", "campaign"]
    value: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=120)


def _account(box: Container) -> tuple[int, object]:
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    return account[0], account[1]


@router.post("/plans")
def create_plan(limit: int | None = None, box: Container = Depends(get_container)) -> dict:
    account_id, _ = _account(box)
    preview = box.plan_tidy().run(account_id, limit=limit)  # type: ignore[arg-type]
    return {
        "plan_id": preview.plan_id,
        "persisted": preview.persisted,
        "clearable": preview.clearable,
        "items": [asdict(line) for line in preview.lines],
    }


@router.get("/plans")
def recent_plans(limit: int = 10, box: Container = Depends(get_container)) -> dict:
    """最近的计划。界面重开时靠它接上没做完的那一次。"""
    account_id, _ = _account(box)
    rows = box.repos.tidy.recent_plans(account_id, limit=max(1, min(limit, 50)))  # type: ignore[arg-type]
    return {"items": [dict(row) for row in rows]}


def _owner(box: Container, plan_id: int) -> int:
    account_id, _ = _account(box)
    plan = box.repos.tidy.plan_of(plan_id)
    if not plan or int(plan["account_id"]) != account_id:
        raise HTTPException(status_code=404, detail="清单不存在")
    return account_id


@router.post("/plans/{plan_id}/items/select")
def select_items(plan_id: int, body: SelectIn, box: Container = Depends(get_container)) -> dict:
    """逐条勾选。只在预演阶段可用，确认之后清单就锁了。"""
    owner = _owner(box, plan_id)
    try:
        with box.lanes.exclusive(owner):
            report = EditTidy(tidy=box.repos.tidy, clock=box.clock).select(plan_id, body.item_ids)
    except (TidyRejected, BusyAccount) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "plan_id": report.plan_id,
        "selected": report.selected,
        "dropped": report.dropped,
        "revived": report.revived,
    }


@router.get("/plans/{plan_id}")
def show_plan(plan_id: int, box: Container = Depends(get_container)) -> dict:
    account_id, _ = _account(box)
    plan = box.repos.tidy.plan_of(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    if int(plan["account_id"]) != account_id:  # type: ignore[arg-type]
        raise HTTPException(status_code=404, detail="计划不存在")
    items = [dict(row) for row in box.repos.tidy.items_of(plan_id)]
    return {
        "plan_id": plan_id,
        "state": plan["state"],
        "note": plan["note"],
        "created_at": plan["created_at"],
        "confirmed_at": plan["confirmed_at"],
        "counts": box.repos.tidy.counts_of(plan_id),
        "items": items,
    }


@router.post("/plans/{plan_id}/confirm")
def confirm_plan(plan_id: int, box: Container = Depends(get_container)) -> dict:
    owner = _owner(box, plan_id)
    try:
        with box.lanes.exclusive(owner):
            state = EditTidy(tidy=box.repos.tidy, clock=box.clock).confirm(plan_id)
    except (TidyRejected, BusyAccount) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"plan_id": plan_id, "state": state.value}


@router.post("/plans/{plan_id}/run")
def run_plan(
    plan_id: int, body: RunIn | None = None, box: Container = Depends(get_container)
) -> dict:
    account_id = _owner(box, plan_id)
    payload = body or RunIn()
    try:
        with box.lanes.exclusive(account_id):  # type: ignore[arg-type]
            report = box.execute_tidy(account_id).run(
                plan_id, apply=payload.apply, limit=payload.limit
            )
    except TidyRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TidyBlocked as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except BusyAccount as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MissingCredential as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=safe_exception(exc)) from exc

    return {
        "plan_id": plan_id,
        "state": report.plan_state,
        "applied": payload.apply,
        "done": report.done,
        "failed": report.failed,
        "skipped": report.skipped,
        "planned": report.planned,
        "note": report.note,
        "snapshot": report.snapshot,
        "items": [asdict(line) for line in report.lines],
    }


@router.post("/items/{item_id}/undo")
def undo_item(item_id: int, box: Container = Depends(get_container)) -> dict:
    owner, _ = _account(box)
    record = box.repos.tidy.undo_of(item_id)
    if not record or int(record["account_id"]) != owner:
        raise HTTPException(404, "恢复记录不存在")
    try:
        with box.lanes.exclusive(owner):
            result = box.execute_tidy(owner).undo(item_id)
    except BusyAccount as exc:
        raise HTTPException(409, str(exc)) from exc
    return asdict(result)


@router.get("/gate")
def gate_status(box: Container = Depends(get_container)) -> dict:
    account_id, _ = _account(box)
    # inspect() 要连远端读未读/风控信号，凭据缺失或网络异常都不该冒成 500，
    # 否则页面上只会显示「服务器错误」，看不出是没登录还是 B 站抽风。
    try:
        snapshot = box.cleaning_gate().inspect(account_id)  # type: ignore[arg-type]
    except MissingCredential as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=safe_exception(exc)) from exc
    signals = snapshot.signals
    return {
        "open": snapshot.open,
        "reason": snapshot.decision.reason,
        "hint": snapshot.decision.hint,
        "paused": signals.paused,
        "risk_hits": signals.risk_hits,
        "unread_chat": signals.unread_chat,
        "unread_system": signals.unread_system,
        "acked_chat": signals.acked_chat,
        "acked_system": signals.acked_system,
        "readable": signals.readable,
    }


@router.post("/pause")
def pause(body: PauseIn | None = None, box: Container = Depends(get_container)) -> dict:
    account_id, _ = _account(box)
    reason = (body.reason if body else "") or ""
    box.cleaning_gate().pause(account_id, reason)  # type: ignore[arg-type]
    return {"paused": True, "reason": reason or "手动暂停"}


@router.post("/resume")
def resume(box: Container = Depends(get_container)) -> dict:
    account_id, _ = _account(box)
    gate = box.cleaning_gate()
    gate.resume(account_id)  # type: ignore[arg-type]
    decision = gate.check(account_id)  # type: ignore[arg-type]
    return {"open": decision.open, "reason": decision.reason, "hint": decision.hint}


@router.post("/ack")
def ack(box: Container = Depends(get_container)) -> dict:
    account_id, _ = _account(box)
    chat, system = box.cleaning_gate().ack(account_id)  # type: ignore[arg-type]
    return {"acked_chat": chat, "acked_system": system}


@router.get("/keep")
def list_keep(box: Container = Depends(get_container)) -> dict:
    """保护名单。命中的条目在预演里会显示"在保护名单里"，不会被清理。"""
    account_id, _ = _account(box)
    rows = box.repos.keep.all_of(account_id)  # type: ignore[arg-type]
    return {"items": [dict(row) for row in rows]}


@router.post("/keep")
def add_keep(body: KeepIn, box: Container = Depends(get_container)) -> dict:
    account_id, _ = _account(box)
    box.repos.keep.add(
        account_id,  # type: ignore[arg-type]
        kind=body.kind,
        value=body.value.strip(),
        note=body.note.strip(),
        now=box.clock.now(),
    )
    rows = box.repos.keep.all_of(account_id)  # type: ignore[arg-type]
    return {"ok": True, "items": [dict(row) for row in rows]}


@router.delete("/keep/{kind}/{value}")
def remove_keep(kind: str, value: str, box: Container = Depends(get_container)) -> dict:
    """从名单里移出去。移出之后这条才会重新参与清理 —— 这是刻意的两步操作。"""
    account_id, _ = _account(box)
    if kind not in ("up", "campaign"):
        raise HTTPException(status_code=400, detail="kind 只能是 up 或 campaign")
    removed = box.repos.keep.remove(account_id, kind=kind, value=value)  # type: ignore[arg-type]
    if not removed:
        raise HTTPException(status_code=404, detail="名单里没有这一条")
    rows = box.repos.keep.all_of(account_id)  # type: ignore[arg-type]
    return {"ok": True, "items": [dict(row) for row in rows]}
