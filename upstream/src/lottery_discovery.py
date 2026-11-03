"""Discover public lotteries from Bilibili topic feeds, without a third-party database.

Each successful page is checkpointed after classification/persistence. Historical
pagination is resumable; every run also checks the newest page so a long backfill
does not starve new activities. Only the existing three lottery types are stored.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from src.app_paths import data_dir
from src.bilibili_client import BilibiliClient
from src.pipeline.refresh_all_pipeline import run_new_links_pipeline
from src.sources.common import opus_link
from src.task_control import check_cancelled

TOPIC_URL = "https://app.bilibili.com/x/topic/web/details/cards"
TOPICS = ((1040028, "bilibili互动抽奖"), (1140180, "抽奖专用频道"),
          (1091435, "互动抽奖合集"), (60094, "转发抽奖合集"),
          (11592, "抽奖互动"), (1051197, "BiliBili动态抽奖"), (1261901, "抽奖！！！"),
          (1267572, "互动抽奖活动"), (1028370, "互动抽奖好运来"),
          (1013444, "动态抽奖#"), (1055656, "转发动态抽奖！"))
HISTORY_DAYS = 7


def history_cutoff() -> int:
    return int(time.time()) - HISTORY_DAYS * 86400


def publication_time(item: dict) -> int:
    try:
        return int(((item.get("modules") or {}).get("module_author") or {}).get("pub_ts") or 0)
    except (TypeError, ValueError):
        return 0


def _path() -> Path:
    return data_dir() / "lottery_discovery.json"


def _load() -> dict:
    path = _path()
    if not path.exists():
        return {"topics": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("topics"), dict):
        raise RuntimeError("B站发现进度文件格式错误，请保留文件后检查")
    return value


def _save(state: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def discovery_status() -> dict:
    try:
        state = _load()
    except (ValueError, OSError, RuntimeError):
        return {"message": "采集进度读取失败，请检查任务日志"}
    topics = state["topics"]
    active_topics = TOPICS
    policy_current = state.get("history_days") == HISTORY_DAYS
    complete = sum(bool(topics.get(str(tid), {}).get("history_done")) for tid, _ in active_topics) if policy_current else 0
    count = sum(len(topics.get(str(tid), {}).get("seen", [])) for tid, _ in active_topics)
    last_run = state.get("last_run")
    if not last_run:
        from src.job_store import get_latest_job_for_action
        previous = get_latest_job_for_action("refresh_discovery")
        if previous:
            last_run = {**(previous.get("result") or {}), "state": previous["state"],
                        "started_at": previous["started_at"], "finished_at": previous["finished_at"]}
    return {"history_complete": complete == len(active_topics), "checked_count": count,
            "history_days": HISTORY_DAYS, "since": history_cutoff(),
            "pending_count": len(state.get("pending_ids", [])),
            "history_done": complete, "topic_count": len(active_topics),
            "topics": [{"id": tid, "name": name,
                        "checked_count": len(topics.get(str(tid), {}).get("seen", [])),
                        "history_done": policy_current and bool(topics.get(str(tid), {}).get("history_done")),
                        "oldest_pub_ts": topics.get(str(tid), {}).get("oldest_pub_ts")}
                       for tid, name in active_topics],
            "last_run": last_run,
            "message": f"已扫描 {count} 条话题动态；历史完成 {complete}/{len(active_topics)} 个话题。"
                       + ("后续检查新发布内容。" if complete == len(active_topics)
                          else "再次点击会检查最新动态并继续补历史。")}


def _page(client: BilibiliClient, topic_id: int, offset: str) -> tuple[list[dict], str, bool]:
    response = client.get_json(TOPIC_URL, {
        "topic_id": topic_id, "sort_by": 3, "offset": offset,
        "page_size": 20, "source": "H5",
    }, referer=f"https://www.bilibili.com/v/topic/detail?topic_id={topic_id}", retries=0)
    if response.get("code") != 0:
        raise RuntimeError(f"话题接口返回错误：{response.get('code')}")
    page = (response.get("data") or {}).get("topic_card_list")
    if not isinstance(page, dict) or not isinstance(page.get("items"), list):
        raise RuntimeError("话题接口结构变化，保留进度等待重试")
    more = bool(page.get("has_more"))
    next_offset = str(page.get("offset") or "")
    if more and (not next_offset or next_offset == offset or not page["items"]):
        raise RuntimeError("话题分页未前进，保留进度等待重试")
    return page["items"], next_offset, more


def _candidates(rows: list[dict], *, since: int | None = None) -> list[str]:
    """Unwrap reposts to their original activity and sort by original publication."""
    found: dict[str, int] = {}
    for row in rows:
        item = row.get("dynamic_card_item") or {}
        # A feed repost is not itself the lottery. Resolve nested original cards.
        for _ in range(10):
            original = item.get("orig")
            if not isinstance(original, dict):
                break
            item = original
        dynamic_id = str(item.get("id_str") or "")
        if not dynamic_id.isdigit() or item.get("type") == "DYNAMIC_TYPE_NONE":
            continue
        published = publication_time(item)
        if since is not None and (not published or published < since):
            continue
        found[dynamic_id] = published
    return sorted(found, key=lambda did: (found[did], int(did)), reverse=True)


def prepare_history_window(state: dict) -> None:
    """Migrate old cursors and discard retries outside the current window before work."""
    if state.get("history_days") != HISTORY_DAYS:
        # Revisit only the recent window; never resume a legacy years-old cursor.
        for record in state["topics"].values():
            record.update(history_done=False, history_offset="", update_offsets=[], oldest_pub_ts=0)
        state["history_days"] = HISTORY_DAYS
    cutoff = history_cutoff()
    publications = state.get('publication_times', {})
    state['pending_ids'] = [did for did in state.get('pending_ids', [])
                            if int(publications.get(did) or 0) >= cutoff]


def sync_discovery(*, on_progress: Callable[[str], None] | None = None,
                   continuous_history: bool = False) -> dict:
    state = _load()
    prepare_history_window(state)
    run = {"started_at": int(time.time()), "finished_at": None, "state": "running",
           "pages": 0, "persisted_count": 0, "new_link_count": 0, "topics": [],
           "pending_count": len(state.get("pending_ids", []))}
    state["last_run"] = run
    _save(state)
    try:
        result = _sync_discovery(state, on_progress=on_progress, continuous_history=continuous_history)
        run["state"] = "success"
        return result
    except Exception as exc:
        from src.task_control import TaskCancelled
        run["state"] = "cancelled" if isinstance(exc, TaskCancelled) else "error"
        raise
    finally:
        run["finished_at"] = int(time.time())
        _save(state)


def _sync_discovery(state: dict, *, on_progress=None, continuous_history=False) -> dict:
    cutoff = history_cutoff()
    publications = state.setdefault("publication_times", {})
    processed = set(state.get("processed_ids", []))
    pending = dict.fromkeys(did for did in state.get("pending_ids", [])
                            if int(publications.get(did) or 0) >= cutoff)
    state["pending_ids"] = list(pending)
    state["last_run"]["pending_count"] = len(pending)
    # Retry only earlier failures, once per run, after scanning fresh pages.
    retry_ids = list(pending)[:5]
    attempted: set[str] = set()
    result = state["last_run"]
    report = on_progress or (lambda message: None)

    def classify(ids: list[str], label: str) -> None:
        if not ids:
            return
        attempted.update(ids)
        pipeline = run_new_links_pipeline([opus_link(did) for did in ids], workers=2,
            defer_unavailable_content=True,
            on_progress=lambda done, total, message: report(f"「{label}」{message} {done}/{total}"))
        if not pipeline.ok:
            raise RuntimeError(pipeline.message or "抽奖识别失败，保留本页等待重试")
        deferred = set(getattr(pipeline, "deferred_ids", []))
        processed.update(did for did in ids if did not in deferred)
        for did in ids:
            pending.pop(did, None)
            if did in deferred:
                pending[did] = None
        result["persisted_count"] += pipeline.persisted_count
        result["new_link_count"] += pipeline.new_link_count
        result["pending_count"] = len(pending)
        state["processed_ids"] = sorted(processed)
        state["pending_ids"] = list(pending)
        # Persist retry IDs before the page cursor advances, including on cancellation.
        _save(state)

    with BilibiliClient() as client:
        for topic_id, name in TOPICS:
            check_cancelled()
            record = state["topics"].setdefault(str(topic_id), {
                "seen": [], "history_done": False, "history_offset": "", "update_offsets": [],
            })
            seen = set(record["seen"])

            def consume(offset: str) -> tuple[str, bool, bool]:
                check_cancelled()
                report(f"正在读取「{name}」最新动态…" if not offset else f"正在继续读取「{name}」历史动态…")
                rows, next_offset, more = _page(client, topic_id, offset)
                dynamic_rows = [r for r in rows if isinstance(r.get("dynamic_card_item"), dict)]
                if any(not str(r["dynamic_card_item"].get("id_str") or "").isdigit() for r in dynamic_rows):
                    raise RuntimeError("话题动态缺少 ID，保留进度等待重试")
                unseen = [r for r in dynamic_rows if str(r["dynamic_card_item"]["id_str"]) not in seen]
                for row in dynamic_rows:
                    item = row["dynamic_card_item"]
                    for _ in range(10):
                        did = str(item.get("id_str") or "")
                        published = publication_time(item)
                        if did.isdigit() and published:
                            publications[did] = published
                        item = item.get("orig")
                        if not isinstance(item, dict):
                            break
                ids = [did for did in _candidates(unseen, since=cutoff)
                       if did not in processed and did not in pending and did not in attempted]
                classify(ids, name)
                check_cancelled()
                seen.update(str(r["dynamic_card_item"]["id_str"]) for r in dynamic_rows)
                record["seen"] = sorted(seen)
                feed_dates = [publication_time(r["dynamic_card_item"]) for r in dynamic_rows]
                if feed_dates and all(0 < stamp < cutoff for stamp in feed_dates):
                    more = False
                    report(f"「{name}」已到最近 {HISTORY_DAYS} 天的采集边界，停止读取更早历史。")
                dates = [stamp for stamp in feed_dates if stamp >= cutoff]
                dates = [stamp for stamp in dates + [record.get("oldest_pub_ts", 0)] if stamp > 0]
                if dates:
                    record["oldest_pub_ts"] = min(dates)
                result["pages"] += 1
                # Non-dynamic cards are not an overlap boundary.
                return next_offset, more, bool(dynamic_rows) and not unseen

            # Always prioritize new publications, even while resuming old pages.
            offset, more, overlap = consume("")
            if not record["history_done"] and not record["history_offset"]:
                record["history_offset"] = offset if more else ""
                record["history_done"] = not more
            elif more and not overlap and offset not in record["update_offsets"]:
                # Keep every unfinished head scan. Overwriting its cursor when
                # newer posts arrive would silently lose pages between runs.
                record["update_offsets"].append(offset)
            _save(state)

            # At most one update continuation and one historical page per topic.
            # Requests remain bounded while both queues make progress.
            if record["update_offsets"]:
                next_offset, more, overlap = consume(record["update_offsets"][0])
                if not more or overlap:
                    record["update_offsets"].pop(0)
                else:
                    record["update_offsets"][0] = next_offset
                _save(state)
            visited_offsets = set()
            while not record["history_done"] and record["history_offset"]:
                cursor = record["history_offset"]
                if cursor in visited_offsets:
                    raise RuntimeError(f"「{name}」历史分页重复，已保留进度；请稍后重试")
                visited_offsets.add(cursor)
                next_offset, more, _ = consume(record["history_offset"])
                if more and next_offset in visited_offsets:
                    raise RuntimeError(f"「{name}」历史分页未前进，已保留进度；请稍后重试")
                record["history_offset"] = next_offset if more else ""
                record["history_done"] = not more
                _save(state)
                date = time.strftime("%Y-%m-%d", time.localtime(record["oldest_pub_ts"])) if record.get("oldest_pub_ts") else "日期未知"
                report(f"「{name}」已扫描 {len(seen)} 条，最早发布 {date}；" + ("历史已遍历完成" if not more else "进度已保存"))
                if not continuous_history:
                    break
            record["checked_at"] = int(time.time())
            _save(state)
            result["topics"].append({"id": topic_id, "name": name,
                                     "history_done": record["history_done"]})
    retries = [did for did in retry_ids if did not in attempted]
    if retries:
        report(f"正在重试此前正文暂不可用的 {len(retries)} 条动态…")
        classify(retries, "待重试动态")
    if pending:
        report(f"本轮扫描完成，仍有 {len(pending)} 条动态正文暂不可用；已保存，后续每轮最多重试 5 条。")
    result["history_complete"] = all(t["history_done"] for t in result["topics"])
    return result
