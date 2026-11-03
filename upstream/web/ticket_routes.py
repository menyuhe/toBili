import time
from typing import Literal
from fastapi import APIRouter
from pydantic import Field
from src import tickets
from src.bilibili_client import BilibiliClient
from src.ticket_api import TicketApi, project_id
from web.maintenance_routes import StrictRequest, checked_call, current_uid

router = APIRouter(prefix="/api/tickets", tags=["stable"])


class ProjectRequest(StrictRequest):
    url: str = Field(min_length=1, max_length=2000)


class TicketRequest(StrictRequest):
    project_id: str = Field(pattern=r"^[1-9][0-9]{0,15}$")
    screen_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    sku_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    buyer_ids: list[str] = Field(min_length=1, max_length=10)
    address_id: str = Field(default="", pattern=r"^(|[1-9][0-9]{0,19})$")
    amount: int = Field(ge=0, le=100000000)
    start_at: int = Field(ge=0, le=4102444800)
    interval: int = Field(default=2, ge=2, le=60)
    duration: int = Field(default=60, ge=10, le=600)
    contact_name: str = Field(min_length=1, max_length=80)
    contact_phone: str = Field(min_length=6, max_length=20)


class ResolveRequest(StrictRequest):
    outcome: Literal["completed", "closed"]


@router.post("/project")
def project(request: ProjectRequest):
    uid = current_uid()
    pid = checked_call(project_id, request.url)
    with BilibiliClient(warmup=False) as client:
        api = TicketApi(client, uid)
        api.verify_login()
        return {"uid": uid, **checked_call(api.context, pid)}


@router.get("/tasks")
def list_tasks():
    uid = current_uid()
    return {"uid": uid, "server_time": int(time.time()), "items": tickets.list_tasks(uid)}


@router.post("/tasks")
def save_task(request: TicketRequest):
    return checked_call(tickets.save_task, current_uid(), request.model_dump())


@router.post("/tasks/{task_id}/start")
def start(task_id: str):
    return checked_call(tickets.start_task, current_uid(), task_id)


@router.post("/tasks/{task_id}/cancel")
def cancel(task_id: str):
    checked_call(tickets.cancel_task, current_uid(), task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/check")
def check(task_id: str):
    uid = current_uid(); task = checked_call(tickets.get_task, uid, task_id)
    if task["order_id"]:
        with BilibiliClient(warmup=False) as client:
            api = TicketApi(client, uid); api.verify_login()
            payable = checked_call(api.payment_available, task["order_id"])
        if payable:
            tickets.transition(uid, task_id, "payment", "平台确认此订单仍可支付，请及时打开订单页")
    return tickets.get_task(uid, task_id)


@router.post("/tasks/{task_id}/resolve")
def resolve(task_id: str, request: ResolveRequest):
    uid = current_uid(); task = checked_call(tickets.get_task, uid, task_id)
    if task["state"] not in {"payment", "unknown", "order_review"}:
        from web.api_errors import AppError, ErrorCode
        raise AppError(ErrorCode.VALIDATION_ERROR, "仅可人工核对待支付或结果未知的任务")
    tickets.transition(uid, task_id, request.outcome, "用户已在官方订单页核实：" + ("已支付" if request.outcome == "completed" else "订单已关闭／未生成订单"))
    return tickets.get_task(uid, task_id)
