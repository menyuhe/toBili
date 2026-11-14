"""扫码登录接口。

对话式流程：POST 拿票 → 前端每两秒 GET 一次。确认成功时顺带把凭据写进保险库，
所以前端不需要再调一次"保存"。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...container import Container
from ...domain.errors import RemoteError
from ...ports.models import QrTicket
from ...runtime.redact import mask_id, safe_exception
from ..deps import get_container

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/qr")
def issue(box: Container = Depends(get_container)) -> dict[str, object]:
    try:
        ticket = box.authenticator().begin()
    except RemoteError as exc:
        raise HTTPException(status_code=502, detail=safe_exception(exc)) from exc
    box.remember(ticket)
    return {"key": ticket.key, "url": ticket.url}


@router.get("/qr/{key}")
def poll(key: str, box: Container = Depends(get_container)) -> dict[str, object]:
    try:
        progress = box.authenticator().poll(QrTicket(key=key, url=box.ticket(key).url))
    except RemoteError as exc:
        raise HTTPException(status_code=502, detail=safe_exception(exc)) from exc

    if progress.state != "confirmed" or not progress.credential:
        return {"state": progress.state}

    try:
        result = box.sign_in().persist(progress.credential)
    except RemoteError as exc:
        raise HTTPException(status_code=502, detail=safe_exception(exc)) from exc

    box.account_health.signed_in(result.account_id)

    return {
        "state": "confirmed",
        "account": {
            "uid": mask_id(result.profile.uid),
            "nickname": result.profile.nickname,
            "level": result.profile.level,
        },
        "encrypted": result.encrypted,
    }


@router.delete("/session")
def sign_out(box: Container = Depends(get_container)) -> dict[str, object]:
    """退出当前账号。

    多账号下这就是"把这个号摘掉"。还有别的号能用就顺手切过去 —— 退出一个号
    不该把人踹回扫码页，他明明还登录着别的。
    """
    current = box.account()
    if current is None:
        return {"state": "signed_out", "current": None}
    next_id = box.remove_account(current[0])
    box.bus.publish("account.removed", {"account_id": current[0]})
    return {"state": "signed_out", "current": next_id}
