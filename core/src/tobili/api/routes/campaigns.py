"""活动库。

库里躺着的每一个活动，界面上都得能看见 —— 否则"我参与过哪些""哪些还没开奖"
只能靠翻命令行。这一页不做任何判断，只把四个来源拼起来如实展示：活动本身、
我的报名、开奖结论、清理状态。

改判入口也在这里（PUT /api/results/{id} 在 results.py），因为人只有在看到
"这条被判成未中奖"的时候，才会意识到判错了。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ...container import Container
from ..deps import get_container

router = APIRouter(prefix="/api", tags=["campaigns"])


@router.get("/campaigns")
def list_campaigns(
    keyword: str = "",
    kind: str = "",
    state: str = Query(default="", description="enrolled / won / lost / pending / cleaned"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    statuses: str = "",
    time_field: str = "published",
    after: str = "",
    before: str = "",
    order: str = "newest",
    prize_state: str = "",
    availability: str = "",
    official: str = Query(default="", description="yes 官方池 / no 线索池"),
    box: Container = Depends(get_container),
) -> dict[str, object]:
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    from ...adapters.sqlite.db import parse_stamp, stamp

    try:
        after = stamp(parse_stamp(after)) if after else ""
        before = stamp(parse_stamp(before)) if before else ""
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(422, "时间格式无效") from None
    if after and before and after > before:
        raise HTTPException(422, "起始时间不能晚于结束时间")
    body = box.repos.campaigns.catalog(
        account[0],  # type: ignore[arg-type]
        keyword=keyword,
        kind=kind,
        state=state,
        limit=limit,
        offset=offset,
        statuses=statuses,
        time_field=time_field,
        after=after,
        before=before,
        order=order,
        prize_state=prize_state,
        availability=availability,
        official=official,
    )
    items = []
    for row in body["items"]:  # type: ignore[union-attr]
        item = dict(row)  # type: ignore[arg-type]
        # 时间戳统一转成 ISO，前端不猜格式。解析不了就给 None，不编一个值。
        for key in ("deadline_at", "first_seen_at", "decided_at", "published_at"):
            item[key] = _iso(item.get(key))
        item["confidence"] = round(float(item.get("confidence") or 0), 2)
        items.append(item)
    return {"items": items, "total": body["total"], "counts": body.get("counts", {})}


def _iso(value: object) -> str | None:
    from ...adapters.sqlite.db import parse_stamp

    if not value:
        return None
    try:
        stamp = parse_stamp(str(value))
    except (TypeError, ValueError):
        return None
    return stamp.isoformat() if stamp else None


@router.post("/campaigns/shelf")
def shelf(box: Container = Depends(get_container)) -> dict:
    account = box.account()
    if not account:
        raise HTTPException(401, "请先登录")
    return {"count": box.repos.campaigns.shelve_closed(account[0], box.clock.now())}


@router.delete("/campaigns/shelf/{campaign_id}")
def unshelve(campaign_id: int, box: Container = Depends(get_container)) -> dict:
    account = box.account()
    if not account:
        raise HTTPException(401, "请先登录")
    box.repos.campaigns.unshelve(account[0], campaign_id)
    return {"ok": True}


@router.post("/campaigns/shelf/{campaign_id}")
def dismiss(
    campaign_id: int, prize_only: bool = False, box: Container = Depends(get_container)
) -> dict:
    from ...runtime.lane import BusyAccount

    account = box.account()
    if not account:
        raise HTTPException(401, "请先登录")
    try:
        with box.lanes.exclusive(account[0]):
            box.repos.campaigns.dismiss(account[0], campaign_id, prize_only=prize_only)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, BusyAccount) as exc:
        raise HTTPException(409, str(exc)) from exc
    box.bus.publish("pool.changed", {"account_id": account[0]})
    return {"ok": True}


@router.get("/campaigns/prizes")
def prize_status(box: Container = Depends(get_container)):
    from ...usecases.prize_review import review_status

    return review_status(box)


@router.post("/campaigns/prizes")
def prize_start(enabled: bool = True, box: Container = Depends(get_container)):
    if enabled and not box.llm().ready():
        raise HTTPException(409, "请先配置模型")
    box.repos.settings.put(0, "prizes.enabled", "1" if enabled else "0")
    return prize_status(box)


@router.get("/campaigns/{campaign_id}")
def show_campaign(campaign_id: int, box: Container = Depends(get_container)) -> dict[str, object]:
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    row = box.repos.campaigns.get(campaign_id)
    if row is None:
        raise HTTPException(status_code=404, detail="活动不存在")
    verdict = box.repos.verdicts.detail(campaign_id, account[0])  # type: ignore[arg-type]
    enrollment = [
        {
            "id": item.id,
            "stage": item.stage.value,
            "reason": item.reason,
            "updated_at": item.updated_at.isoformat(),
        }
        for item in box.repos.enrollments.by_campaign(campaign_id)
        if item.account_id == account[0]
    ]
    return {
        "campaign": {**row, "deadline_at": _iso(row.get("deadline_at"))},
        "verdict": (
            {
                "outcome": verdict["outcome"],
                "source": verdict["source"],
                "note": verdict["note"],
                "evidence": verdict["evidence"],
                "decided_at": (
                    verdict["decided_at"].isoformat()  # type: ignore[union-attr]
                    if verdict["decided_at"]
                    else None
                ),
            }
            if verdict
            else None
        ),
        "enrollment": enrollment[0] if enrollment else None,
    }
