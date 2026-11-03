"""Account-scoped ticket task state machine. Persist intent before every order write."""
from __future__ import annotations
import json
import re
import threading
import time
import uuid
from sqlmodel import select
from src.app_paths import user_home
from src.bilibili_auth import get_login_uid
from src.bilibili_client import BilibiliClient
from src.db.models import TicketTaskRow
from src.db.session import session_scope
from src.notifications import enqueue
from src.secure_files import write_text_secret
from src.task_control import check_cancelled, cancellable_sleep
from src.ticket_api import TicketApi, project_id

_lock = threading.RLock()
ACTIVE = {"waiting", "preparing", "ordering"}
UNRESOLVED = ACTIVE | {"unknown", "payment", "order_review"}


def get_task(uid, task_id):
    with session_scope() as session:
        row = session.get(TicketTaskRow, task_id)
        if not row or row.uid != uid:
            raise ValueError("未找到当前账号的会员购任务")
        data = row.model_dump()
    data["config"] = json.loads(data.pop("config_json"))
    data["history"] = json.loads(data.pop("history_json"))
    data["order_url"] = f"https://show.bilibili.com/platform/orderDetail.html?order_id={data['order_id']}" if data["order_id"] else ""
    return data


def list_tasks(uid):
    with session_scope() as session:
        ids = [r.id for r in session.exec(select(TicketTaskRow).where(TicketTaskRow.uid == uid).order_by(TicketTaskRow.created_at.desc()).limit(100)).all()]
    return [get_task(uid, i) for i in ids]


def transition(uid, task_id, state, message, *, order_id=None, attempts=None):
    with _lock, session_scope() as session:
        row = session.get(TicketTaskRow, task_id)
        if not row or row.uid != uid:
            raise ValueError("任务不存在")
        history = json.loads(row.history_json)
        history.append(dict(state=state, message=message, at=int(time.time())))
        row.history_json = json.dumps(history[-100:], ensure_ascii=False)
        row.state, row.message, row.updated_at = state, message, int(time.time())
        if order_id is not None:
            row.order_id = order_id
        if attempts is not None:
            row.attempts = attempts


def contact_path():
    return user_home() / "config" / "ticket_contacts.json"


def save_task(uid, values):
    config = {k: values[k] for k in ("project_id", "screen_id", "sku_id", "buyer_ids", "address_id", "amount", "start_at", "interval", "duration")}
    config["project_id"] = project_id(config["project_id"])
    if len(set(config["buyer_ids"])) != len(config["buyer_ids"]):
        raise ValueError("购票人不可重复")
    contact = {"name": values["contact_name"].strip(), "phone": values["contact_phone"].strip()}
    if not contact["name"] or not re.fullmatch(r"[0-9+ -]{6,20}", contact["phone"]):
        raise ValueError("请填写联系人及有效联系电话")
    with BilibiliClient(warmup=False) as client:
        api = TicketApi(client, uid); api.verify_login()
        api.payload(config, contact)
        project = api.project(config["project_id"])
        option = next(o for o in project["options"] if o["screen_id"] == config["screen_id"] and o["sku_id"] == config["sku_id"])
    config.update(name=project["name"], label=option["label"], url=project["url"])
    now = int(time.time()); task_id = uuid.uuid4().hex
    with _lock:
        contacts = json.loads(contact_path().read_text(encoding="utf-8")) if contact_path().exists() else {}
        contacts[task_id] = {"uid": uid, **contact}
        write_text_secret(contact_path(), json.dumps(contacts, ensure_ascii=False))
        with session_scope() as session:
            session.add(TicketTaskRow(id=task_id, uid=uid, config_json=json.dumps(config, ensure_ascii=False),
                created_at=now, updated_at=now, start_at=config["start_at"]))
    return get_task(uid, task_id)


def start_task(uid, task_id):
    with _lock:
        task = get_task(uid, task_id)
        if task["state"] not in {"draft", "paused", "handoff", "failed", "cancelled"}:
            raise ValueError("该任务不能重复开始；已有订单或不确定结果请先在官方订单页核实")
        # An earlier ambiguous create remains unresolved even if the user cancelled it.
        if any(h["state"] in {"ordering", "unknown", "order_review", "payment"} for h in task["history"]):
            raise ValueError("该任务曾提交下单请求，请从官方订单页面继续处理")
        with session_scope() as session:
            unresolved_ids = [r.id for r in session.exec(select(TicketTaskRow).where(TicketTaskRow.uid == uid, TicketTaskRow.state.in_(UNRESOLVED))).all()]
        for other in (get_task(uid, i) for i in unresolved_ids):
            if other["id"] == task_id:
                continue
            if other["state"] in ACTIVE or (other["config"]["project_id"] == task["config"]["project_id"] and other["state"] in UNRESOLVED):
                raise ValueError("已有运行中的会员购任务，或该项目仍有待核实／待支付订单")
        transition(uid, task_id, "waiting", "等待开售；软件需保持运行")
    return get_task(uid, task_id)


def cancel_task(uid, task_id):
    from web.job_runner import runner
    with _lock:
        task = get_task(uid, task_id)
        status = runner.get_status()
        if task["state"] in {"preparing", "ordering"} and status.action == "ticket_run" and runner.is_running():
            runner.cancel()
            return
        if task["state"] in {"payment", "unknown", "order_review"}:
            raise ValueError("本地取消不会取消平台订单，请打开官方订单页处理")
        transition(uid, task_id, "cancelled", "任务已取消，未继续发起请求")


def recover_tasks():
    with session_scope() as session:
        rows = [(r.uid, r.id, r.state) for r in session.exec(select(TicketTaskRow).where(TicketTaskRow.state.in_(ACTIVE))).all()]
    for uid, task_id, state in rows:
        transition(uid, task_id, "unknown" if state == "ordering" else "paused",
                   "上次执行中断；请核实官方订单，不自动重发" if state == "ordering" else "软件已重启，请核对时间与配置后继续")


def notify(uid, task_id, state, message):
    try:
        enqueue(uid, "ticket", f"ticket:{task_id}:{state}", "会员购提醒", message)
    except Exception:
        pass  # delivery cannot change the purchase result


def run_task(uid, task_id, progress):
    with _lock:
        task = get_task(uid, task_id)
        if task["state"] != "waiting":
            return {"ok": True, "message": "会员购任务状态已改变"}
        transition(uid, task_id, "preparing", "正在核对账号、票价和购票人")
    if str(get_login_uid() or "") != uid:
        transition(uid, task_id, "paused", "账号已切换，请重新核对")
        return {"ok": False, "message": "账号已切换"}
    config = task["config"]
    ambiguous = False
    try:
        contact = json.loads(contact_path().read_text(encoding="utf-8"))[task_id]
        if contact["uid"] != uid:
            raise ValueError("联系人账号不一致")
        with BilibiliClient(warmup=False) as client:
            api = TicketApi(client, uid); api.verify_login()
            payload = api.payload(config, contact)
            deadline = time.time() + config["duration"]
            attempt = 0
            while time.time() <= deadline:
                check_cancelled(); api.assert_account()
                attempt += 1
                transition(uid, task_id, "preparing", "正在准备订单", attempts=attempt)
                progress(step=1, total=3, message=f"会员购：准备订单（第 {attempt} 次）")
                prepared = api.prepare(payload)
                code = prepared.get("errno", prepared.get("code"))
                data = prepared.get("data") or {}
                if code != 0 or not isinstance(data, dict) or not data.get("token"):
                    transition(uid, task_id, "handoff", f"订单准备需手动处理（代码 {code}）；请在官方购票页完成验证后继续")
                    notify(uid, task_id, "handoff", "会员购需手动处理，请打开官方购票页")
                    break
                check_cancelled(); api.assert_account()
                if time.time() > deadline:
                    transition(uid, task_id, "failed", "准备订单已超过本次尝试时限，未提交下单")
                    break
                transition(uid, task_id, "ordering", "已提交下单请求，等待平台确认")
                ambiguous = True
                progress(step=2, total=3, message="会员购：提交订单")
                response = api.create(payload, str(data["token"]))
                code = response.get("errno", response.get("code"))
                data = response.get("data") or {}
                oid = str(data.get("orderId") or "") if isinstance(data, dict) else ""
                if code in (0, 100048) and re.fullmatch(r"[1-9][0-9]{0,24}", oid):
                    transition(uid, task_id, "payment", "订单已生成，请打开官方订单页支付", order_id=oid)
                    ambiguous = False
                    notify(uid, task_id, "payment", f"{config['name']} 已生成订单 {oid}，请及时支付")
                    break
                if code == 100009:  # explicit sold-out business result; no order was created
                    ambiguous = False
                    transition(uid, task_id, "preparing", "暂时无票，等待下一次尝试")
                    cancellable_sleep(config["interval"])
                    continue
                if code == 100034:
                    ambiguous = False
                    transition(uid, task_id, "failed", "平台返回金额变化，请重新核对价格；未自动接受涨价")
                    break
                transition(uid, task_id, "unknown", f"下单结果待核实（代码 {code}）；请查看官方订单，不自动重发")
                notify(uid, task_id, "unknown", "会员购下单结果待核实，请查看官方订单")
                ambiguous = False
                break
            else:
                transition(uid, task_id, "failed", "到达本次尝试时限，已停止")
    except BaseException as exc:
        from src.task_control import TaskCancelled
        state = "unknown" if ambiguous else "cancelled" if isinstance(exc, TaskCancelled) else "failed"
        message = "下单响应中断，请核实官方订单，不自动重发" if ambiguous else "会员购已停止，请检查账号、配置或网络后重试"
        # Never log API payloads, buyer details or contact data.
        transition(uid, task_id, state, message)
        notify(uid, task_id, state, message)
        if isinstance(exc, (KeyboardInterrupt, SystemExit, TaskCancelled)):
            raise
    final = get_task(uid, task_id)
    progress(step=3, total=3, message=final["message"])
    return {"ok": final["state"] in {"payment", "handoff"}, "message": final["message"], "result": {"task_id": task_id, "state": final["state"]}}


def tick(job_runner, now=None):
    uid = str(get_login_uid() or "")
    if not uid or job_runner.is_running():
        return False
    now = int(time.time()) if now is None else now
    with _lock:
        with session_scope() as session:
            waiting = [r.id for r in session.exec(select(TicketTaskRow).where(TicketTaskRow.uid == uid, TicketTaskRow.state == "waiting").order_by(TicketTaskRow.start_at)).all()]
        for task in (get_task(uid, task_id) for task_id in waiting):
            if task["state"] == "waiting" and task["start_at"] <= now:
                if now > max(task["start_at"], task["updated_at"]) + task["config"]["duration"]:
                    transition(uid, task["id"], "paused", "已错过本次开始窗口，请核对后继续")
                    continue
                return job_runner.try_start("ticket_run", {"uid": uid, "task_id": task["id"]}, source="system") is not None
    return False
