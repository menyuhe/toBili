from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...container import Container
from ...desktop import account_browser as browser
from ..deps import get_container
from .schedule import owner

router = APIRouter(prefix="/api/account-browser", tags=["accounts"])


class OpenIn(BaseModel):
    account_id: int = Field(gt=0)
    url: str = Field(max_length=4096)


@router.post("")
def open_window(body: OpenIn, box: Container = Depends(get_container)):
    account_id = owner(box, body.account_id)
    if not box.has_session(account_id):
        raise HTTPException(409, "请重新登录这个账号")
    try:
        ticket = browser.launch(box.paths.home, account_id, body.url)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ticket": ticket}


@router.get("/{ticket}")
def status(ticket: str, box: Container = Depends(get_container)):
    owner(box)
    try:
        return browser.read_status(box.paths.home, ticket)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
