"""收件箱。

读三类消息（私信、回复我的、@我的），标出"可能在说中奖"，并记下"我看过了"。

**这个接口不会清掉 B 站的红点。** 没有那样的公开接口，"已看"只是本机记录。
所以返回体里带着 `system_count`（只有条数、没有内容）和一个说明字段 ——
把人会误会的地方写在数据里，比写在文档里管用。
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
from ..deps import get_container

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


class ReviewIn(BaseModel):
    account_id: int
    key: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["irrelevant", "auto"]


@router.post("/review")
def review_message(body: ReviewIn, box: Container = Depends(get_container)):
    from .schedule import owner

    account_id = owner(box, body.account_id)
    box.repos.inbox.review(account_id, body.key, body.decision == "irrelevant")
    return {"reviewed": body.decision == "irrelevant"}


class SeenIn(BaseModel):
    keys: list[str]
    account_id: int | None = None


def _account(box: Container) -> int:
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    return account[0]


@router.get("")
def read_inbox(
    pages: int = 1, limit: int = 60, box: Container = Depends(get_container)
) -> dict[str, object]:
    account_id = _account(box)
    try:
        view = box.inbox(account_id).fetch(account_id, pages=max(1, pages), limit=limit)
    except MissingCredential as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except BusyAccount as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=safe_exception(exc)) from exc

    return {
        "items": [
            {
                **asdict(item),
                "at": item.at.isoformat() if item.at else None,
                "short": item.short,
            }
            for item in view.items
        ],
        "counts": view.counts,
        "system_count": view.system_count,
        "problems": list(view.problems),
        # 界面直接用这句话，别在别处再写一遍"已读只是本机记录"。
        "note": "「已看」只记在本机，不会清掉 B 站的红点。系统通知只有条数，B 站没有提供列表接口。",
    }


@router.post("/seen")
def mark_seen(body: SeenIn, box: Container = Depends(get_container)) -> dict[str, object]:
    from .schedule import owner

    account_id = owner(box, body.account_id)
    count = box.inbox(account_id).mark_seen(account_id, body.keys)
    return {"marked": count}


@router.delete("/seen")
def forget_seen(box: Container = Depends(get_container)) -> dict[str, str]:
    """清掉本机记录，让所有消息重新算未读。B 站那边一条都不会少。"""
    account_id = _account(box)
    box.inbox(account_id).forget_seen(account_id)
    return {"state": "cleared"}
