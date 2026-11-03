from __future__ import annotations

from typing import Literal
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from src import maintenance, notifications
from src.bilibili_auth import get_login_uid
from web.api_errors import AppError, ErrorCode

router = APIRouter(prefix="/api", tags=["stable"])


def current_uid() -> str:
    uid = get_login_uid()
    if not uid:
        raise AppError(ErrorCode.AUTH_REQUIRED, "请先扫码登录")
    return str(uid)


def checked_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from None


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NotificationSettingsRequest(StrictRequest):
    audio: bool
    dingtalk: bool
    webhook: str | None = Field(default=None, max_length=2000)
    sign_secret: str | None = Field(default=None, max_length=200)
    prefix: str = Field(default="Binggo", max_length=50)
    events: list[Literal["won", "at", "login", "failure", "cleanup"]]


class ChannelRequest(StrictRequest):
    channel: Literal["audio", "dingtalk"]


class CleanupSettingsRequest(StrictRequest):
    enabled: bool
    delete_reposts: bool
    unfollow: bool
    protected_ups: list[str] = Field(default_factory=list, max_length=1000)


class ResultRequest(StrictRequest):
    outcome: Literal["won", "lost", "unknown"]


class KeepRequest(StrictRequest):
    keep: bool


class LinkRequest(StrictRequest):
    up_uid: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    repost_id: str = Field(default="", pattern=r"^(|[1-9][0-9]{0,24})$")
    follow_added: bool = False


def start_job(action: str, params=None):
    from web.job_runner import runner
    uid = current_uid()
    params = {**(params or {}), "uid": uid}
    if runner.try_start(action, params, source="ui") is None:
        raise AppError(ErrorCode.JOB_BUSY, "已有任务正在运行，请稍后再试")
    return {"ok": True, "job": runner.get_status().to_dict()}


@router.get("/notifications/settings")
def get_notifications():
    return notifications.public_settings()


@router.put("/notifications/settings")
def put_notifications(request: NotificationSettingsRequest):
    return checked_call(notifications.save_settings, request.model_dump())


@router.post("/notifications/test")
def test_notification(request: ChannelRequest):
    uid = str(get_login_uid() or "__global__")
    key = checked_call(notifications.test_notification, uid, request.channel)
    return {"ok": True, "event_key": key, "message": "测试通知已加入发送队列"}


@router.get("/notifications/history")
def notifications_history():
    return {"items": notifications.history(str(get_login_uid() or "__global__"))}


@router.get("/maintenance/settings")
def get_maintenance_settings():
    return maintenance.settings(current_uid())


@router.put("/maintenance/settings")
def put_maintenance_settings(request: CleanupSettingsRequest):
    return checked_call(maintenance.save_settings, current_uid(), request.model_dump())


@router.get("/maintenance/activities")
def list_candidates(page: int = Query(1, ge=1), outcome: str = "", q: str = "", association: str = ""):
    from src.repost_discovery import LABELS, scan_status
    uid = current_uid()
    items = maintenance.list_candidates(uid)
    counts = {key: sum(item["association"]["state"] == key for item in items) for key in LABELS}
    if association:
        items = [item for item in items if item["association"]["state"] == association]
    if outcome:
        items = [item for item in items if item["outcome"] == outcome]
    if q:
        items = [item for item in items if q.casefold() in (item["title"] + item["dynamic_id"]).casefold()]
    return {"items": items[(page-1)*20:page*20], "total": len(items), "page": page, "page_size": 20,
            "association_counts": counts, "scan": scan_status(uid)}


@router.get("/maintenance/history")
def history(dynamic_id: str = ""):
    return {"items": maintenance.audit_history(current_uid(), dynamic_id)}


@router.get("/maintenance/cleanup-history")
def cleanup_history(before: int = Query(0, ge=0)):
    return maintenance.cleanup_history(current_uid(), before)


@router.post("/maintenance/check")
def check():
    return start_job("maintenance_check")


@router.post("/maintenance/discover")
def discover_all(mode: Literal["recent", "continue"] = "recent"):
    return start_job("maintenance_discover_all", {"mode": mode})


@router.post("/maintenance/clean")
def clean():
    if not maintenance.settings(current_uid())["enabled"]:
        raise AppError(ErrorCode.VALIDATION_ERROR, "请先在设置中开启自动清理及所需动作")
    return start_job("maintenance_clean")


@router.post("/maintenance/activities/{did}/retry")
def retry(did: str):
    if did not in maintenance.activities_for_uid(current_uid()):
        raise AppError(ErrorCode.NOT_FOUND, "未找到当前账号的参与记录")
    if not maintenance.settings(current_uid())["enabled"]:
        raise AppError(ErrorCode.VALIDATION_ERROR, "清理开关未开启")
    return start_job("maintenance_clean", {"dynamic_id": did})


@router.put("/maintenance/activities/{did}/result")
def mark_result(did: str, request: ResultRequest):
    checked_call(maintenance.mark_outcome, current_uid(), did, request.outcome)
    return {"ok": True}


@router.put("/maintenance/activities/{did}/keep")
def keep(did: str, request: KeepRequest):
    checked_call(maintenance.mark_keep, current_uid(), did, request.keep)
    return {"ok": True}


@router.post("/maintenance/activities/{did}/link")
def confirm_link(did: str, request: LinkRequest):
    return start_job("maintenance_link", {"dynamic_id": did, **request.model_dump()})


@router.post("/maintenance/activities/{did}/discover")
def discover(did: str):
    return start_job("maintenance_discover", {"dynamic_id": did})


class MessageResolutionRequest(StrictRequest):
    key: str = Field(min_length=1, max_length=200)
    resolution: Literal["irrelevant", "won", "pending"]
    dynamic_id: str = ""


@router.get("/maintenance/messages")
def message_reviews():
    from src.message_review import review_status
    return review_status(current_uid())


@router.post("/maintenance/messages/resolve")
def resolve_message_review(request: MessageResolutionRequest):
    from src.message_review import resolve_message
    return checked_call(resolve_message, current_uid(), request.key, request.resolution, request.dynamic_id)
