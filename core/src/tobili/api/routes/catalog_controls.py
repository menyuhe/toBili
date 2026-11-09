from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...container import Container
from ...usecases.catalog_controls import edit_prize, put_block
from ..deps import get_container

router = APIRouter(prefix="/api", tags=["catalog-controls"])

class DeadlineEdit(BaseModel):
    value: str
    expected: str | None = None

@router.put('/campaigns/{cid}/deadline')
def deadline(cid:int,body:DeadlineEdit,box:Container=Depends(get_container)):
    from ...usecases.catalog_controls import edit_deadline
    try:return edit_deadline(box,cid,body.value,body.expected)
    except LookupError as exc:raise HTTPException(404,str(exc)) from exc
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


class PrizeEdit(BaseModel):
    reward: str = Field(min_length=1, max_length=500)
    expected: str = Field(max_length=1000)
    evidence: str = Field(default="", max_length=2000)


@router.put("/campaigns/{cid}/prize")
def prize(cid: int, body: PrizeEdit, box: Container = Depends(get_container)):
    if not body.reward.strip():
        raise HTTPException(422, "请填写具体奖品")
    try:
        return edit_prize(box, cid, body.reward.strip(), body.expected, body.evidence.strip())
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class BlockEdit(BaseModel):
    uid: str = Field(pattern=r"^[0-9]{1,24}$")
    name: str = Field(default="", max_length=120)
    reason: str = Field(default="", max_length=500)
    active: bool = True


@router.get("/blocks")
def blocks(box: Container = Depends(get_container)):
    rows = (
        box.db.connect()
        .execute("SELECT * FROM up_block WHERE active=1 ORDER BY updated_at DESC")
        .fetchall()
    )
    return {
        "items": [dict(r) for r in rows],
        "shared": getattr(box, "change_block", None) is not None,
    }


@router.put("/blocks")
def block(body: BlockEdit, box: Container = Depends(get_container)):
    change = getattr(box, "change_block", None)
    if change:
        return change(body.uid, body.name.strip(), body.reason.strip(), body.active)
    put_block(box, body.uid, body.name.strip(), body.reason.strip(), body.active)
    return {"synced": False, "local": True}
