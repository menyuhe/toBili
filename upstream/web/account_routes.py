"""Explicit per-request identity, with legacy single-account compatibility."""
import uuid
from urllib.parse import parse_qs
from fastapi import APIRouter, HTTPException
from starlette.responses import JSONResponse, FileResponse
from src.account_context import account_scope, scoped_uid
from src import accounts

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        query = parse_qs(scope.get("query_string", b"").decode())
        uid = headers.get(b"x-account-uid", b"").decode() or query.get("account_uid", [""])[0]
        explicit = bool(uid)
        if uid == "none":
            uid = ""
        elif uid:
            if not uid.isdigit() or accounts.read_state(uid, "account") is None:
                return await JSONResponse({"detail": "账号不存在"}, status_code=404)(scope, receive, send)
        else:
            registered = accounts.list_accounts()
            if len(registered) > 1 and not scope["path"].startswith("/api/accounts"):
                return await JSONResponse({"detail": "多账号请求必须指定 X-Account-UID"}, status_code=400)(scope, receive, send)
            uid = accounts.default_uid()
        with account_scope(uid):
            await self.app(scope, receive, send)


@router.get("")
def get_accounts():
    from web.job_runner import runner
    from src.message_review import review_status
    items = accounts.list_accounts()
    shared = runner.for_uid("__global__").get_status().to_dict()
    for item in items:
        uid = item["uid"]
        job = runner.for_uid(uid).get_status().to_dict()
        if job["state"] != "running" and shared["state"] == "running" and shared["uid"] == uid:
            job = shared
        item["job"] = {key: job[key] for key in ("id", "state", "action", "uid", "scope", "label",
                                                "progress_message", "progress_step", "progress_total", "cancel_requested")}
        item["messages"] = {key: value for key, value in review_status(uid).items() if key != "items"}
    return {"items": items}


@router.post("/login")
def add_login(expected_uid: str = ""):
    from web.job_runner import runner
    if expected_uid and not accounts.read_state(expected_uid, "account"):
        raise HTTPException(404, "账号不存在")
    session_id = uuid.uuid4().hex
    with account_scope(expected_uid, session_id):
        job_id = runner.try_start("login", {"login_session": session_id, "expected_uid": expected_uid})
    if job_id is None:
        raise HTTPException(409, "该账号仍有任务正在执行")
    return {"session_id": session_id, "job_id": job_id}


@router.get("/login/{session_id}")
def login_status(session_id: str):
    from web.job_runner import runner
    return runner.login_status(session_id)


@router.get("/login/{session_id}/qrcode")
def login_qrcode(session_id: str):
    from src.bilibili_login import qrcode_path
    from web.job_runner import runner
    runner.login_status(session_id)
    with account_scope("", session_id):
        path = qrcode_path()
    if not path.exists():
        raise HTTPException(404, "二维码尚未生成")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/login/{session_id}/cancel")
def cancel_login(session_id: str):
    from web.job_runner import runner
    return {"ok": runner.cancel_login(session_id)}


@router.post("/{uid}/logout")
def logout(uid: str):
    from web.job_runner import runner
    from web.auto_scheduler import auto_scheduler
    if uid != scoped_uid():
        raise HTTPException(409, "请求账号不一致")
    with account_scope(uid):
        auto_scheduler.stop(reason="账号退出")
        runner.cancel_pending(uid)
        runner.for_uid(uid).cancel()
        shared = runner.for_uid("__global__")
        if shared.get_status().uid == uid:
            shared.cancel()
        accounts.logout(uid)
    return {"ok": True}
