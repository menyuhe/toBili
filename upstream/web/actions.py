from __future__ import annotations

import contextvars
import io
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Callable

from src.app_logging import get_logger
from src.task_control import TaskCancelled, check_cancelled, task_cancellation
from src.bilibili_client import BilibiliClient
from src.bilibili_login import login_with_qrcode
from src.fetch_activity_info import mark_enriched_joined
from src.log_span import PhaseSpanTracker, log_span
from src.lottery_actions import ActionResult
from src.lottery_classifier import PARTICIPATABLE_TYPES
from src.participate_preflight import ensure_activity_participatable
from src.participation import participate_activity
from src.participation_log import participation_succeeded
from src.pipeline.refresh_all_pipeline import PipelineResult, run_new_links_pipeline, run_refresh_all_pipeline
from src.sources import (
    ds2_fanqiao,
    ds3_gongjuren,
    ds4_junming,
    ds6_nuomi,
    ds7_dajinli,
)
from src.sources.common import CheckResult, commit_source_checkpoint, is_valid_dynamic_id
from src.sources.article_source import NEW_ARTICLE_SOURCES
from src.state_store import set_last_pipeline_persisted, set_watch_last_synced_at
from src.status_refresh import refresh_local_activity_statuses
from web.activity_service import (
    SOURCE_LABELS,
    invalidate_activity_cache,
    lookup_lottery_type,
    participate_step_budget,
    resolve_participate_lottery_type,
)
from src.watch_sync import save_watch_result, sync_watch_forwards
from web.user_messages import format_participation_log, sanitize_log

logger = get_logger("job")

REFRESH_ALL_PIPELINE_SUBSTEPS = 3
REFRESH_WATCH_PIPELINE_SUBSTEPS = 3
REFRESH_WATCH_TOTAL = 1 + REFRESH_WATCH_PIPELINE_SUBSTEPS


def _sync_completed_pool_item(dynamic_id: str) -> None:
    from src.db.uids import participation_uid
    from src.participation_pool import reconcile
    uid = participation_uid()
    if uid.isdigit():
        try:
            reconcile(uid, completed_ids={dynamic_id})
        except (OSError, ValueError):
            # Participation was already recorded; the next pool read reconciles it.
            logger.warning("活动已参与，预选池同步待重试：%s", dynamic_id)


def _deserialize_payload_actions(payload: dict[str, Any]) -> list[ActionResult]:
    actions: list[ActionResult] = []
    for item in payload.get("actions") or []:
        if not isinstance(item, dict):
            continue
        action_name = str(item.get("action") or "").strip()
        if not action_name:
            continue
        actions.append(
            ActionResult(
                action=action_name,  # type: ignore[arg-type]
                ok=bool(item.get("ok")),
                detail=str(item.get("detail") or ""),
            )
        )
    return actions


def _payload_joined_success(payload: dict[str, Any]) -> bool:
    if str(payload.get("status") or "") != "joined":
        return False
    lottery_type = str(payload.get("lottery_type") or "")
    actions = _deserialize_payload_actions(payload)
    if not actions:
        return False
    if lottery_type in ("互动抽奖", "转发抽奖", "预约抽奖"):
        return participation_succeeded(actions, lottery_type=lottery_type)
    return all(item.ok for item in actions)


def _require_participate_success(payload: dict[str, Any]) -> None:
    status = str(payload.get("status") or "")
    if status == "skipped":
        raise RuntimeError(str(payload.get("message") or "该活动不可参与"))
    if status != "joined" or not _payload_joined_success(payload):
        raise RuntimeError(str(payload.get("message") or "参与失败"))


def _normalize_participate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    _require_participate_success(payload)
    return dict(payload)


def _participate_dynamic_payload(
    dynamic_id: str,
    on_step: Callable[[int, int, str, str], None],
    *,
    lottery_type_hint: str | None = None,
    client: BilibiliClient | None = None,
) -> dict[str, Any]:
    resolved_type = resolve_participate_lottery_type(dynamic_id, hint=lottery_type_hint)
    if client is not None:
        payload = _execute_participate(
            dynamic_id,
            on_step,
            lottery_type=resolved_type,
            client=client,
        )
    else:
        payload, _detail = _capture_output(
            _execute_participate,
            dynamic_id,
            on_step,
            lottery_type=resolved_type,
        )
    return _normalize_participate_payload(payload)


def _list_filter_params(params: dict[str, Any]) -> dict[str, str | None]:
    return {
        "status": str(params.get("status") or "").strip() or None,
        "lottery_type": str(params.get("lottery_type") or params.get("type") or "").strip() or None,
        "draw": str(params.get("draw") or "").strip() or None,
        "draw_window": str(params.get("draw_window") or "").strip() or None,
        "q": str(params.get("q") or "").strip() or None,
        "sort": str(params.get("sort") or "").strip() or None,
        "order": str(params.get("order") or "").strip() or None,
    }


def _execute_participate(
    dynamic_id: str,
    on_step: Callable[[int, int, str, str], None],
    *,
    lottery_type: str | None = None,
    client: BilibiliClient | None = None,
) -> dict[str, Any]:
    dynamic_id = str(dynamic_id or "").strip()
    if not is_valid_dynamic_id(dynamic_id):
        raise ValueError(f"活动 ID 无效: {dynamic_id}")
    resolved_type = str(lottery_type or "").strip()
    if resolved_type not in PARTICIPATABLE_TYPES:
        resolved_type = resolve_participate_lottery_type(dynamic_id)

    def _run(active_client: BilibiliClient) -> dict[str, Any]:
        result = participate_activity(
            active_client,
            dynamic_id=dynamic_id,
            lottery_type=resolved_type,
            dry_run=False,
            persist=True,
            on_step=on_step,
        )
        payload = result.to_dict()
        payload["lottery_type"] = resolved_type
        return payload

    if client is not None:
        return _run(client)
    with BilibiliClient() as owned_client:
        return _run(owned_client)


def _append_log_detail(log_lines: list[str], detail: str) -> None:
    cleaned = sanitize_log(detail.strip())
    if cleaned:
        log_lines.append(cleaned)


DS_HANDLERS: list[tuple[str, Callable[..., Any], Callable[[Any], Any]]] = [
    ("DS-2", ds2_fanqiao.check_update, ds2_fanqiao.save_result),
    ("DS-3", ds3_gongjuren.check_update, ds3_gongjuren.save_result),
    ("DS-4", ds4_junming.check_update, ds4_junming.save_result),
    ("DS-6", ds6_nuomi.check_update, ds6_nuomi.save_result),
    *((source.source_id, source.check_update, source.save_result) for source in NEW_ARTICLE_SOURCES),
]
BACKUP_DS_HANDLERS = [("DS-7", ds7_dajinli.check_update, ds7_dajinli.save_result)]
DS_HANDLER_BY_ID: dict[str, tuple[Callable[..., Any], Callable[[Any], Any]]] = {
    source_id: (check_update, save_result)
    for source_id, check_update, save_result in [*DS_HANDLERS, *BACKUP_DS_HANDLERS]
}

REFRESH_ALL_TOTAL = len(DS_HANDLERS) + REFRESH_ALL_PIPELINE_SUBSTEPS
REFRESH_SOURCE_TOTAL = 1 + REFRESH_ALL_PIPELINE_SUBSTEPS
ProgressCallback = Callable[..., None]


_SUBPROGRESS_SUFFIX_RE = re.compile(r"(?:\s*\(\s*\d+\s*/\s*\d+\s*\))+\s*$")


def _format_subprogress_message(message: str, done: int, total: int) -> str:
    base = _SUBPROGRESS_SUFFIX_RE.sub("", str(message or "").strip()).strip()
    if total <= 0:
        return base
    return f"{base} ({done}/{total})"


def _pipeline_substep_index(message: str) -> int:
    text = str(message or "")
    if "入库" in text or "落库" in text or "写入活动库" in text:
        return 3
    if "详情进度" in text or "活动详情" in text:
        return 2
    if "分类" in text or "新链接" in text:
        return 1
    return 1


_PIPELINE_PHASE_BY_SUBSTEP = {
    1: ("pipeline_classify", "pipeline_classify"),
    2: ("pipeline_detail", "pipeline_detail"),
    3: ("pipeline_persist", "pipeline_persist"),
}


def _make_refresh_all_pipeline_progress(
    progress: ProgressCallback,
    *,
    ds_count: int,
    span_tracker: PhaseSpanTracker | None = None,
) -> Callable[[int, int, str], None]:
    def on_pipeline_progress(done: int, total: int, message: str) -> None:
        substep = _pipeline_substep_index(message)
        if span_tracker is not None:
            phase_name = _PIPELINE_PHASE_BY_SUBSTEP.get(substep)
            if phase_name:
                phase, span_name = phase_name
                span_tracker.set_phase(phase, name=span_name)
        progress(
            step=ds_count + substep,
            total=ds_count + REFRESH_ALL_PIPELINE_SUBSTEPS,
            message=_format_subprogress_message(message, done, total),
        )

    return on_pipeline_progress


def _make_refresh_watch_pipeline_progress(
    progress: ProgressCallback,
    *,
    span_tracker: PhaseSpanTracker | None = None,
) -> Callable[[int, int, str], None]:
    def on_pipeline_progress(done: int, total: int, message: str) -> None:
        substep = _pipeline_substep_index(message)
        if span_tracker is not None:
            phase_name = _PIPELINE_PHASE_BY_SUBSTEP.get(substep)
            if phase_name:
                phase, span_name = phase_name
                span_tracker.set_phase(phase, name=span_name)
        progress(
            step=1 + substep,
            total=REFRESH_WATCH_TOTAL,
            message=_format_subprogress_message(message, done, total),
        )

    return on_pipeline_progress


def _noop_progress(**_kwargs: Any) -> None:
    return None


def _capture_output(func: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[Any, str]:
    result = func(*args, **kwargs)
    return result, ""


def _pipeline_log_lines(result: PipelineResult) -> list[str]:
    if result.pipeline_skipped:
        return [f"【流水线】{result.message}"]
    skip_detail = ""
    if result.skip_reasons:
        parts = [f"{reason} {count}" for reason, count in result.skip_reasons.items()]
        skip_detail = f"，跳过 {result.skipped_count} 条（{' / '.join(parts)}）"
    return [
        f"【新链接流水线】候选 {result.new_link_count} 条，"
        f"分类通过 {result.classified_count} 条，"
        f"详情 {result.enriched_count} 条，"
        f"入库 {result.persisted_count} 条{skip_detail}，待重试 {len(result.deferred_ids)} 条"
    ] + result.diagnostics


def _run_ds_check(
    index: int,
    source_id: str,
    check_update: Callable[..., Any],
    save_result: Callable[[Any], Any],
) -> tuple[int, dict[str, Any], str, CheckResult]:
    with log_span(
        f"ds_check:{source_id}",
        logger=logger,
        component="ds",
        source_id=source_id,
        phase="ds_check",
    ):
        result, detail = _capture_output(check_update, force=False)
        container_updated = result.updated
        from src.source_content_retry import include_pending
        result = include_pending(result)
        check_cancelled()
        out_path = save_result(result)
    status_text = "发现新专栏，已爬取" if result.updated else "检查完成，暂无更新"
    if result.updated and not container_updated:
        status_text = "专栏未更新，继续处理暂不可读的动态"
    log_line = f"【{SOURCE_LABELS.get(source_id, source_id)}】{status_text}，共 {len(result.activity_links)} 条链接"
    payload = {
        "source_id": source_id,
        "updated": result.updated,
        "link_count": len(result.activity_links),
        "saved": bool(out_path),
        "log_line": log_line,
        "detail": detail.strip(),
        "status_text": status_text,
    }
    return index, payload, log_line, result


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ValueError("任务已取消")


def run_action(
    action: str,
    params: dict[str, Any] | None = None,
    *,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    _raise_if_cancelled(cancel_event)
    with task_cancellation(cancel_event):
        return _run_action(action, params, on_progress=on_progress, cancel_event=cancel_event)


def _run_action(
    action: str,
    params: dict[str, Any] | None = None,
    *,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    params = params or {}
    progress = on_progress or _noop_progress
    log_lines: list[str] = []
    _raise_if_cancelled(cancel_event)

    if action == "ticket_run":
        from src.tickets import run_task
        return run_task(str(params["uid"]), str(params["task_id"]), progress)

    if action.startswith("maintenance_"):
        from src.bilibili_auth import get_login_uid
        from src.cleanup_api import CleanupApi
        from src.maintenance import run_maintenance, confirm_link, activities_for_uid
        uid = str(params.get("uid") or "")
        if not uid or uid != str(get_login_uid()):
            raise RuntimeError("当前账号已改变，停止维护")
        if action == "maintenance_discover_all":
            from src.maintenance import discover_associations
            with BilibiliClient(timeout=15, warmup=False) as client:
                api = CleanupApi(client, uid)
                api.verify_login()
                return discover_associations(api, uid, mode=str(params.get("mode") or "recent"), progress=progress)
        if action in {"maintenance_check", "maintenance_clean", "maintenance_cycle"}:
            if params.get("scheduled_nonstop"):
                from web.schedule_config import load
                if not load(uid).nonstop:
                    return {"ok": True, "message": "挂机模式已关闭", "result": {"skipped": True}}
            if action == "maintenance_cycle":
                from web.account_service import get_account_profile, get_account_extras
                account = get_account_profile()
                if not account.get("logged_in"):
                    raise RuntimeError(account.get("message") or "账号未就绪")
                get_account_extras()
            return run_maintenance(uid, clean=action != "maintenance_check", progress=progress,
                                   only_id=str(params.get("dynamic_id") or ""), manual_clean=action == "maintenance_clean")
        did = str(params.get("dynamic_id") or "")
        if did not in activities_for_uid(uid):
            raise ValueError("活动不属于当前账号的参与记录")
        with BilibiliClient(timeout=15, warmup=False) as client:
            api = CleanupApi(client, uid)
            api.verify_login()
            if action == "maintenance_link":
                confirm_link(api, uid, did, str(params.get("up_uid") or ""),
                             str(params.get("repost_id") or ""), params.get("follow_added") is True)
                return {"ok": True, "message": "关联已核对，满足条件后进入清理", "result": {"dynamic_id": did}}
            if action == "maintenance_discover":
                matches = api.find_reposts(did)
                return {"ok": True, "message": f"找到 {len(matches)} 条可能关联的本人转发，请核对后保存：" + "、".join(matches),
                        "result": {"dynamic_id": did, "repost_ids": matches}}
        raise ValueError("维护操作无效")

    if action == "login":
        qrcode_refresh_count = 0

        def on_qrcode_ready() -> None:
            nonlocal qrcode_refresh_count
            qrcode_refresh_count += 1
            refreshed_at = int(time.time())
            if qrcode_refresh_count == 1:
                progress(
                    step=1,
                    total=1,
                    message="请使用哔哩哔哩 App 扫码登录",
                    log_append="已生成登录二维码",
                    login_phase="waiting",
                    qrcode_refreshed_at=refreshed_at,
                )
            else:
                progress(
                    step=1,
                    total=1,
                    message="二维码已刷新，请重新扫码",
                    log_append="二维码已自动刷新",
                    login_phase="waiting",
                    qrcode_refreshed_at=refreshed_at,
                )

        def on_login_status(phase: str, message: str) -> None:
            log_append = {
                "scanned": "已在手机扫码，等待确认",
                "confirming": "用户已确认，正在写入登录信息",
                "refreshing": "二维码已过期，请重新扫码",
            }.get(phase)
            progress(
                step=1,
                total=1,
                message=message,
                log_append=log_append,
                login_phase=phase,
            )

        progress(step=1, total=1, message="正在生成登录二维码…", login_phase="waiting")
        cookie, log = _capture_output(
            login_with_qrcode,
            open_image=False,
            auto_refresh_on_expire=False,
            cancel_event=cancel_event,
            on_qrcode_ready=on_qrcode_ready,
            on_status_change=on_login_status,
        )
        progress(step=1, total=1, message="登录成功，账号已就绪", login_phase="success")
        return {
            "ok": True,
            "message": "登录成功，账号已就绪",
            "log": sanitize_log(log) or "登录成功",
            "result": {"login_phase": "success", "uid": __import__("re").search(r"DedeUserID=(\d+)", cookie)[1]},
        }

    if action == "refresh_all":
        from src.source_registry import handlers
        configured = handlers([*globals()["DS_HANDLERS"], *BACKUP_DS_HANDLERS])
        DS_HANDLERS = [(r["id"], check, save) for r, check, save in configured if not r["backup"]]
        REFRESH_ALL_TOTAL = len(DS_HANDLERS) + REFRESH_ALL_PIPELINE_SUBSTEPS
        if not DS_HANDLERS:
            return {"ok": True, "message": "没有启用的 UP 合集，可在数据源中添加", "result": {}}
        ds_results: list[dict[str, Any]] = []
        sources_updated = 0
        progress(
            step=0,
            total=REFRESH_ALL_TOTAL,
            message=f"正在顺序检查 {len(DS_HANDLERS)} 个数据源…",
        )
        _raise_if_cancelled(cancel_event)

        ds_payloads: list[tuple[int, dict[str, Any], str, CheckResult]] = []
        for index, (source_id, check_update, save_result) in enumerate(DS_HANDLERS, start=1):
            _raise_if_cancelled(cancel_event)
            progress(step=index - 1, total=REFRESH_ALL_TOTAL,
                     message=f"正在顺序检查 {index}/{len(DS_HANDLERS)} · {SOURCE_LABELS.get(source_id, source_id)}")
            try:
                completed = _run_ds_check(index, source_id, check_update, save_result)
            except TaskCancelled:
                raise
            except Exception as exc:
                raise RuntimeError(f"{SOURCE_LABELS.get(source_id, source_id)} 检查失败：{exc}") from exc
            ds_payloads.append(completed)
            _, payload, log_line, _ = completed
            log_lines.append(log_line)
            progress(step=index, total=REFRESH_ALL_TOTAL,
                     message=f"已检查 {index}/{len(DS_HANDLERS)} · {SOURCE_LABELS.get(source_id, source_id)}：{payload['status_text']}",
                     log_append=log_line)

        ds_payloads.sort(key=lambda item: item[0])
        ds_check_results: list[CheckResult] = []
        for index, payload, log_line, check_result in ds_payloads:
            _raise_if_cancelled(cancel_event)
            ds_check_results.append(check_result)
            if payload.get("updated"):
                sources_updated += 1
            ds_results.append(
                {
                    "source_id": payload["source_id"],
                    "updated": payload["updated"],
                    "link_count": payload["link_count"],
                    "saved": payload["saved"],
                }
            )
        if sources_updated == 0:
            skip_line = f"【跳过流水线】{len(DS_HANDLERS)} 个数据源均为同一专栏，跳过后续步骤"
            log_lines.append(skip_line)
            progress(
                step=REFRESH_ALL_TOTAL,
                total=REFRESH_ALL_TOTAL,
                message="均无新专栏，已跳过整个流水线",
                log_append=skip_line,
            )
            logger.info("一键更新：%s 个数据源均无新专栏，跳过流水线", len(DS_HANDLERS))
            set_last_pipeline_persisted(action="refresh_all", persisted_count=0)
            return {
                "ok": True,
                "message": f"检查完成：{len(DS_HANDLERS)} 个数据源均无新专栏，已跳过整个流水线",
                "result": {
                    "sources": ds_results,
                    "sources_updated": 0,
                    "pipeline_skipped": True,
                    "new_link_count": 0,
                    "persisted_count": 0,
                },
                "log": sanitize_log("\n".join(log_lines).strip()),
            }

        _raise_if_cancelled(cancel_event)
        pipeline_step = len(DS_HANDLERS) + 1
        progress(step=pipeline_step, total=REFRESH_ALL_TOTAL, message="正在分类新链接…")

        pipeline_spans = PhaseSpanTracker(logger=logger, component="pipeline")
        pipeline_error: str | None = None
        try:
            pipeline_result = run_refresh_all_pipeline(
                ds_check_results,
                workers=1,
                on_progress=_make_refresh_all_pipeline_progress(
                    progress,
                    ds_count=len(DS_HANDLERS),
                    span_tracker=pipeline_spans,
                ),
            )
        except Exception as exc:
            pipeline_error = type(exc).__name__
            raise
        finally:
            pipeline_spans.close(error_kind=pipeline_error)
        _raise_if_cancelled(cancel_event)
        for check_result in ds_check_results:
            commit_source_checkpoint(check_result)
        invalidate_activity_cache()
        for line in _pipeline_log_lines(pipeline_result):
            log_lines.append(line)
        progress(
            step=REFRESH_ALL_TOTAL,
            total=REFRESH_ALL_TOTAL,
            message=pipeline_result.message or "流水线完成",
            log_append=log_lines[-1] if log_lines else "",
        )

        summary_parts = [
            f"检查 {len(DS_HANDLERS)} 个数据源，{sources_updated} 个有新专栏",
            f"新链接 {pipeline_result.new_link_count} 条",
            f"新入库 {pipeline_result.persisted_count} 条",
        ]
        if pipeline_result.skip_reasons:
            summary_parts.append(f"跳过 {pipeline_result.skipped_count} 条")

        logger.info(
            "一键更新完成：新链接 %s，入库 %s",
            pipeline_result.new_link_count,
            pipeline_result.persisted_count,
        )
        set_last_pipeline_persisted(
            action="refresh_all",
            persisted_count=pipeline_result.persisted_count,
        )

        return {
            "ok": True,
            "message": "一键更新完成：" + "，".join(summary_parts),
            "result": {
                "sources": ds_results,
                "sources_updated": sources_updated,
                "pipeline": pipeline_result.to_dict(),
            },
            "log": sanitize_log("\n".join(log_lines).strip()),
        }

    if action == "refresh_source":
        source_id = str(params.get("source_id") or "").strip()
        from src.source_registry import handlers
        configured = handlers([*globals()["DS_HANDLERS"], *BACKUP_DS_HANDLERS])
        handler = next(((check, save) for r, check, save in configured if r["id"] == source_id), None)
        if not handler:
            raise ValueError(f"未知数据源：{source_id}")
        check_update, save_result = handler
        source_name = SOURCE_LABELS.get(source_id, source_id)

        progress(
            step=0,
            total=REFRESH_SOURCE_TOTAL,
            message=f"正在检查 {source_name}…",
        )
        _raise_if_cancelled(cancel_event)
        _, payload, log_line, check_result = _run_ds_check(1, source_id, check_update, save_result)
        _raise_if_cancelled(cancel_event)
        log_lines.append(log_line)
        progress(
            step=1,
            total=REFRESH_SOURCE_TOTAL,
            message=f"{source_name}：{payload['status_text']}",
            log_append=log_line,
        )

        if not check_result.updated:
            skip_line = f"【{source_name}】暂无新专栏，跳过后续步骤"
            log_lines.append(skip_line)
            progress(
                step=REFRESH_SOURCE_TOTAL,
                total=REFRESH_SOURCE_TOTAL,
                message=f"{source_name}：无新专栏，已跳过流水线",
                log_append=skip_line,
            )
            logger.info("%s 无新专栏，跳过流水线", source_id)
            set_last_pipeline_persisted(action="refresh_source", persisted_count=0)
            return {
                "ok": True,
                "message": f"{source_name} 检查完成：暂无更新，已刷新检查时间",
                "result": {
                    "source_id": source_id,
                    "source": {
                        "source_id": payload["source_id"],
                        "updated": payload["updated"],
                        "link_count": payload["link_count"],
                        "saved": payload["saved"],
                    },
                    "pipeline_skipped": True,
                    "new_link_count": 0,
                    "persisted_count": 0,
                },
                "log": sanitize_log("\n".join(log_lines).strip()),
            }

        _raise_if_cancelled(cancel_event)
        progress(step=2, total=REFRESH_SOURCE_TOTAL, message="正在分类新链接…")
        pipeline_spans = PhaseSpanTracker(logger=logger, component="pipeline")
        pipeline_error: str | None = None
        try:
            pipeline_result = run_refresh_all_pipeline(
                [check_result],
                on_progress=_make_refresh_all_pipeline_progress(
                    progress,
                    ds_count=1,
                    span_tracker=pipeline_spans,
                ),
            )
        except Exception as exc:
            pipeline_error = type(exc).__name__
            raise
        finally:
            pipeline_spans.close(error_kind=pipeline_error)
        _raise_if_cancelled(cancel_event)
        commit_source_checkpoint(check_result)
        invalidate_activity_cache()
        for line in _pipeline_log_lines(pipeline_result):
            log_lines.append(line)
        progress(
            step=REFRESH_SOURCE_TOTAL,
            total=REFRESH_SOURCE_TOTAL,
            message=pipeline_result.message or "流水线完成",
            log_append=log_lines[-1] if log_lines else "",
        )

        summary_parts = [
            f"{source_name} 有新专栏",
            f"新链接 {pipeline_result.new_link_count} 条",
            f"新入库 {pipeline_result.persisted_count} 条",
        ]
        if pipeline_result.skip_reasons:
            summary_parts.append(f"跳过 {pipeline_result.skipped_count} 条")

        logger.info(
            "%s 更新完成：新链接 %s，入库 %s",
            source_id,
            pipeline_result.new_link_count,
            pipeline_result.persisted_count,
        )
        set_last_pipeline_persisted(
            action="refresh_source",
            persisted_count=pipeline_result.persisted_count,
        )

        return {
            "ok": True,
            "message": f"{source_id} 更新完成：" + "，".join(summary_parts),
            "result": {
                "source_id": source_id,
                "source": {
                    "source_id": payload["source_id"],
                    "updated": payload["updated"],
                    "link_count": payload["link_count"],
                    "saved": payload["saved"],
                },
                "pipeline": pipeline_result.to_dict(),
            },
            "log": sanitize_log("\n".join(log_lines).strip()),
        }

    if action in {"refresh_discovery", "refresh_discovery_history"}:
        from src.lottery_discovery import HISTORY_DAYS, sync_discovery
        progress(step=0, total=1, message="正在直接读取 B站抽奖话题…")
        def discovery_progress(message: str) -> None:
            _raise_if_cancelled(cancel_event)
            progress(step=0, total=1, message=message, log_append=message)
        try:
            result = sync_discovery(on_progress=discovery_progress,
                                    continuous_history=action == "refresh_discovery_history")
        finally:
            # Completed pages are committed even if a later request fails.
            invalidate_activity_cache()
        set_last_pipeline_persisted(action=action, persisted_count=result["persisted_count"])
        message = f"B站直接发现：读取 {result['pages']} 页，新增 {result['persisted_count']} 条活动。"
        if result.get("pending_count"):
            message += f"{result['pending_count']} 条正文暂不可用，已保留待重试。"
        message += (f"近 {HISTORY_DAYS} 天已检查完成，后续检查新动态。" if result["history_complete"]
                    else f"近 {HISTORY_DAYS} 天尚未检查完成，可点击补齐近{HISTORY_DAYS}天。")
        progress(step=1, total=1, message=message, log_append=message)
        return {"ok": True, "message": message, "result": result, "log": message}

    if action == "refresh_watch":
        log_lines: list[str] = []
        progress(step=0, total=REFRESH_WATCH_TOTAL, message="准备扫描监控用户动态…")
        _raise_if_cancelled(cancel_event)

        def on_watch_progress(done: int, total: int, message: str) -> None:
            _raise_if_cancelled(cancel_event)
            progress(step=1, total=REFRESH_WATCH_TOTAL, message=message)

        with log_span("watch_scan", logger=logger, component="pipeline", phase="watch_scan"):
            watch_result = sync_watch_forwards(on_progress=on_watch_progress)
        _raise_if_cancelled(cancel_event)
        watch_line = (
            f"【监控扫描】窗口内 {watch_result.link_count} 条活动链接，"
            f"成功扫描 {watch_result.users_ok}/{watch_result.users_total} 人"
        )
        log_lines.append(watch_line)
        progress(
            step=1,
            total=REFRESH_WATCH_TOTAL,
            message=f"扫描完成：窗口 {watch_result.link_count} 条链接",
            log_append=watch_line,
        )

        pipeline_step = 2
        progress(step=pipeline_step, total=REFRESH_WATCH_TOTAL, message="正在分类新链接…")
        _raise_if_cancelled(cancel_event)

        pipeline_spans = PhaseSpanTracker(logger=logger, component="pipeline")
        pipeline_error: str | None = None
        try:
            pipeline_result = run_new_links_pipeline(
                watch_result.activity_links,
                on_progress=_make_refresh_watch_pipeline_progress(
                    progress,
                    span_tracker=pipeline_spans,
                ),
            )
        except Exception as exc:
            pipeline_error = type(exc).__name__
            raise
        finally:
            pipeline_spans.close(error_kind=pipeline_error)
        _raise_if_cancelled(cancel_event)
        invalidate_activity_cache()
        for line in _pipeline_log_lines(pipeline_result):
            log_lines.append(line)
        progress(
            step=REFRESH_WATCH_TOTAL,
            total=REFRESH_WATCH_TOTAL,
            message=pipeline_result.message or "流水线完成",
            log_append=log_lines[-1] if log_lines else "",
        )

        set_watch_last_synced_at(watch_result.synced_at)
        save_watch_result(watch_result)
        set_last_pipeline_persisted(
            action="refresh_watch",
            persisted_count=pipeline_result.persisted_count,
            synced_at=watch_result.synced_at,
        )

        summary_parts = [
            f"扫描 {watch_result.users_total} 人，提取 {watch_result.link_count} 条链接",
            f"新链接 {pipeline_result.new_link_count} 条",
            f"新入库 {pipeline_result.persisted_count} 条",
        ]
        logger.info(
            "监控用户更新完成：窗口 %s 条，入库 %s",
            watch_result.link_count,
            pipeline_result.persisted_count,
        )
        return {
            "ok": True,
            "message": "监控用户动态更新完成：" + "，".join(summary_parts),
            "result": {
                "watch": watch_result.to_dict(),
                "pipeline": pipeline_result.to_dict(),
            },
            "log": sanitize_log("\n".join(log_lines).strip()),
        }

    if action == "refresh_status":
        progress(step=0, total=1, message="正在刷新活动状态…")
        _raise_if_cancelled(cancel_event)
        with log_span(
            "status_refresh",
            logger=logger,
            component="pipeline",
            phase="status_refresh",
        ):
            result = refresh_local_activity_statuses()
        _raise_if_cancelled(cancel_event)
        invalidate_activity_cache()
        if result.get("skipped"):
            message = "没有需要刷新的进行中活动"
        else:
            message = (
                f"状态刷新完成：检查 {result.get('scanned', 0)} 条，"
                f"更新 {result.get('updated', 0)} 条，"
                f"新结束 {result.get('ended_marked', 0)} 条，"
                f"即将开奖 {result.get('soon_marked', 0)} 条"
            )
        progress(step=1, total=1, message=message, log_append=message)
        logger.info(
            "刷新任务状态完成：检查 %s，更新 %s",
            result.get("scanned", 0),
            result.get("updated", 0),
        )
        return {
            "ok": True,
            "message": message,
            "result": result,
            "log": sanitize_log(message),
        }

    if action == "participate_all":
        from src.nonstop_participation import run
        from web.pool_routes import candidates
        uid = str(params.get("uid") or "")
        return run(uid, candidates=candidates, progress=progress,
                   participate=lambda did, report: _run_action("participate", {"dynamic_id": did},
                       on_progress=report, cancel_event=cancel_event))

    if action == "participate_pool":
        from src.participation_pool import run as run_pool
        from web.pool_routes import account_uid, candidates
        uid = str(params.get("uid") or "")

        if params.get("from_auto"):
            from src.participation_pool import snapshot
            saved = snapshot(uid)
            if saved.get("paused") or not saved["pending"]:
                return {"ok": True, "message": "预选池已暂停或没有待参与活动，已跳过", "result": {"skipped": True}}

        def eligible(did):
            if uid != account_uid():
                raise RuntimeError("当前账号已改变，预选池已暂停")
            row = candidates().get(did)
            if not row:
                return "活动已移出活动库，已跳过"
            if not row.get('can_participate'):
                return f"活动当前为{row.get('activity_status') or '不可参与'}，已跳过"
            return None

        return run_pool(uid, eligible=eligible, progress=progress,
                        participate=lambda did, report: _run_action('participate', {'dynamic_id': did},
                            on_progress=report, cancel_event=cancel_event))

    if action == "participate":
        dynamic_id = str(params.get("dynamic_id") or "").strip()
        if not is_valid_dynamic_id(dynamic_id):
            raise ValueError("活动 ID 无效")
        _raise_if_cancelled(cancel_event)
        try:
            lottery_type = lookup_lottery_type(dynamic_id)
        except RuntimeError as exc:
            logger.warning("参与失败：未找到活动类型 %s — %s", dynamic_id, exc)
            raise ValueError(f"未找到活动 {dynamic_id} 的类型信息，请先一键更新") from exc
        total_steps = participate_step_budget(lottery_type, dynamic_id=dynamic_id)
        progress(step=0, total=total_steps, message="正在检查活动状态…", log_append="正在打开活动链接检查状态…")
        logger.info("开始参与活动 %s (%s)", dynamic_id, lottery_type)

        def on_step(step: int, total: int, message: str, _action_name: str) -> None:
            _raise_if_cancelled(cancel_event)
            progress(step=step, total=total, message=message, log_append=message)

        with BilibiliClient() as client, client.read_snapshot():
            _raise_if_cancelled(cancel_event)
            ensure_activity_participatable(client, dynamic_id, lottery_type_hint=lottery_type)
            _raise_if_cancelled(cancel_event)
            progress(step=0, total=total_steps, message="检查通过，开始参与…", log_append="活动可参与，开始执行参与步骤")
            payload = _participate_dynamic_payload(
                dynamic_id,
                on_step,
                lottery_type_hint=lottery_type,
                client=client,
            )

        mark_enriched_joined(dynamic_id)
        _sync_completed_pool_item(dynamic_id)
        refresh_local_activity_statuses()
        logger.info("参与活动成功 %s", dynamic_id)
        action_log = format_participation_log(payload)
        invalidate_activity_cache()
        return {
            "ok": True,
            "message": str(payload.get("message") or "参与成功"),
            "result": payload,
            "log": action_log,
        }

    raise ValueError(f"未知操作: {action}")
