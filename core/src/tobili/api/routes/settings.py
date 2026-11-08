"""设置：文案模板与模型接入。

密钥单独走保险库，不进 `setting` 表，也永远不回显 —— 界面只知道"设没设"。
这不是洁癖：`setting` 表会随手被导出、被备份、被贴进 issue，而密钥在那里就是
明文的。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...container import Container
from ...domain.participate_text import TextMode
from ...ports.llm import LlmError
from ...usecases.participate_text import describe_settings, dump_preview, save_settings
from ..deps import get_container

router = APIRouter(prefix="/api/settings", tags=["settings"])


class PaceIn(BaseModel):
    account_id: int
    interval_seconds: float = Field(ge=0, le=3600, allow_inf_nan=False)


@router.get("/pace")
def read_pace(box: Container = Depends(get_container)):
    account = box.account()
    if not account:
        raise HTTPException(401, "还没有登录")
    return {
        "account_id": account[0],
        "interval_seconds": box.quota(account[0]).budget.min_gap_seconds,
    }


@router.put("/pace")
def save_pace(body: PaceIn, box: Container = Depends(get_container)):
    if not box.account() or box.account()[0] != body.account_id:
        raise HTTPException(409, "账号已切换，请重新打开设置")
    box.repos.settings.put(
        body.account_id, "participation.interval_seconds", str(body.interval_seconds)
    )
    return read_pace(box)


class TextIn(BaseModel):
    """文案设置。字段全可选：只改传了的那个，没传表示"别动"。"""

    mode: TextMode | None = None
    custom: str | None = Field(default=None, max_length=200)
    fallback: str | None = Field(default=None, max_length=200)


class LlmIn(BaseModel):
    base_url: str | None = Field(default=None, max_length=300)
    scan_enabled: bool | None = None
    model: str | None = Field(default=None, max_length=120)
    api_key: str | None = Field(default=None, max_length=300, description="留空表示不修改")
    enabled: bool | None = None


class PreviewIn(BaseModel):
    """预览一条文案。不参与、不写库，纯粹是"这样写出来像不像人话"。"""

    up_name: str = ""
    title: str = ""
    body: str = ""
    dyn_id: str = ""


@router.get("")
def read_settings(box: Container = Depends(get_container)) -> dict:
    return describe_settings(box.repos.settings, box.vault, box.llm())


@router.put("/text")
def write_text(body: TextIn, box: Container = Depends(get_container)) -> dict:
    save_settings(
        box.repos.settings,
        box.vault,
        mode=body.mode.value if body.mode else None,
        custom=body.custom,
        fallback=body.fallback,
    )
    return describe_settings(box.repos.settings, box.vault, box.llm())


@router.put("/llm")
def write_llm(body: LlmIn, box: Container = Depends(get_container)) -> dict:
    save_settings(
        box.repos.settings,
        box.vault,
        llm_base=body.base_url,
        llm_model=body.model,
        llm_key=body.api_key,
        llm_enabled=body.enabled,
        scan_enabled=body.scan_enabled,
    )
    box.repos.settings.put(0, "llm.health", "{}")
    return describe_settings(box.repos.settings, box.vault, box.llm())


@router.post("/llm/test")
def test_llm(box: Container = Depends(get_container)) -> dict:
    """连一次，只让它回两个字。失败把原因原样返回 —— 界面上要能照着改。"""
    client = box.llm()
    if not client.ready():
        raise HTTPException(status_code=409, detail="地址、密钥、模型名三项都要填")
    try:
        reply = client.test()  # type: ignore[attr-defined]
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    box.bus.publish("llm", {"event": "test_ok", "model": client.describe().get("model", "")})
    return {"ok": True, "reply": reply[:80], "model": client.describe()}


@router.post("/text/preview")
def preview_text(body: PreviewIn, box: Container = Depends(get_container)) -> dict:
    """按当前设置生成一条，不发送。

    这条端点是"配好了但不敢用"的解药：先看十条，觉得像人话再放开自动参与。
    """
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    draft = box.text().resolve(
        action="share",
        campaign_title=body.title or "（没有标题）",
        up_name=body.up_name,
        dyn_id=body.dyn_id,
        body=body.body or body.title,
    )
    return dump_preview(draft)


class NotifyIn(BaseModel):
    """钉钉通知配置。`webhook`/`secret` 不传表示不改 —— 它们读不回来，界面上永远是空框。"""

    webhook: str | None = Field(default=None, max_length=400)
    secret: str | None = Field(default=None, max_length=200)
    enabled: bool = False


@router.get("/notify")
def read_notify(box: Container = Depends(get_container)) -> dict:
    return _notify_dict(box.notify().state())


@router.put("/notify")
def write_notify(body: NotifyIn, box: Container = Depends(get_container)) -> dict:
    state = box.notify().save(webhook=body.webhook, secret=body.secret, enabled=body.enabled)
    box.bus.publish("notify.saved", {"enabled": state.enabled})
    return _notify_dict(state)


@router.delete("/notify")
def clear_notify(box: Container = Depends(get_container)) -> dict:
    state = box.notify().clear()
    return _notify_dict(state)


@router.post("/notify/test")
def test_notify(box: Container = Depends(get_container)) -> dict:
    """真发一条。发不出去时把原因原样返回 —— 钉钉的报错就那几句，
    照着改比猜快。"""
    ok, message = box.notify().test()
    box.bus.publish("notify.tested", {"ok": ok})
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"ok": True, "detail": message}


def _notify_dict(state) -> dict:  # type: ignore[no-untyped-def]
    return {
        "enabled": state.enabled,
        "webhook": state.webhook,
        "has_webhook": state.has_webhook,
        "has_secret": state.has_secret,
    }
