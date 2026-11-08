"""预选池：一个有顺序的待参与队列。

接口和别的写入口一样，**默认预演**。想真跑必须显式 `apply=true` —— 这个开关不是
形式主义，它挡的是"手滑点了一下，十几条转发就发出去了"。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...container import Container
from ...domain.errors import MissingCredential
from ...domain.pool import PoolOp, PoolRejected
from ...runtime.lane import BusyAccount
from ...runtime.redact import safe_exception
from ...runtime.work_priority import participation_turn
from ..deps import get_container

router = APIRouter(prefix="/api/pool", tags=["pool"])


class PoolEdit(BaseModel):
    operation: PoolOp
    campaign_ids: list[int] = Field(default_factory=list, max_length=200)
    campaign_id: int = 0
    direction: int = 0


class PoolRun(BaseModel):
    apply: bool = False
    limit: int = 0


def _account(box: Container) -> int:
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    return account[0]


@router.get("")
def view(box: Container = Depends(get_container)) -> dict[str, object]:
    account_id = _account(box)
    pool = box.manage_pool().view(account_id)
    return {
        "items": list(pool.items),
        "paused": pool.paused,
        "note": pool.note,
        "counts": pool.counts,
        "limit": pool.limit,
        "progress": box.pool_progress.get(account_id, ""),
    }


@router.get("/count")
def count(box: Container = Depends(get_container)) -> dict[str, int]:
    account_id = _account(box)
    pending = (
        box.db.connect()
        .execute(
            "SELECT count(*) FROM pool_item WHERE account_id=? AND state IN ('pending','running')",
            (account_id,),
        )
        .fetchone()[0]
    )
    return {"account_id": account_id, "pending": pending}


@router.post("")
def edit(body: PoolEdit, box: Container = Depends(get_container)) -> dict[str, object]:
    account_id = _account(box)
    try:
        pool = box.manage_pool().edit(
            account_id,
            body.operation,
            campaign_ids=body.campaign_ids,
            campaign_id=body.campaign_id,
            direction=body.direction,
        )
    except PoolRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "items": list(pool.items),
        "paused": pool.paused,
        "note": pool.note,
        "counts": pool.counts,
        "limit": pool.limit,
        "progress": box.pool_progress.get(account_id, ""),
    }


@router.post("/run")
def run(body: PoolRun, box: Container = Depends(get_container)) -> dict[str, object]:
    account_id = _account(box)
    try:
        with participation_turn(box, account_id):
            report = box.run_pool(account_id).run(
                account_id, apply=body.apply, limit=max(0, body.limit)
            )
    except (PoolRejected, BusyAccount) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MissingCredential as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        box.bus.publish(
            "participation.issue",
            {"account_id": account_id, "level": "error", "message": safe_exception(exc)},
        )
        raise HTTPException(status_code=502, detail=safe_exception(exc)) from exc
    return {
        "applied": report.applied,
        "attempted": report.attempted,
        "joined": report.joined,
        "skipped": report.skipped,
        "failed": report.failed,
        "pending": report.pending,
        "paused": report.paused,
        "note": report.note,
        "lines": [
            {
                "campaign_id": line.campaign_id,
                "dyn_id": line.dyn_id,
                "title": line.title,
                "state": line.state,
                "message": line.message,
            }
            for line in report.lines
        ],
    }
