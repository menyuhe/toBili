"""开奖核验、清理预演、写配额。

三个都是**只读**端点（settle 默认也是预演），破坏性动作不在这层暴露。等清理真的
要执行时，会单独开一个必须带确认令牌的端点，而不是给这里加个 `apply=true`。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...container import Container
from ...domain.errors import MissingCredential
from ...domain.tidy import Outcome
from ...runtime.lane import BusyAccount
from ...runtime.redact import safe_exception
from ...usecases.settle import SettleRejected
from ..deps import get_container

router = APIRouter(prefix="/api", tags=["results"])


@router.get('/results/discarded')
def discarded(box: Container = Depends(get_container)):
    from .schedule import owner
    rows=box.db.connect().execute('''SELECT d.*,c.reward,c.dyn_id FROM result_dismissal d
        JOIN campaign c ON c.id=d.campaign_id WHERE d.account_id=? ORDER BY d.at DESC''',(owner(box),)).fetchall()
    return {'items':[dict(r) for r in rows]}


class DiscardIn(BaseModel):
    account_id: int


@router.post('/results/{campaign_id}/discard')
def discard_result(campaign_id: int, body: DiscardIn, box: Container = Depends(get_container)):
    from ...usecases.discard_result import discard
    from .schedule import owner
    key=owner(box,body.account_id)
    try:
        with box.lanes.exclusive(key):
            return discard(box,key,campaign_id)
    except BusyAccount as exc:
        raise HTTPException(409,str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400,safe_exception(exc)) from exc


@router.get("/results")
def result_list(
    query: str = "",
    outcome: str = "",
    link: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    box: Container = Depends(get_container),
):
    from ...usecases.result_ledger import ledger
    from .schedule import owner

    return ledger(
        box, owner(box), query=query, outcome=outcome, link=link, offset=offset, limit=limit
    )


@router.get("/results/history")
def result_history(offset: int = Query(0, ge=0), box: Container = Depends(get_container)):
    from .schedule import owner

    key = owner(box)
    rows = (
        box.db.connect()
        .execute(
            """SELECT h.dyn_id,h.action,h.target,h.state,h.note,h.at,c.reward
        FROM prior_cleanup h LEFT JOIN campaign c ON c.dyn_id=h.dyn_id WHERE h.account_id=?
        UNION ALL SELECT c.dyn_id,t.action,t.target,t.state,t.reason,t.executed_at,c.reward
        FROM tidy_item t JOIN tidy_plan p ON p.id=t.plan_id JOIN campaign c ON c.id=t.campaign_id
        WHERE p.account_id=? AND p.note!='legacy-import' AND t.executed_at IS NOT NULL
        ORDER BY at DESC LIMIT 50 OFFSET ?""",
            (key, key, offset),
        )
        .fetchall()
    )
    return {"items": [dict(r) for r in rows]}


class MatchIn(BaseModel):
    account_id: int
    resume: bool = False


@router.post("/results/match")
def match_results(body: MatchIn, box: Container = Depends(get_container)):
    from ...usecases.result_ledger import discover
    from .schedule import owner

    key = owner(box, body.account_id)
    try:
        with box.lanes.exclusive(key):
            return discover(box, key, resume=body.resume)
    except BusyAccount as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, safe_exception(exc)) from exc


class LinkIn(BaseModel):
    account_id: int
    share_id: str


@router.put("/results/{campaign_id}/link")
def confirm_link(campaign_id: int, body: LinkIn, box: Container = Depends(get_container)):
    import json

    from .schedule import owner

    key = owner(box, body.account_id)
    try:
        with box.lanes.exclusive(key), box.db.write() as db:
            row = db.execute(
                "SELECT choices FROM participation_ref WHERE account_id=? AND campaign_id=?",
                (key, campaign_id),
            ).fetchone()
            if not row or body.share_id not in json.loads(row["choices"]):
                raise HTTPException(409, "该转发不在当前账号的候选记录中")
            db.execute(
                "UPDATE participation_ref SET share_id=?,link_state='verified',checked_at=? WHERE account_id=? AND campaign_id=?",
                (body.share_id, box.clock.now().isoformat(), key, campaign_id),
            )
    except BusyAccount as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"state": "verified"}


class OverrideIn(BaseModel):
    """人工改判。

    `outcome` 不接受 pending：待定不是一种结论，是"还没判"。想恢复自动核验，
    把结论改回它原本的值即可 —— 但 `source` 会一直是 manual，这是有意的：
    人一旦介入过，这件事就该由人负责。
    """

    account_id: int | None = None
    protect: bool = False
    outcome: Literal["won", "lost", "void"]
    note: str = Field(default="", max_length=200, description="改判理由，会写进证据")


@router.get("/quota")
def quota(box: Container = Depends(get_container)) -> dict[str, object]:
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    engine = box.quota()
    gate = engine.check(account[0])
    budget = engine.budget
    return {
        "verdict": gate.verdict.value,
        "allowed": gate.allowed,
        "explain": gate.explain(),
        "wait_seconds": round(gate.wait_seconds, 1),
        "last_hour": gate.last_hour,
        "last_day": gate.last_day,
        "budget": {
            "per_hour": budget.per_hour,
            "per_day": budget.per_day,
            "min_gap_seconds": budget.min_gap_seconds,
        },
    }


@router.post("/settle")
def settle(
    apply: bool = False,
    limit: int = 20,
    box: Container = Depends(get_container),
) -> dict[str, object]:
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    try:
        with box.lanes.exclusive(account[0]):
            report = box.settle().run(
                account[0], profile=account[1], limit=limit, dry_run=not apply
            )
    except BusyAccount as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MissingCredential as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=safe_exception(exc)) from exc

    return {
        "checked": report.checked,
        "decided": report.decided,
        "applied": apply,
        "items": [asdict(item) for item in report.items],
        "problems": list(report.problems),
    }


@router.get("/tidy/preview")
def tidy_preview(
    limit: int | None = None,
    box: Container = Depends(get_container),
) -> dict[str, object]:
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    preview = box.plan_tidy().run(account[0], limit=limit)
    return {
        "plan_id": preview.plan_id,
        "persisted": preview.persisted,
        "clearable": preview.clearable,
        "items": [asdict(line) for line in preview.lines],
    }


@router.put("/results/{campaign_id}")
def override_result(
    campaign_id: int,
    body: OverrideIn,
    box: Container = Depends(get_container),
) -> dict[str, object]:
    """改判。这是整条链路上唯一能纠正"机器判错"的入口。

    机器判错的代价不对称：判成"中奖"只是少清一条，判成"未中奖"会真的删掉一条
    其实中奖的转发。所以按钮必须存在，而且改完之后自动核验不许再覆盖。
    """
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    if body.account_id is not None and body.account_id != account[0]:
        raise HTTPException(409, "当前账号已改变")
    campaign = box.repos.campaigns.get(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="活动不存在")
    try:
        with box.lanes.exclusive(account[0]):
            report = box.settle(account[0]).override(
                account[0],
                campaign_id=campaign_id,
                outcome=Outcome(body.outcome),
                note=body.note.strip(),
            )
            if body.protect:
                box.repos.keep.add(
                    account[0],
                    kind="campaign",
                    value=str(campaign["dyn_id"]),
                    note=body.note.strip() or "人工留存",
                    now=box.clock.now(),
                )
    except BusyAccount as exc:
        raise HTTPException(409, "本账号正在执行任务，请稍后复核") from exc
    except SettleRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    box.bus.publish(
        "verdict.override",
        {
            "account_id": account[0],
            "campaign_id": campaign_id,
            "outcome": report.outcome.value,
            "previous": report.previous.value if report.previous else "",
            "note": report.note,
        },
    )
    return {
        "campaign_id": campaign_id,
        "outcome": report.outcome.value,
        "previous": report.previous.value if report.previous else "",
        "note": report.note,
        "was_manual": report.was_manual,
    }
