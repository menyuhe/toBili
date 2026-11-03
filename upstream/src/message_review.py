"""Durable message evidence gate. Failed/partial reads never permit cleanup."""
import copy
import time
from dataclasses import asdict
from src import accounts
from src.message_api import (GET_SESSIONS_URL, FETCH_SESSION_MSGS_URL, MESSAGE_REFERER,
                             _require_ok, get_unread_summary, list_feed)
from src.message_types import MessageType, normalize_dm_message
from src.task_control import check_cancelled

MAX_PAGES = 500
# Platform message-source values displaying “此条消息为自动回复”.
# Reference: https://github.com/BACNext/bilibili-API-collect-backup/blob/master/docs/message/private_msg.md
AUTO_REPLY_SOURCES = {8, 9, 10, 11}


def review_status(uid):
    state = accounts.read_state(uid, "message_review", {})
    pending = [v for v in state.get("messages", {}).values() if v["resolution"] == "pending"]
    return {"pending": len(pending), "blocked": bool(pending or not state.get("complete") or state.get("cleanup_hold")),
            "cleanup_hold": bool(state.get("cleanup_hold")),
            "checked_at": state.get("checked_at", 0), "error": state.get("error", "" if state.get("complete") else "尚未检查消息"),
            "items": sorted(pending, key=lambda x: x["timestamp"], reverse=True)}


def _request(client, url, params):
    check_cancelled()
    return _require_ok(client.request_json(url, params={"build": 0, "mobi_app": "web", **params},
                                           referer=MESSAGE_REFERER), action="检查领奖消息")


def _entry(entry, talker_name=""):
    return {"id": entry.id, "type": entry.msg_type.value, "talker_id": entry.talker_id,
            "talker_name": talker_name or entry.talker_name, "timestamp": entry.timestamp,
            "text": entry.text or entry.title or "非文字消息，请前往 B 站核查", "resolution": "pending"}


def collect_messages(client, uid, previous):
    """Return a complete new checkpoint or raise. Do not change remote read status."""
    baseline = copy.deepcopy(previous.get("baseline", {}))
    found = {}
    first = not previous.get("initialized_at")
    started = int(time.time())
    unread = get_unread_summary(client)
    # Session list pagination uses session_ts (microseconds), not last message timestamp.
    begin = 0
    visited = set()
    for _ in range(MAX_PAGES):
        data = _request(client, GET_SESSIONS_URL, {"session_type": 1, "group_fold": 0,
                        "unfollow_fold": 0, "sort_rule": 2, "size": 100, **({"begin_ts": begin} if begin else {})})
        sessions = data.get("session_list")
        if not isinstance(sessions, list):
            raise RuntimeError("私信会话列表缺失")
        for session in sessions:
            talker = str(session.get("talker_id") or "")
            if not talker or int(session.get("session_type", 1)) != 1:
                continue
            old_seq = int(baseline.get("dm:" + talker, 0))
            max_seq = old_seq
            end_seq = 0
            unread_left = int(session.get("unread_count") or 0)
            stop = False
            # The session watermark proves this conversation has no newer messages.
            last_seq = int((session.get("last_msg") or {}).get("msg_seqno") or 0)
            if old_seq and last_seq and last_seq <= old_seq and unread_left == 0:
                continue
            for page in range(MAX_PAGES):
                response = _request(client, FETCH_SESSION_MSGS_URL, {"talker_id": talker,
                          "session_type": 1, "size": 100, **({"end_seqno": end_seq} if end_seq else {})})
                messages = response.get("messages")
                if not isinstance(messages, list):
                    raise RuntimeError("私信正文列表缺失")
                seqs = []
                for raw in messages:
                    seq = int(raw.get("msg_seqno") or 0)
                    if not seq or not (raw.get("msg_key") or seq):
                        raise RuntimeError("私信缺少稳定序号，无法完成核查")
                    seqs.append(seq)
                    max_seq = max(max_seq, seq)
                    if seq <= old_seq:
                        stop = True
                        continue
                    incoming = str(raw.get("sender_uid")) != str(uid)
                    include = incoming and (not first or unread_left > 0)
                    if first and incoming:
                        unread_left = max(0, unread_left - 1)
                    if not first and not old_seq and int(raw.get("timestamp") or 0) < previous["initialized_at"]:
                        include, stop = False, True
                    if include and int(raw.get("msg_source") or 0) not in AUTO_REPLY_SOURCES:
                        entry = normalize_dm_message(raw, talker_id=int(talker))
                        # All private-session messages are inspected, including official senders.
                        entry.msg_type = MessageType.DM
                        found["dm:" + talker + ":" + entry.id] = _entry(entry, (session.get("account_info") or {}).get("name", ""))
                if stop or not messages or (first and unread_left == 0) or response.get("has_more") in (0, False):
                    break
                next_seq = min(seqs)
                if end_seq and next_seq >= end_seq:
                    raise RuntimeError("私信分页游标未前进")
                end_seq = next_seq
            else:
                raise RuntimeError("私信分页尚未完成，请稍后重试")
            baseline["dm:" + talker] = max_seq
        if not sessions or data.get("has_more") in (0, False):
            break
        next_begin = min(int(s.get("session_ts") or 0) for s in sessions)
        if not next_begin or next_begin in visited:
            raise RuntimeError("私信会话分页游标缺失或重复")
        visited.add(next_begin)
        begin = next_begin
    else:
        raise RuntimeError("私信会话分页尚未完成")

    cursor_id = cursor_time = 0
    seen_cursors = set()
    prior_ids = set(baseline.get("at_ids", []))
    new_ids = set(prior_ids)
    unread_left = unread["at"]
    reached = False
    for _ in range(MAX_PAGES):
        entries, cursor = list_feed(client, MessageType.AT, cursor_id=cursor_id, cursor_time=cursor_time)
        for entry in entries:
            if not entry.id:
                raise RuntimeError("@消息缺少稳定标识")
            if entry.id in prior_ids:
                reached = True
                break
            new_ids.add(entry.id)
            if (not first and entry.timestamp >= previous["initialized_at"]) or (first and unread_left > 0):
                found["at:" + entry.id] = _entry(entry)
            unread_left = max(0, unread_left - 1)
        if reached or not entries or cursor.get("is_end") in (True, 1) or (first and unread_left == 0):
            break
        next_cursor = (int(cursor.get("id") or 0), int(cursor.get("time") or 0))
        if not all(next_cursor) or next_cursor in seen_cursors:
            raise RuntimeError("@消息分页游标缺失或重复")
        seen_cursors.add(next_cursor)
        cursor_id, cursor_time = next_cursor
    else:
        raise RuntimeError("@消息分页尚未完成")
    baseline["at_ids"] = sorted(new_ids)
    return baseline, found, previous.get("initialized_at") or started


def check_messages(uid, client):
    previous = accounts.read_state(uid, "message_review", {})
    try:
        baseline, found, initialized = collect_messages(client, uid, previous)
        from src.maintenance import activities_for_uid, infer_up
        activities = activities_for_uid(uid)
        for entry in found.values():
            entry["suggested_activity_ids"] = [did for did, item in activities.items()
                if did in entry["text"] or (str(entry.get("talker_id") or "") == infer_up(item))]
        with accounts.lock:
            latest = accounts.read_state(uid, "message_review", {})
            messages = latest.get("messages", {})
            for key, value in found.items():
                messages.setdefault(key, {**value, "key": key})
            accounts.write_state(uid, "message_review", {**latest, "baseline": baseline, "messages": messages,
                                 "initialized_at": initialized, "cleanup_hold": bool(latest.get("cleanup_hold") or any(m["resolution"] == "pending" for m in messages.values())), "complete": True, "error": "", "checked_at": int(time.time())})
    except Exception as exc:
        from src.task_control import TaskCancelled
        if isinstance(exc, TaskCancelled):
            raise
        with accounts.lock:
            latest = accounts.read_state(uid, "message_review", {})
            accounts.write_state(uid, "message_review", {**latest, "complete": False,
                                 "error": str(exc)[:300], "checked_at": int(time.time())})
    return review_status(uid)


def resolve_message(uid, key, resolution, dynamic_id=""):
    if resolution not in {"irrelevant", "won", "pending"}:
        raise ValueError("消息处理方式无效")
    from src import maintenance
    with maintenance._lock, accounts.lock:
        state = accounts.read_state(uid, "message_review", {})
        entry = state.get("messages", {}).get(key)
        if not entry:
            raise ValueError("消息不存在或不属于当前账号")
        if resolution == "won":
            from src import maintenance
            if dynamic_id not in maintenance.activities_for_uid(uid):
                raise ValueError("请选择当前账号已参与的活动；未关联前保留待确认")
            # Message evidence always protects the activity, including conflicting official results.
            from src.db.models import DrawOutcomeRow
            from src.db.session import session_scope
            with session_scope() as session:
                row = session.get(DrawOutcomeRow, (uid, dynamic_id))
                if row is None:
                    row = DrawOutcomeRow(uid=uid, dynamic_id=dynamic_id)
                    session.add(row)
                conflict = row.outcome == "lost" and row.source == "official"
                row.outcome, row.source, row.keep = "won", "message", True
                row.reason = "消息确认中奖；与官方结果冲突，请核查" if conflict else "用户通过消息确认中奖"
                row.checked_at = int(time.time())
        entry.update(resolution=resolution, dynamic_id=dynamic_id, resolved_at=int(time.time()))
        accounts.write_state(uid, "message_review", state)
    return review_status(uid)


def release_cleanup_hold(uid):
    with accounts.lock:
        state = accounts.read_state(uid, "message_review", {})
        if state.get("complete") and not any(m["resolution"] == "pending" for m in state.get("messages", {}).values()):
            accounts.write_state(uid, "message_review", {**state, "cleanup_hold": False})
    return review_status(uid)


def cleanup_block_reason(status):
    """Explain the evidence gate without conflating failed reads and pending messages."""
    if status.get("error"):
        return f"消息核查失败：{status['error']}；已暂停清理，请稍后重新核查消息后再手动清理"
    if status.get("pending"):
        return f"有 {status['pending']} 条消息待确认；已暂停清理，请先到消息核查中处理"
    return "消息核查保护尚未解除；请重新核查消息后手动清理"
