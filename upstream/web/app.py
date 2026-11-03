from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, HTTPException
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.app_logging import get_logger, setup_logging
from src.app_paths import __version__, ensure_user_dirs
from src.bilibili_login import QR_IMAGE_PATH
from src.llm_client import test_llm_connection
from src.llm_settings import (
    build_llm_config_from_inputs,
    get_llm_settings_public,
    load_llm_values,
    mark_llm_test_passed,
    save_llm_settings,
)
from src.sources.common import is_valid_dynamic_id, load_previous_output
from src.state_store import get_watch_last_synced_at
from src.user_settings import (
    DEFAULT_PARTICIPATE_FALLBACK_TEXT,
    DEFAULT_PARTICIPATE_TEXT,
    DEFAULT_PARTICIPATE_TEXT_MODE,
    get_participate_fallback_text,
    get_participate_text,
    get_participate_text_mode,
    set_participate_fallback_text,
    set_participate_text,
    set_participate_text_mode,
)
from src.watch_sync import MAX_WINDOW_SECONDS, OUTPUT_PATH as WATCH_OUTPUT_PATH, compute_sync_window
from src.watch_users import add_watch_user, get_watch_users_payload, remove_watch_user, seed_from_candidates_if_empty
from web.account_service import ack_at_unread_notice, clear_login_cookie, get_account_extras, get_account_profile
from web.activity_service import (
    archive_ended_activities,
    ACTIVITY_PAGE_SIZE,
    get_summary,
    list_activities,
)
from web.api_contract import API_CONTRACT_VERSION, ApiContractMiddleware, patch_openapi_schema
from web.api_errors import AppError, ErrorCode, register_exception_handlers, require_llm_ready, require_login
from web.auto_scheduler import auto_scheduler
from web.job_runner import runner
from web.schemas import (
    ALLOWED_JOB_ACTIONS,
    AckAtUnreadRequest,
    DiagnosticsBundleOut,
    DiagnosticsLogsOut,
    JobRequest,
    JobStartOut,
    JobStatusOut,
    LlmSettingsRequest,
    OkResponse,
    ParticipateTextRequest,
    UpdatesCheckOut,
    WatchUserRequest,
)

ensure_user_dirs()
from src.accounts import migrate_legacy
migrate_legacy()
setup_logging(console=False)
logger = get_logger("api")
try:
    from src.config_health import log_config_health

    log_config_health()
except Exception:
    logger.exception("配置自检失败（已忽略，不阻断启动）")
runner.recover_on_startup()

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
DIST_DIR = STATIC_DIR / "dist"

@asynccontextmanager
async def lifespan(_app):
    from web.maintenance_worker import worker
    from src.cloud_client import cloud_client
    worker.start()
    cloud_client.start()
    try:
        yield
    finally:
        cloud_client.stop()
        worker.stop()


app = FastAPI(
    lifespan=lifespan,
    title="toBili 本地控制台 API",
    version=__version__,
    description=f"契约代见 X-Api-Contract / API_CONTRACT_VERSION；当前={API_CONTRACT_VERSION}",
)
class AssetCacheMiddleware:
    """为 hashed /assets/* 加长缓存；纯 ASGI，不缓冲 body。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not str(scope.get("path") or "").startswith("/assets/"):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "public, max-age=31536000, immutable"
            await send(message)

        await self.app(scope, receive, send_wrapper)


# 纯 ASGI 中间件：勿用 BaseHTTPMiddleware，以免缓冲/打断 SSE
app.add_middleware(AssetCacheMiddleware)
app.add_middleware(ApiContractMiddleware)
from web.account_routes import AccountMiddleware, router as account_router
app.add_middleware(AccountMiddleware)
app.include_router(account_router)
register_exception_handlers(app)
from web.maintenance_routes import router as maintenance_router
app.include_router(maintenance_router)
from web.source_routes import router as source_router
app.include_router(source_router)
from web.ticket_routes import router as ticket_router
app.include_router(ticket_router)
from web.pool_routes import router as pool_router
app.include_router(pool_router)
from web.cloud_routes import router as cloud_router
app.include_router(cloud_router)
from web.article_routes import router as article_router
app.include_router(article_router)

_JOB_REQUIRES_LOGIN = frozenset(
    {
        "participate",
        "participate_pool",
        "refresh_all",
        "refresh_source",
        "refresh_status",
        "refresh_watch",
        "refresh_discovery", "refresh_discovery_history",
    }
)
_JOB_REQUIRES_LLM = frozenset(
    {
        "participate",
        "participate_pool",
        "refresh_all",
        "refresh_source",
        "refresh_watch",
        "refresh_discovery", "refresh_discovery_history",
    }
)


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    app.openapi_schema = patch_openapi_schema(schema)
    return app.openapi_schema


app.openapi = custom_openapi  # type: ignore[method-assign]


@app.get("/api/watch-users", tags=["stable"])
def api_watch_users() -> dict[str, Any]:
    seed_from_candidates_if_empty()
    payload = get_watch_users_payload(ensure_seeded=False)
    now = int(time.time())
    last_synced_at = get_watch_last_synced_at()
    window_start, window_end = compute_sync_window(now=now, last_synced_at=last_synced_at)
    payload["last_synced_at"] = last_synced_at
    payload["max_window_seconds"] = MAX_WINDOW_SECONDS
    payload["next_window"] = {"start": window_start, "end": window_end}
    watch_data = load_previous_output(WATCH_OUTPUT_PATH) or {}
    payload["last_scan_link_count"] = int(watch_data.get("link_count") or 0)
    return payload


@app.post("/api/watch-users", tags=["stable"])
def api_add_watch_user(request: WatchUserRequest) -> dict[str, Any]:
    account = get_account_profile()
    require_login(account, message="请先扫码登录后再管理监控用户")
    try:
        user = add_watch_user(mid=request.mid)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    except OSError as exc:
        raise AppError(ErrorCode.INTERNAL, f"保存监控用户失败：{exc}") from exc
    name_fallback = str(request.mid) == user.name
    return {"ok": True, "user": user.to_dict(), "name_fallback": name_fallback}


@app.delete("/api/watch-users/{mid}", tags=["stable"])
def api_remove_watch_user(mid: int) -> OkResponse:
    account = get_account_profile()
    require_login(account, message="请先扫码登录后再管理监控用户")
    if mid <= 0:
        raise AppError(ErrorCode.VALIDATION_ERROR, "MID 无效")
    try:
        removed = remove_watch_user(mid=mid)
    except OSError as exc:
        raise AppError(ErrorCode.INTERNAL, f"删除监控用户失败：{exc}") from exc
    if not removed:
        raise AppError(ErrorCode.NOT_FOUND, "用户不在监控列表中")
    return OkResponse(ok=True)


@app.get("/api/account", tags=["stable"])
def api_account() -> dict[str, Any]:
    return get_account_profile()


@app.get("/api/account/extras", tags=["stable"])
def api_account_extras() -> dict[str, Any]:
    try:
        return get_account_extras()
    except RuntimeError as exc:
        raise AppError(ErrorCode.AUTH_REQUIRED, str(exc)) from exc


@app.post("/api/account/ack-at-unread", tags=["stable"])
def api_ack_at_unread(request: AckAtUnreadRequest) -> dict[str, Any]:
    try:
        return ack_at_unread_notice(request.current)
    except RuntimeError as exc:
        raise AppError(ErrorCode.AUTH_REQUIRED, str(exc)) from exc
    except OSError as exc:
        raise AppError(ErrorCode.INTERNAL, f"保存提醒状态失败：{exc}") from exc


@app.get("/api/summary", tags=["stable"])
def api_summary() -> dict[str, Any]:
    payload = get_summary()
    payload["job"] = runner.get_status().to_dict()
    return payload


@app.get("/api/activities", tags=["stable"])
def api_activities(
    status: str | None = Query(default=None),
    type: str | None = Query(default=None, alias="type"),
    draw: str | None = Query(default=None),
    draw_window: str | None = Query(default=None),
    q: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    order: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=ACTIVITY_PAGE_SIZE, ge=1, le=ACTIVITY_PAGE_SIZE),
    archived: bool = Query(default=False),
) -> dict[str, Any]:
    return list_activities(
        status=status,
        lottery_type=type,
        draw=draw,
        draw_window=draw_window,
        q=q,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
        archived=archived,
    )


@app.post("/api/activities/archive-ended", tags=["stable"])
def api_archive_ended() -> dict[str, Any]:
    return {"ok": True, "archived_count": archive_ended_activities()}


@app.post("/api/activities/{dynamic_id}/restore", tags=["stable"])
def api_restore_archived(dynamic_id: str) -> dict[str, Any]:
    from src.activity_archive import restore_archived_activity
    if not is_valid_dynamic_id(dynamic_id):
        raise AppError(ErrorCode.VALIDATION_ERROR, "活动 ID 无效")
    return {"ok": True, "restored": restore_archived_activity(dynamic_id)}


@app.post("/api/activities/{dynamic_id}/archive", tags=["stable"])
def api_archive_activity(dynamic_id: str) -> dict[str, Any]:
    from src.activity_archive import archive_activity_ids
    from src.activity_store import activity_exists
    if not is_valid_dynamic_id(dynamic_id) or not activity_exists(dynamic_id):
        raise AppError(ErrorCode.VALIDATION_ERROR, "活动不存在或 ID 无效")
    if runner.get_status().state == "running":
        raise HTTPException(status_code=409, detail="请先暂停或等待当前任务结束，再移出活动")
    return {"ok": True, "archived_count": archive_activity_ids({dynamic_id})}


@app.post("/api/jobs", response_model=JobStartOut, tags=["stable"])
def api_start_job(request: JobRequest) -> dict[str, Any]:
    if request.action not in ALLOWED_JOB_ACTIONS:
        raise AppError(ErrorCode.UNSUPPORTED_ACTION, "暂不支持该操作")
    account = get_account_profile()
    if request.action in _JOB_REQUIRES_LOGIN:
        require_login(account, message="请先扫码登录后再执行此操作")
    if request.action in _JOB_REQUIRES_LLM:
        require_llm_ready()
    params = request.params or {}
    if request.action == "participate_pool":
        from web.pool_routes import account_uid
        from src.participation_pool import snapshot
        uid = account_uid()
        if not snapshot(uid)['pending']:
            raise AppError(ErrorCode.VALIDATION_ERROR, "请先加入待参与活动，或将失败项重新排队")
        params = {"uid": uid}
    if request.action == "refresh_source":
        from src.source_registry import catalog

        source_id = str(params.get("source_id") or "").strip()
        if source_id not in {row["id"] for row in catalog()}:
            raise AppError(ErrorCode.VALIDATION_ERROR, "数据源 ID 无效")
    if request.action == "participate":
        dynamic_id = str(params.get("dynamic_id") or "").strip()
        if not is_valid_dynamic_id(dynamic_id):
            raise AppError(ErrorCode.VALIDATION_ERROR, "活动 ID 无效")
    from src.cloud_client import cloud_client, load_connection
    if request.action != "login" and load_connection() and cloud_client.blocked_reason():
        raise AppError(ErrorCode.VALIDATION_ERROR, cloud_client.blocked_reason())
    if runner.try_start(request.action, params, source="ui") is None:
        raise AppError(ErrorCode.JOB_BUSY, "已有任务正在运行")
    return {"ok": True, "job": runner.get_status().to_dict()}


@app.post("/api/jobs/cancel", response_model=JobStartOut, tags=["stable"])
def api_cancel_job(job_id: int | None = None) -> dict[str, Any]:
    if not runner.cancel(job_id):
        raise AppError(ErrorCode.JOB_NOT_CANCELLABLE, "当前没有可取消的任务")
    return {"ok": True, "job": runner.get_status().to_dict()}


@app.get("/api/jobs/current", response_model=JobStatusOut, tags=["stable"])
def api_current_job() -> dict[str, Any]:
    return runner.get_status().to_dict()


@app.get("/api/runtime", tags=["stable"])
def api_runtime() -> dict[str, Any]:
    from src.config_health import run_config_health_checks

    report = run_config_health_checks()
    payload = report.to_dict()
    payload["ok"] = True
    return payload


@app.get("/api/jobs/history", tags=["stable"])
def api_job_history(limit: int = Query(default=50, ge=1, le=50),
                    before_id: int | None = Query(default=None, ge=1)) -> dict:
    from src.job_store import list_recent_jobs
    rows = list_recent_jobs(limit, before_id=before_id)
    for row in rows:
        row["uid"] = (row.get("params") or {}).get("uid", "")
        row["scope"] = (row.get("params") or {}).get("scope", "account")
    fields = ("uid", "scope", "id", "label", "state", "action", "started_at", "finished_at", "message", "source")
    return {"items": [{key: row.get(key) for key in fields} for row in rows],
            "next_before_id": rows[-1]["id"] if len(rows) == limit else None}


@app.get("/api/jobs/history/{job_id}", tags=["stable"])
def api_job_history_detail(job_id: int) -> dict:
    from src.job_store import get_job
    from web.job_runner import _status_from_row
    row = get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="任务记录不存在或已超过保留时间")
    from src.account_context import scoped_uid
    params = row.get("params") or {}
    if params.get("uid") != scoped_uid() and params.get("scope") != "global":
        raise HTTPException(404, "任务不属于当前账号")
    return _status_from_row(row).to_dict()


@app.post(
    "/api/updates/check",
    response_model=UpdatesCheckOut,
    tags=["stable"],
)
def api_updates_check() -> dict[str, Any]:
    """手动检查 GitHub Releases；网络失败也返回 200 + ok=false。"""
    from src.update_check import check_for_updates

    return check_for_updates().to_dict()


@app.get(
    "/api/diagnostics/logs",
    response_model=DiagnosticsLogsOut,
    tags=["internal"],
    include_in_schema=False,
)
def api_diagnostics_logs(
    job_id: int | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    from src.log_query import query_log_lines

    try:
        payload = query_log_lines(job_id=job_id, limit=limit)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    return {
        "ok": True,
        "files": payload.get("files") or [],
        "count": int(payload.get("count") or 0),
        "lines": payload.get("lines") or [],
    }


@app.get(
    "/api/diagnostics/bundle",
    response_model=DiagnosticsBundleOut,
    tags=["internal"],
    include_in_schema=False,
)
def api_diagnostics_bundle(job_id: int | None = Query(default=None)) -> dict[str, Any]:
    from src.diagnostics import build_diagnostics_bundle

    bundle = build_diagnostics_bundle(
        job_id=job_id,
        current_job=runner.get_status().to_dict(),
        auto_status=auto_scheduler.get_status(),
    )
    return {"ok": True, "filename": bundle["filename"], "text": bundle["text"]}


@app.get("/api/events", tags=["streaming"])
def api_events():
    """SSE：job.* + auto.* 进程级事件流（协议见方向二）。"""
    from web.sse import sse_response

    return sse_response(
        job_snapshot=runner.get_status().to_dict(),
        auto_snapshot=auto_scheduler.get_status(),
    )


@app.get("/api/auto/status", tags=["stable"])
def api_auto_status() -> dict[str, Any]:
    return auto_scheduler.get_status()


from web.schedule_config import ScheduleConfig

@app.put("/api/auto/config", tags=["stable"])
def api_auto_config(body: ScheduleConfig):
    from web.schedule_config import save
    try:
        return save(body)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc


@app.post("/api/auto/nonstop/resume", tags=["stable"])
def api_nonstop_resume():
    from src.account_context import scoped_uid
    from src.nonstop_participation import resume
    uid = scoped_uid()
    if not uid or not uid.isdigit():
        raise AppError(ErrorCode.VALIDATION_ERROR, "请先选择账号")
    resume(uid)
    return {"ok": True}


@app.post("/api/auto/start", tags=["stable"])
def api_auto_start() -> dict[str, Any]:
    try:
        return auto_scheduler.start()
    except RuntimeError as exc:
        text = str(exc)
        if "已在运行" in text:
            raise AppError(ErrorCode.AUTO_ALREADY_RUNNING, text) from exc
        raise AppError(ErrorCode.INTERNAL, text) from exc


@app.post("/api/auto/stop", tags=["stable"])
def api_auto_stop() -> dict[str, Any]:
    """只停止定时点击调度器，不会取消抽奖端正在运行的任务。"""
    return auto_scheduler.stop(reason="用户在监视面板停止")


def _build_settings_payload() -> dict[str, Any]:
    account = get_account_profile()
    llm = get_llm_settings_public()
    logged_in = bool(account.get("logged_in"))
    return {
        "participate_text": get_participate_text(),
        "default_participate_text": DEFAULT_PARTICIPATE_TEXT,
        "participate_fallback_text": get_participate_fallback_text(),
        "default_participate_fallback_text": DEFAULT_PARTICIPATE_FALLBACK_TEXT,
        "participate_text_mode": get_participate_text_mode(),
        "default_participate_text_mode": DEFAULT_PARTICIPATE_TEXT_MODE,
        "llm": llm,
        "setup_complete": logged_in and bool(llm.get("ready")),
    }


@app.get("/api/settings", tags=["stable"])
def api_settings() -> dict[str, Any]:
    return _build_settings_payload()


@app.get("/api/settings/llm", tags=["stable"])
def api_get_llm_settings() -> dict[str, Any]:
    account = get_account_profile()
    llm = get_llm_settings_public()
    logged_in = bool(account.get("logged_in"))
    return {
        "llm": llm,
        "setup_complete": logged_in and bool(llm.get("ready")),
    }


@app.post("/api/settings/llm/test", tags=["stable"])
def api_test_llm_settings(request: LlmSettingsRequest) -> dict[str, Any]:
    account = get_account_profile()
    require_login(account, message="请先扫码登录后再测试 LLM")
    try:
        saved = load_llm_values()
        test_key = request.api_key.strip() or saved.get("LLM_API_KEY", "").strip()
        test_base = (request.base_url or "").strip().rstrip("/")
        test_model = request.model_name.strip() or saved.get("LLM_MODEL_NAME", "").strip()
        config = build_llm_config_from_inputs(
            api_key=test_key,
            base_url=test_base,
            model_name=test_model,
        )
        endpoint = test_llm_connection(config)
        llm = mark_llm_test_passed(
            api_key=test_key,
            base_url=test_base,
            model_name=test_model,
        )
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    except RuntimeError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    logged_in = bool(account.get("logged_in"))
    return {
        "ok": True,
        "message": f"连接成功：{endpoint}",
        "llm": llm,
        "setup_complete": logged_in and bool(llm.get("ready")),
    }


@app.post("/api/settings/llm", tags=["stable"])
@app.put("/api/settings/llm", tags=["stable"], deprecated=True)
def api_update_llm_settings(request: LlmSettingsRequest) -> dict[str, Any]:
    account = get_account_profile()
    require_login(account, message="请先扫码登录后再配置 LLM")
    try:
        llm = save_llm_settings(
            api_key=request.api_key.strip() or None,
            base_url=request.base_url,
            model_name=request.model_name,
        )
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
    except OSError as exc:
        raise AppError(ErrorCode.INTERNAL, f"保存 LLM 配置失败：{exc}") from exc
    logged_in = bool(account.get("logged_in"))
    return {
        "llm": llm,
        "setup_complete": logged_in and bool(llm.get("ready")),
    }


@app.put("/api/settings/participate-text", tags=["stable"])
@app.post("/api/settings/participate-text", tags=["stable"], deprecated=True)
def api_update_participate_text(request: ParticipateTextRequest) -> dict[str, Any]:
    account = get_account_profile()
    require_login(account, message="请先扫码登录后再修改参与文案")
    payload: dict[str, Any] = {}
    try:
        if request.participate_text_mode is not None:
            payload["participate_text_mode"] = set_participate_text_mode(request.participate_text_mode)
        if request.participate_text is not None:
            payload["participate_text"] = set_participate_text(request.participate_text)
        if request.participate_fallback_text is not None:
            payload["participate_fallback_text"] = set_participate_fallback_text(
                request.participate_fallback_text
            )
    except OSError as exc:
        raise AppError(ErrorCode.INTERNAL, f"保存参与文案失败：{exc}") from exc
    if not payload:
        raise AppError(ErrorCode.VALIDATION_ERROR, "未提供可保存的设置")
    return payload


@app.put("/api/settings/participate-text-mode", tags=["stable"], deprecated=True)
@app.post("/api/settings/participate-text-mode", tags=["stable"], deprecated=True)
def api_update_participate_text_mode(request: ParticipateTextRequest) -> dict[str, str]:
    account = get_account_profile()
    require_login(account, message="请先扫码登录后再修改参与文案模式")
    if request.participate_text_mode is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "缺少 participate_text_mode")
    try:
        value = set_participate_text_mode(request.participate_text_mode)
    except OSError as exc:
        raise AppError(ErrorCode.INTERNAL, f"保存参与文案模式失败：{exc}") from exc
    return {"participate_text_mode": value}


@app.post("/api/logout", response_model=OkResponse, tags=["stable"])
def api_logout() -> dict[str, Any]:
    if runner.is_running():
        raise AppError(ErrorCode.JOB_BUSY, "请先等待或取消当前任务，再退出账号")
    try:
        clear_login_cookie()
    except OSError as exc:
        raise AppError(ErrorCode.INTERNAL, f"退出登录失败：{exc}") from exc
    return {"ok": True}


@app.get("/api/login/qrcode", tags=["stable"])
def api_login_qrcode() -> FileResponse:
    from src.bilibili_login import qrcode_path
    from src.job_store import get_job
    from src.account_context import account_scope, scoped_uid
    current = runner.get_status()
    job = get_job(current.id) if current.id else None
    login_session = ((job or {}).get("params") or {}).get("login_session", "")
    with account_scope(scoped_uid(), login_session):
        QR_IMAGE_PATH = qrcode_path()
    if not QR_IMAGE_PATH.exists():
        raise AppError(ErrorCode.NOT_FOUND, "二维码尚未生成")
    return FileResponse(
        QR_IMAGE_PATH,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@app.get("/app.js", tags=["internal"], include_in_schema=False)
@app.get("/styles.css", tags=["internal"], include_in_schema=False)
def api_legacy_static_gone() -> None:
    raise AppError(
        ErrorCode.NOT_FOUND,
        "旧静态入口已移除，请使用构建产物 web/static/dist（先 npm run build）",
        status_code=410,
    )


@app.get("/favicon.svg", tags=["internal"], include_in_schema=False)
def api_favicon() -> FileResponse:
    favicon = DIST_DIR / "favicon.svg"
    if not favicon.exists():
        favicon = STATIC_DIR / "favicon.svg"
    if not favicon.exists():
        raise AppError(ErrorCode.NOT_FOUND, "favicon 不存在")
    return FileResponse(favicon, media_type="image/svg+xml")


if not (DIST_DIR / "index.html").exists():
    logger.error(
        "未找到 web/static/dist。开发请另开: cd web/frontend && npm run dev；"
        "生产请先: cd web/frontend && npm ci && npm run build"
    )


@app.get("/", tags=["internal"], include_in_schema=False)
def spa_index() -> FileResponse:
    index_path = DIST_DIR / "index.html"
    if not index_path.exists():
        raise AppError(
            ErrorCode.INTERNAL,
            "前端未构建：请先执行 cd web/frontend && npm ci && npm run build",
        )
    return FileResponse(
        index_path,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


# E2E 钩子仅在 BINGGO_E2E=1 时安装（生产 run_dashboard 不得设置该变量）
from web.e2e_hooks import e2e_enabled, install_e2e_hooks

if e2e_enabled():
    install_e2e_hooks(app)
    logger.warning("BINGGO_E2E=1：已安装测试钩子（仅 127.0.0.1 /api/testing/e2e-state）")

_assets_dir = DIST_DIR / "assets"
if _assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")
else:
    logger.error("未找到 web/static/dist/assets，静态资源将无法加载")
