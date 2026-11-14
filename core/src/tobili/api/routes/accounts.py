"""多账号。

三个动作：看有哪些号、切到哪个号、把哪个号摘掉。

**不做"改昵称"这类编辑。** 昵称和头像来自 B 站，改了就和服务端对不上，
下次刷新又变回去，等于给用户一个不生效的开关。

切号只改"当前是谁"。订阅源、参与记录、预选池、清理清单本来就按 account_id
分开存，所以切号之后看到的自然是另一套数据，不需要搬任何东西。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ...container import Container
from ...domain.errors import MissingCredential
from ...runtime.redact import mask_id, safe_exception
from ..deps import get_container

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class SwitchIn(BaseModel):
    account_id: int


class HealthIn(BaseModel):
    force: bool = False


@router.post("/{account_id}/check")
def check_login(account_id: int, body: HealthIn, box: Container = Depends(get_container)):
    if account_id not in box.repos.accounts.ids():
        raise HTTPException(404, "账号不存在")
    return box.account_health.request(account_id, force=body.force)


@router.get("/{account_id}/avatar")
def avatar(account_id: int, box: Container = Depends(get_container)):
    if account_id not in box.repos.accounts.ids():
        raise HTTPException(404, "账号不存在")
    profile = box.repos.accounts.by_id(account_id)
    try:
        data, kind = box.avatars.get(profile.avatar)
    except (ValueError, OSError):
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    return Response(
        data,
        media_type=kind,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


@router.get("")
def list_accounts(box: Container = Depends(get_container)) -> dict[str, object]:
    items = box.accounts()
    current = next((item["id"] for item in items if item["current"]), None)
    return {"items": items, "current": current}


@router.post("/switch")
def switch(body: SwitchIn, box: Container = Depends(get_container)) -> dict[str, object]:
    try:
        account_id, profile = box.switch_account(body.account_id)
    except MissingCredential as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    box.bus.publish(
        "account.switched",
        {"account_id": account_id, "nickname": profile.nickname},
    )
    return {
        "account": {
            "id": account_id,
            "uid": mask_id(profile.uid),
            "nickname": profile.nickname,
            "level": profile.level,
        }
    }


@router.delete("/{account_id}")
def remove(account_id: int, box: Container = Depends(get_container)) -> dict[str, object]:
    """把一个账号摘掉。

    只删凭据、把账号标成停用 —— **不删它名下的数据**。删了就是真的没了：
    参与记录是开奖核验的依据，清理清单是"我删了什么"的唯一凭据。
    人想重新用这个号，扫一次码就回来了，数据还在。
    """
    if not box.repos.accounts.exists(account_id):
        raise HTTPException(status_code=404, detail="没有这个账号")
    try:
        current = box.remove_account(account_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=safe_exception(exc)) from exc
    box.bus.publish("account.removed", {"account_id": account_id})
    return {"state": "removed", "current": current}
