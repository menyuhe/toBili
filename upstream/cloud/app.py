"""Credential-free sync relay and mobile PWA. Run with uvicorn --factory."""
from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from cloud.models import Account, Base, Device, LoginAttempt, Receipt, Record, Task, User
from cloud.security import digest, hash_password, verify_password
from src.cloud_protocol import Change, StrictModel, canonical, task_params

LEASE_SECONDS = 60
SESSION_SECONDS = 30 * 86400
STATIC = Path(__file__).with_name("static")


def new_id():
    return uuid.uuid4().hex


class Store:
    def __init__(self, url):
        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).resolve().parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, pool_pre_ping=True,
                                    connect_args={"check_same_thread": False, "timeout": 20} if url.startswith("sqlite:") else {})
        Base.metadata.create_all(self.engine)

    @contextmanager
    def transaction(self):
        with self.engine.connect() as conn:
            if conn.dialect.name == "sqlite":
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            with Session(bind=conn, expire_on_commit=False) as session:
                try:
                    yield session
                    session.flush()
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise


class Credentials(StrictModel):
    username: str = Field(pattern=r"^[a-zA-Z0-9_\-]{3,64}$")
    password: str = Field(min_length=12, max_length=128)
    device_name: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern=r"^(windows|mac|pwa)$")
    invite: str = Field(default="", max_length=256)


class Bind(StrictModel):
    bili_uid: str = Field(pattern=r"^[1-9][0-9]{0,19}$")


class Heartbeat(Bind):
    ready: bool


class Push(Bind):
    changes: list[Change] = Field(max_length=100)


class NewTask(Bind):
    request_key: str = Field(pattern=r"^[a-zA-Z0-9_:\-]{8,100}$")
    action: str = Field(max_length=40)
    params: dict = Field(default_factory=dict)


class Acquire(Bind):
    task_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    revision: int = Field(ge=0)


class Lease(Bind):
    lease_token: str = Field(min_length=32, max_length=128)
    revision: int = Field(ge=0)


class Finish(Lease):
    state: str = Field(pattern=r"^(success|failed|unknown|cancelled)$")
    message: str = Field(default="", max_length=500)


def wire_record(row):
    return {"kind": row.kind, "key": row.key, "version": row.version,
            "deleted": row.deleted, "value": json.loads(row.value_json)}


def wire_task(row):
    return {"id": row.id, "bili_uid": row.bili_uid, "action": row.action, "params": json.loads(row.params_json),
            "state": row.state, "executor": row.executor, "target_device": row.target_device, "created_at": row.created_at,
            "updated_at": row.updated_at, "message": row.message, "queue_order": row.queue_order}


def expire(session, account):
    if account.lease_task and account.lease_until <= int(time.time()):
        account.quarantine = True
        task = session.get(Task, account.lease_task)
        if task and task.state == "running":
            task.state, task.message = "unknown", "执行设备失联；请核实操作结果，任务不会自动重跑"
            task.updated_at = int(time.time())


def create_app(database_url=None, public_origin=None, registration_invite=None):
    origin = (public_origin or os.environ.get("RELAY_PUBLIC_ORIGIN", "http://localhost:8787")).rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path or parsed.username or parsed.password:
        raise ValueError("RELAY_PUBLIC_ORIGIN 必须是无路径的 HTTP(S) 地址")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("公网部署必须使用 HTTPS")
    invite = registration_invite if registration_invite is not None else os.environ.get("RELAY_REGISTRATION_INVITE", "")
    store = Store(database_url or os.environ.get("RELAY_DATABASE_URL", "sqlite:///data/relay.db"))
    app = FastAPI(title="toBili 多端中转", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.public_origin = origin

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _exc):
        # Never echo invalid passwords, bearer tokens or rejected credential fields.
        return JSONResponse({"detail": "请求字段或格式无效"}, status_code=422)

    @app.middleware("http")
    async def guard(request, call_next):
        supplied_origin = request.headers.get("origin")
        if supplied_origin and supplied_origin != origin:
            return JSONResponse({"detail": "请求来源无效"}, status_code=403)
        # Desktop bearer requests don't rely on ambient browser credentials.
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.headers.get("authorization"):
            if supplied_origin != origin and request.headers.get("sec-fetch-site") != "same-origin":
                return JSONResponse({"detail": "缺少同源验证"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if parsed.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    class BodyLimit:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                size += len(message.get("body", b""))
                if size > 512 * 1024:
                    return await JSONResponse({"detail": "请求过大"}, status_code=413)(scope, receive, send)
                chunks.append(message.get("body", b""))
                if not message.get("more_body"):
                    break
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
                return await receive()
            await self.app(scope, replay, send)

    app.add_middleware(BodyLimit)

    def transaction():
        with store.transaction() as session:
            yield session

    def auth(request: Request, session=Depends(transaction)):
        header = request.headers.get("authorization", "")
        token = header[7:] if header.startswith("Bearer ") else request.cookies.get("tobili_session", "")
        if not token or len(token) > 128:
            raise HTTPException(401, "请登录应用账号")
        device = session.scalar(select(Device).where(Device.token_hash == digest(token)))
        if not device or device.revoked or device.expires_at <= int(time.time()):
            raise HTTPException(401, "登录已过期或设备已解绑")
        # Serializes all mutations of this user's devices/accounts on PostgreSQL too.
        session.scalar(select(User).where(User.id == device.user_id).with_for_update())
        session.refresh(device)
        if device.revoked:
            raise HTTPException(401, "设备已解绑")
        device.last_seen = int(time.time())
        return device

    def account_for(session, device, uid):
        account = session.get(Account, (device.user_id, uid))
        if account is None:
            raise HTTPException(404, "尚未绑定此 B 站账号")
        expire(session, account)
        return account

    def desktop(device, uid=None):
        if device.kind not in {"windows", "mac"} or (uid and device.bili_uid != uid):
            raise HTTPException(403, "需要已绑定此账号的桌面设备")

    def lease_for(session, device, body, *, fresh=False):
        desktop(device, body.bili_uid)
        account = account_for(session, device, body.bili_uid)
        if (account.quarantine or account.lease_device != device.id or not account.lease_hash
                or not secrets.compare_digest(account.lease_hash, digest(body.lease_token))
                or account.lease_until <= int(time.time())):
            raise HTTPException(409, "执行租约失效，已暂停写入")
        if fresh and account.revision != body.revision:
            raise HTTPException(409, "云端数据已更新，请同步后重新执行")
        return account

    def issue_session(session, user, body, response):
        raw = secrets.token_urlsafe(48)
        device = Device(id=new_id(), user_id=user.id, name=body.device_name, kind=body.kind,
                        token_hash=digest(raw), expires_at=int(time.time()) + SESSION_SECONDS,
                        last_seen=int(time.time()), bili_uid="", ready=False)
        session.add(device)
        response.set_cookie("tobili_session", raw, max_age=SESSION_SECONDS, httponly=True,
                            secure=parsed.scheme == "https", samesite="strict", path="/")
        result = {"user_id": user.id, "username": user.username, "device_id": device.id, "protocol": 1}
        if body.kind != "pwa":
            result["device_token"] = raw
        return result

    def throttle(session, request, username):
        # Never trust X-Forwarded-For from an arbitrary peer.
        key = digest(f"{request.client.host if request.client else 'local'}:{username.lower()}")
        if session.bind.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(key[:15], 16)})
        row = session.get(LoginAttempt, key)
        window = int(time.time()) // 900
        if row is None:
            row = LoginAttempt(key=key, window=window, count=0)
            session.add(row)
        if row.window != window:
            row.window, row.count = window, 0
        row.count += 1
        blocked = row.count > 15
        session.flush()
        return blocked

    @app.get("/api/v1/health")
    async def health():
        return {"ok": True, "protocol": 2, "mode": "activity_catalog", "executor": False}

    @app.post("/api/v1/auth/register")
    async def register(body: Credentials, request: Request, response: Response):
        with store.transaction() as session:
            blocked = throttle(session, request, body.username)
        if blocked:
            raise HTTPException(429, "尝试过于频繁，请稍后重试")
        if not invite or not secrets.compare_digest(digest(body.invite), digest(invite)):
            raise HTTPException(403, "注册未开放或邀请码无效")
        try:
            with store.transaction() as session:
                user = User(id=new_id(), username=body.username.lower(), password_hash=hash_password(body.password))
                session.add(user)
                session.flush()
                return issue_session(session, user, body, response)
        except IntegrityError:
            raise HTTPException(409, "账号不可用") from None

    @app.post("/api/v1/auth/login")
    async def login(body: Credentials, request: Request, response: Response):
        with store.transaction() as session:
            blocked = throttle(session, request, body.username)
        if blocked:
            raise HTTPException(429, "尝试过于频繁，请稍后重试")
        with store.transaction() as session:
            user = session.scalar(select(User).where(User.username == body.username.lower()))
            # Constant-cost password derivation for unknown accounts as well.
            encoded = user.password_hash if user else f"600000${'00' * 16}${'00' * 32}"
            valid = verify_password(body.password, encoded)
            if not user or not valid:
                raise HTTPException(401, "账号或密码错误")
            return issue_session(session, user, body, response)

    @app.get("/api/v1/me")
    async def me(device=Depends(auth), session=Depends(transaction)):
        return {"user_id": device.user_id, "username": session.get(User, device.user_id).username,
                "device_id": device.id, "protocol": 1}

    @app.get("/api/v1/devices")
    async def devices(device=Depends(auth), session=Depends(transaction)):
        rows = session.scalars(select(Device).where(Device.user_id == device.user_id).order_by(Device.last_seen.desc())).all()
        return {"items": [{"id": d.id, "name": d.name, "kind": d.kind, "bili_uid": d.bili_uid,
                           "last_seen": d.last_seen, "revoked": d.revoked,
                           "online": not d.revoked and d.ready and d.last_seen > int(time.time()) - 45} for d in rows]}

    @app.post("/api/v1/devices/{device_id}/revoke")
    async def revoke(device_id: str, device=Depends(auth), session=Depends(transaction)):
        target = session.get(Device, device_id)
        if not target or target.user_id != device.user_id:
            raise HTTPException(404, "设备不存在")
        target.revoked, target.ready = True, False
        for account in session.scalars(select(Account).where(Account.user_id == device.user_id,
                                                             Account.lease_device == target.id)).all():
            account.lease_until = int(time.time())
            expire(session, account)
        return {"ok": True}

    @app.post("/api/v1/auth/logout")
    async def logout(response: Response, device=Depends(auth)):
        device.revoked, device.ready = True, False
        response.delete_cookie("tobili_session", path="/")
        return {"ok": True}

    @app.post("/api/v1/accounts/bind")
    async def bind(body: Bind, device=Depends(auth), session=Depends(transaction)):
        desktop(device)
        active = session.scalar(select(Account).where(Account.user_id == device.user_id, Account.lease_device == device.id,
                                                       Account.lease_task != ""))
        if active and active.bili_uid != body.bili_uid:
            raise HTTPException(409, "请先完成当前账号任务")
        if not session.get(Account, (device.user_id, body.bili_uid)):
            session.add(Account(user_id=device.user_id, bili_uid=body.bili_uid))
        device.bili_uid, device.last_seen = body.bili_uid, int(time.time())
        return {"ok": True}

    @app.get("/api/v1/accounts")
    async def accounts(device=Depends(auth), session=Depends(transaction)):
        result = []
        for row in session.scalars(select(Account).where(Account.user_id == device.user_id)).all():
            expire(session, row)
            result.append({"bili_uid": row.bili_uid, "revision": row.revision,
                           "executor": row.lease_device, "quarantine": row.quarantine,
                           "lease_until": row.lease_until})
        return {"items": result}

    @app.post("/api/v1/devices/heartbeat")
    async def heartbeat(body: Heartbeat, device=Depends(auth), session=Depends(transaction)):
        desktop(device, body.bili_uid)
        account_for(session, device, body.bili_uid)
        device.last_seen, device.ready = int(time.time()), body.ready
        return {"ok": True}

    def update_record(session, account, change):
        row = session.get(Record, (account.user_id, account.bili_uid, change.kind, change.key))
        current = row.version if row else 0
        if current != change.base_version:
            return {"conflict": wire_record(row) if row else None}
        if row and not row.deleted:
            old = json.loads(row.value_json)
            if ((change.kind == "outcome" and old.get("outcome") == "won" and change.value.get("outcome") != "won")
                    or (change.kind == "participation" and old.get("user_status") == "已参加" and change.value.get("user_status") != "已参加")):
                return {"conflict": wire_record(row), "reason": "已确认中奖或参与记录不能降级"}
        account.revision += 1
        if row is None:
            row = Record(user_id=account.user_id, bili_uid=account.bili_uid, kind=change.kind, key=change.key,
                         version=account.revision)
            session.add(row)
        row.version, row.deleted, row.value_json = account.revision, change.deleted, canonical(change.value)
        session.flush()
        return {"record": wire_record(row)}

    @app.post("/api/v1/sync/push")
    async def push(body: Push, device=Depends(auth), session=Depends(transaction)):
        desktop(device, body.bili_uid)
        account = account_for(session, device, body.bili_uid)
        if account.lease_task and account.lease_device != device.id:
            raise HTTPException(409, "其他设备执行中，稍后同步本地修改")
        results = []
        for change in body.changes:
            receipt = session.get(Receipt, (device.user_id, body.bili_uid, change.change_id))
            fingerprint = digest(canonical(change.model_dump()))
            if receipt:
                if receipt.request_hash != fingerprint:
                    raise HTTPException(409, "同步操作 ID 已被使用")
                result = json.loads(receipt.response_json)
            else:
                result = update_record(session, account, change)
                if "record" in result:
                    session.add(Receipt(user_id=device.user_id, bili_uid=body.bili_uid, change_id=change.change_id,
                                        request_hash=fingerprint, response_json=canonical(result)))
            results.append({"change_id": change.change_id, **result})
        return {"results": results, "revision": account.revision}

    @app.get("/api/v1/sync/pull")
    async def pull(bili_uid: str = Query(pattern=r"^[1-9][0-9]{0,19}$"), after: int = Query(0, ge=0),
                   limit: int = Query(100, ge=1, le=200), device=Depends(auth), session=Depends(transaction)):
        account = account_for(session, device, bili_uid)
        if after > account.revision:
            raise HTTPException(409, "同步游标超前，请重新连接")
        rows = session.scalars(select(Record).where(Record.user_id == device.user_id, Record.bili_uid == bili_uid,
                                                   Record.version > after).order_by(Record.version).limit(limit + 1)).all()
        more = len(rows) > limit
        rows = rows[:limit]
        return {"items": [wire_record(r) for r in rows], "cursor": rows[-1].version if more else account.revision,
                "has_more": more, "revision": account.revision}

    @app.get("/api/v1/records")
    async def records(bili_uid: str, kind: str = "activity", after: str = "", limit: int = Query(50, ge=1, le=200),
                      device=Depends(auth), session=Depends(transaction)):
        account_for(session, device, bili_uid)
        rows = session.scalars(select(Record).where(Record.user_id == device.user_id, Record.bili_uid == bili_uid,
                                                   Record.kind == kind, Record.key > after, Record.deleted.is_(False))
                               .order_by(Record.key).limit(limit + 1)).all()
        return {"items": [wire_record(r) for r in rows[:limit]], "has_more": len(rows) > limit}

    @app.get("/api/v1/activities")
    async def activities(bili_uid: str, after: str = "", limit: int = Query(50, ge=1, le=100),
                         view: Literal["active", "archived", "all"] = "active",
                         device=Depends(auth), session=Depends(transaction)):
        account = account_for(session, device, bili_uid)
        scope = (Record.user_id == device.user_id, Record.bili_uid == bili_uid,
                 Record.kind == "activity", Record.deleted.is_(False))
        archive = aliased(Record)
        archived = select(archive.key).where(
            archive.user_id == device.user_id, archive.bili_uid == bili_uid,
            archive.kind == "archive", archive.deleted.is_(False), archive.key == Record.key).exists()
        total = session.scalar(select(func.count()).select_from(Record).where(*scope))
        archived_total = session.scalar(select(func.count()).select_from(Record).where(*scope, archived))
        counts = {"all": total, "active": total - archived_total, "archived": archived_total}
        # Apply the visibility filter before the page limit, so archived history cannot consume the page.
        filters = scope + ((~archived,) if view == "active" else (archived,) if view == "archived" else ())
        rows = session.scalars(select(Record).where(*filters, Record.key > after)
                               .order_by(Record.key).limit(limit + 1)).all()
        result = []
        for row in rows[:limit]:
            values = {}
            for kind in ("participation", "outcome", "keep", "archive"):
                extra = session.get(Record, (device.user_id, bili_uid, kind, row.key))
                values[kind] = wire_record(extra) if extra and not extra.deleted else None
            result.append({**wire_record(row), **values})
        return {"items": result, "has_more": len(rows) > limit, "revision": account.revision,
                "view": view, "total": counts[view], "counts": counts}

    @app.post("/api/v1/records/keep")
    async def keep(body: Push, device=Depends(auth), session=Depends(transaction)):
        account = account_for(session, device, body.bili_uid)
        if any(c.kind != "keep" for c in body.changes):
            raise HTTPException(422, "手机端仅允许修改保留标记")
        if any(not session.get(Record, (device.user_id, body.bili_uid, "activity", c.key)) for c in body.changes):
            raise HTTPException(404, "活动尚未同步")
        # A keep edit advances revision, invalidating every old executor write check.
        return {"results": [{"change_id": c.change_id, **update_record(session, account, c)} for c in body.changes],
                "revision": account.revision}

    @app.post("/api/v1/tasks")
    async def submit(body: NewTask, device=Depends(auth), session=Depends(transaction)):
        account = account_for(session, device, body.bili_uid)
        try:
            params = task_params(body.action, body.params, remote=device.kind == "pwa")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if params.get("uid") and params["uid"] != body.bili_uid:
            raise HTTPException(422, "任务账号不匹配")
        prior = session.scalar(select(Task).where(Task.user_id == device.user_id, Task.bili_uid == body.bili_uid,
                                                  Task.request_key == body.request_key))
        if prior:
            if prior.action != body.action or prior.params_json != canonical(params):
                raise HTTPException(409, "请求 ID 已用于其他任务")
            return wire_task(prior)
        if body.action in {"participate", "participate_triple"} and params.get("dynamic_id"):
            did = params["dynamic_id"]
            joined = session.get(Record, (device.user_id, body.bili_uid, "participation", did))
            if joined and json.loads(joined.value_json).get("user_status") == "已参加":
                raise HTTPException(409, "此活动已参加")
            existing = session.scalars(select(Task).where(Task.user_id == device.user_id, Task.bili_uid == body.bili_uid,
                                                         Task.action.in_(["participate", "participate_triple"]),
                                                         Task.state.in_(["queued", "running", "unknown", "success"]))).all()
            for row in existing:
                if json.loads(row.params_json).get("dynamic_id") == did:
                    return wire_task(row)
        queued = session.scalars(select(Task.id).where(Task.user_id == device.user_id, Task.state == "queued").limit(201)).all()
        if len(queued) >= 200:
            raise HTTPException(409, "等待任务过多，请先处理队列")
        now = int(time.time())
        account.task_sequence += 1
        row = Task(id=new_id(), user_id=device.user_id, bili_uid=body.bili_uid, request_key=body.request_key,
                   action=body.action, params_json=canonical(params), state="queued", executor="", created_at=now,
                   target_device=device.id if device.kind != "pwa" else "",
                   queue_order=account.task_sequence,
                   updated_at=now, message="等待执行设备")
        session.add(row)
        session.flush()
        return wire_task(row)

    @app.get("/api/v1/tasks")
    async def tasks(bili_uid: str, device=Depends(auth), session=Depends(transaction)):
        account_for(session, device, bili_uid)
        # Include oldest queued tasks even when >100 newer completed tasks exist.
        waiting = session.scalars(select(Task).where(Task.user_id == device.user_id, Task.bili_uid == bili_uid,
                                                     Task.state == "queued").order_by(Task.queue_order).limit(200)).all()
        recent = session.scalars(select(Task).where(Task.user_id == device.user_id, Task.bili_uid == bili_uid,
                                                    Task.state != "queued").order_by(Task.created_at.desc()).limit(100)).all()
        return {"items": [wire_task(r) for r in waiting + recent]}

    @app.post("/api/v1/tasks/{task_id}/cancel")
    async def cancel(task_id: str, device=Depends(auth), session=Depends(transaction)):
        row = session.get(Task, task_id)
        if not row or row.user_id != device.user_id:
            raise HTTPException(404, "任务不存在")
        if row.state != "queued":
            raise HTTPException(409, "只能取消尚未开始的任务；执行中请在桌面端停止")
        row.state, row.message, row.updated_at = "cancelled", "用户取消了等待任务", int(time.time())
        return {"ok": True}

    @app.post("/api/v1/leases/acquire")
    async def acquire(body: Acquire, device=Depends(auth), session=Depends(transaction)):
        desktop(device, body.bili_uid)
        account = account_for(session, device, body.bili_uid)
        if account.quarantine or account.lease_task:
            raise HTTPException(409, "已有执行端或失联任务待核实")
        if body.revision != account.revision:
            raise HTTPException(409, "数据已更新，请先同步")
        task = session.get(Task, body.task_id)
        if not task or task.user_id != device.user_id or task.bili_uid != body.bili_uid or task.state != "queued":
            raise HTTPException(409, "任务已被领取或已结束")
        if task.target_device and task.target_device != device.id:
            raise HTTPException(409, "此本地任务属于另一台设备")
        raw = secrets.token_urlsafe(48)
        now = int(time.time())
        account.lease_device, account.lease_hash = device.id, digest(raw)
        account.lease_until, account.lease_task = now + LEASE_SECONDS, task.id
        task.state, task.executor, task.updated_at, task.message = "running", device.id, now, "桌面端执行中"
        device.ready, device.last_seen = True, now
        return {"lease_token": raw, "expires_at": account.lease_until, "task": wire_task(task), "revision": account.revision}

    @app.post("/api/v1/leases/check")
    async def check(body: Lease, device=Depends(auth), session=Depends(transaction)):
        account = lease_for(session, device, body, fresh=True)
        return {"ok": True, "expires_at": account.lease_until}

    @app.post("/api/v1/leases/heartbeat")
    async def renew(body: Lease, device=Depends(auth), session=Depends(transaction)):
        account = lease_for(session, device, body)
        account.lease_until = int(time.time()) + LEASE_SECONDS
        device.last_seen = int(time.time())
        return {"ok": True, "expires_at": account.lease_until, "revision": account.revision}

    @app.post("/api/v1/leases/finish")
    async def finish(body: Finish, device=Depends(auth), session=Depends(transaction)):
        account = lease_for(session, device, body)
        task = session.get(Task, account.lease_task)
        task.state, task.updated_at, task.message = body.state, int(time.time()), body.message
        if body.state == "unknown":
            account.quarantine = True
        else:
            account.lease_device, account.lease_hash, account.lease_task, account.lease_until = "", "", "", 0
        return {"ok": True}

    @app.post("/api/v1/accounts/{uid}/acknowledge")
    async def acknowledge(uid: str, device=Depends(auth), session=Depends(transaction)):
        account = account_for(session, device, uid)
        if not account.quarantine:
            raise HTTPException(409, "账号没有待核实任务")
        # Even a stale in-flight HTTP request must have timed out before handoff.
        if int(time.time()) < account.lease_until + 90:
            raise HTTPException(409, "仍在等待旧执行请求结束，请稍后核实")
        task = session.get(Task, account.lease_task)
        if task:
            task.state, task.message, task.updated_at = "reviewed", "用户已核实；原任务不会自动重跑", int(time.time())
        account.quarantine = False
        account.lease_device, account.lease_hash, account.lease_task, account.lease_until = "", "", "", 0
        return {"ok": True}

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/{asset}")
    async def asset(asset: str):
        if asset not in {"app.js", "styles.css", "sw.js", "manifest.webmanifest", "icon.svg", "icon-192.png", "icon-512.png"}:
            raise HTTPException(404)
        media = {"sw.js": "application/javascript", "manifest.webmanifest": "application/manifest+json"}.get(asset)
        return FileResponse(STATIC / asset, media_type=media)

    from cloud.catalog import install
    install(app, store, auth, transaction)
    return app
