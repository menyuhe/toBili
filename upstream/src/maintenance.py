"""Draw verification and conservative cleanup, isolated from display-only expiry."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

from sqlmodel import select

from src.db.models import (ActivityRow, ParticipationRow, ParticipationLinkRow, DrawOutcomeRow,
    CleanupStepRow, MaintenanceSettingsRow, ProtectedUpRow, MaintenanceAuditRow)
from src.db.session import session_scope
from src.cleanup_api import CleanupApi, MaintenanceBlocked, own_repost
from src.notifications import safe_enqueue
from src.task_control import check_cancelled, TaskCancelled

GRACE_SECONDS = 24 * 3600
MESSAGE_RECHECK_SECONDS = 300
CLEANUP_BATCH_LIMIT = 20
_lock = threading.RLock()
_tracking: ContextVar[bool] = ContextVar("record_cleanup_links", default=False)


@contextmanager
def track_participation(enabled: bool):
    token = _tracking.set(enabled)
    try:
        yield
    finally:
        _tracking.reset(token)


def audit(session, uid, dynamic_id, action, target_id, state, message):
    session.add(MaintenanceAuditRow(uid=uid, dynamic_id=dynamic_id, action=action,
        target_id=target_id, state=state, message=message[:500], recorded_at=int(time.time())))


def begin_link(client, dynamic_id: str, up_uid: int):
    if not _tracking.get():
        return None
    from src.bilibili_auth import require_login
    _, current = require_login()
    uid = str(current)
    # Unlike the legacy participation probe, failure MUST NOT mean 'not followed'.
    try:
        followed = CleanupApi(client, uid).following(str(up_uid))
    except TaskCancelled:
        raise
    except Exception:
        followed = None
    with _lock, session_scope() as session:
        row = session.get(ParticipationLinkRow, (uid, dynamic_id))
        if row is None:
            row = ParticipationLinkRow(uid=uid, dynamic_id=dynamic_id, up_uid=str(up_uid),
                followed_before=followed, origin="new", created_at=int(time.time()))
            session.add(row)
        # Never replace an older baseline with the already-followed state from a retry.
        return uid


def record_follow(uid, dynamic_id, result):
    if not uid or not result.ok:
        return
    with _lock, session_scope() as session:
        row = session.get(ParticipationLinkRow, (uid, dynamic_id))
        if row and row.followed_before is False and (result.metadata or {}).get("created") is True:
            row.follow_added = True
            audit(session, uid, dynamic_id, "follow_recorded", row.up_uid, "success", "记录本软件新增关注")


def record_repost(client, uid, dynamic_id, result):
    if not uid or not result.ok:
        return
    repost_id = str((result.metadata or {}).get("repost_id") or "")
    # Persist the known ID before doing any optional lookup.
    with _lock, session_scope() as session:
        row = session.get(ParticipationLinkRow, (uid, dynamic_id))
        if row:
            if repost_id.isdigit() and repost_id != dynamic_id:
                row.repost_id, row.repost_recorded = repost_id, True
            audit(session, uid, dynamic_id, "repost_recorded", repost_id, "success" if repost_id else "unknown",
                  "记录转发 ID" if repost_id else "转发返回未包含 ID，等待关联核对")
    if not repost_id:
        try:
            matches = CleanupApi(client, uid).find_reposts(dynamic_id)
            if len(matches) == 1:
                with _lock, session_scope() as session:
                    row = session.get(ParticipationLinkRow, (uid, dynamic_id))
                    if row and not row.repost_id:
                        # A feed match alone cannot prove the program created it; require review.
                        row.repost_id = matches[0]
        except TaskCancelled:
            raise
        except Exception:
            pass


def settings(uid: str) -> dict:
    with session_scope() as session:
        row = session.get(MaintenanceSettingsRow, uid)
        result = row.model_dump() if row else MaintenanceSettingsRow(uid=uid).model_dump()
        result["protected_ups"] = [r.up_uid for r in session.exec(select(ProtectedUpRow).where(ProtectedUpRow.uid == uid)).all()]
        return {**result, "grace_hours": 24, "check_interval_minutes": 30, "cleanup_batch_limit": CLEANUP_BATCH_LIMIT}


def save_settings(uid: str, values: dict):
    with _lock, session_scope() as session:
        row = session.get(MaintenanceSettingsRow, uid)
        if row is None:
            row = MaintenanceSettingsRow(uid=uid)
            session.add(row)
        for key in ("enabled", "delete_reposts", "unfollow"):
            if key in values:
                setattr(row, key, bool(values[key]))
        if "protected_ups" in values:
            for entry in session.exec(select(ProtectedUpRow).where(ProtectedUpRow.uid == uid)).all():
                session.delete(entry)
            for up in sorted(set(values["protected_ups"])):
                if not up.isdigit() or int(up) <= 0:
                    raise ValueError("保留名单应填写 UP 主数字 UID")
                session.add(ProtectedUpRow(uid=uid, up_uid=up))
    return settings(uid)


def activities_for_uid(uid: str) -> dict[str, dict]:
    with session_scope() as session:
        joined = session.exec(select(ParticipationRow).where(ParticipationRow.uid == uid,
            ParticipationRow.user_status == "已参加")).all()
        links = session.exec(select(ParticipationLinkRow).where(ParticipationLinkRow.uid == uid)).all()
        ids = {r.dynamic_id for r in joined} | {r.dynamic_id for r in links}
        result = {}
        for did in ids:
            row = session.get(ActivityRow, did)
            item = json.loads(row.payload_json) if row else {}
            if row:
                item.update({k: getattr(row, k) for k in ("lottery_type", "business_id", "business_type", "lottery_time") if getattr(row, k) is not None})
            result[did] = {**item, "dynamic_id": did}
        return result


def infer_up(item: dict) -> str:
    raw = item.get("sender_uid") or item.get("up_uid") or ((item.get("conditions") or {}).get("sender_uid"))
    return str(raw) if str(raw or "").isdigit() else ""


def sync_candidates(uid: str, items: dict[str, dict]):
    # Older records stay historical even when their activity has already expired.
    from src.participation_log import load_action_entries_for_uid
    snapshots = {r["dynamic_id"]: r.get("context_snapshot") or {} for r in load_action_entries_for_uid(uid)}
    with _lock, session_scope() as session:
        for did, item in items.items():
            if not session.get(DrawOutcomeRow, (uid, did)):
                session.add(DrawOutcomeRow(uid=uid, dynamic_id=did))
            if not session.get(ParticipationLinkRow, (uid, did)):
                up = infer_up(item) or str(snapshots.get(did, {}).get("sender_uid") or "")
                session.add(ParticipationLinkRow(uid=uid, dynamic_id=did, up_uid=up if up.isdigit() else ""))


def evaluate_notice(notice: dict | None, uid: str, *, draw_time: int = 0, now: int | None = None):
    now = int(time.time()) if now is None else now
    if notice is None:
        return ("pending", "尚未到开奖时间") if draw_time > now else ("unknown", "没有可核验的官方结果")
    result = notice.get("lottery_result")
    result = result if isinstance(result, dict) else {}
    for entries in result.values():
        if isinstance(entries, list) and any(isinstance(w, dict) and str(w.get("uid")) == uid for w in entries):
            return "won", "官方中奖名单包含当前账号"
    if notice.get("status") == 0 and int(notice.get("lottery_time") or draw_time or 0) > now:
        return "pending", "官方尚未开奖"
    if notice.get("status") != 2:
        return "unknown", "官方尚未发布完整开奖结果"
    total = 0
    expected_keys = {f"{tier}_prize_result" for tier in ("first", "second", "third")}
    if set(result) - expected_keys:
        return "unknown", "存在未识别的奖项，保留待确认"
    all_uids = []
    for tier in ("first", "second", "third"):
        count = notice.get(f"{tier}_prize")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return "unknown", "奖项人数不完整，保留待确认"
        entries = result.get(f"{tier}_prize_result", [])
        if not isinstance(entries, list) or len(entries) != count:
            return "unknown", "中奖名单人数与奖项不一致"
        ids = [str(w.get("uid") or "") if isinstance(w, dict) else "" for w in entries]
        if any(not v.isdigit() or int(v) <= 0 for v in ids) or len(set(ids)) != count:
            return "unknown", "中奖名单有缺失或重复 UID"
        total += count
        all_uids.extend(ids)
    if total <= 0 or len(set(all_uids)) != total:
        return "unknown", "中奖名单为空或无法确认完整性"
    return "lost", "官方完整中奖名单不包含当前账号"


def get_outcome(uid, did):
    with session_scope() as session:
        row = session.get(DrawOutcomeRow, (uid, did))
        return row.model_dump() if row else DrawOutcomeRow(uid=uid, dynamic_id=did).model_dump()


def check_outcome(api, uid: str, did: str, item: dict) -> dict:
    now = int(time.time())
    old = get_outcome(uid, did)
    try:
        notice = api.notice(item)
        outcome, reason = evaluate_notice(notice, uid, draw_time=int(item.get("lottery_time") or 0), now=now)
        source = "official" if notice else "none"
        # A human review can resolve unstructured draws, never override conflicting official evidence.
        if old["source"] == "manual" and notice is None:
            outcome, reason, source = old["outcome"], "人工确认；当前无官方结果", "manual"
    except (TaskCancelled, MaintenanceBlocked):
        raise
    except Exception:
        outcome, reason, source, notice = "unknown", "本次结果查询失败，保留待确认", "none", None
    # Never erase a previously confirmed win due to a later incomplete response.
    if old["outcome"] == "won":
        conflict = old["source"] == "message" and outcome == "lost"
        outcome, source, reason = "won", old["source"], ("消息确认中奖；与官方结果冲突，请核查" if conflict else old.get("reason") or "已确认中奖，保留")
    with _lock, session_scope() as session:
        row = session.get(DrawOutcomeRow, (uid, did))
        if row is None:
            row = DrawOutcomeRow(uid=uid, dynamic_id=did)
            session.add(row)
        row.outcome, row.reason, row.source, row.checked_at = outcome, reason, source, now
        # A transient unknown result blocks cleanup without restarting the original grace clock.
        row.lost_at = (row.lost_at or now) if outcome == "lost" else (row.lost_at if outcome == "unknown" else 0)
        row.evidence_json = json.dumps(notice or {}, ensure_ascii=False)
        link = session.get(ParticipationLinkRow, (uid, did))
        if link and not link.up_uid and notice and str(notice.get("sender_uid") or "").isdigit():
            link.up_uid = str(notice["sender_uid"])
        if old["outcome"] != outcome:
            audit(session, uid, did, "draw_check", did, outcome, reason)
    if outcome == "won":
        safe_enqueue(uid, "won", f"won:{did}", "抽奖已中奖", f"活动 {did} 已确认中奖，请查看并联系领奖。\nhttps://www.bilibili.com/opus/{did}")
    return get_outcome(uid, did)


def mark_outcome(uid: str, did: str, outcome: str):
    if outcome not in ("won", "lost", "unknown"):
        raise ValueError("结果类型无效")
    if did not in activities_for_uid(uid):
        raise ValueError("活动不属于当前账号的参与记录")
    with _lock, session_scope() as session:
        row = session.get(DrawOutcomeRow, (uid, did))
        if row and (row.outcome == "won" or (row.source == "official" and row.outcome in ("won", "lost"))) and outcome != row.outcome:
            raise ValueError("已核验的官方结果或中奖记录不能改为未中奖，请使用保留标记")
        if row is None:
            row = DrawOutcomeRow(uid=uid, dynamic_id=did)
            session.add(row)
        row.lost_at = (row.lost_at or int(time.time())) if outcome == "lost" else 0
        row.outcome, row.source, row.reason = outcome, "manual", "用户人工确认"
        row.checked_at = int(time.time())
        audit(session, uid, did, "manual_result", did, outcome, "用户人工确认")


def mark_keep(uid: str, did: str, keep: bool):
    if did not in activities_for_uid(uid):
        raise ValueError("活动不属于当前账号")
    with _lock, session_scope() as session:
        row = session.get(DrawOutcomeRow, (uid, did))
        if row is None:
            row = DrawOutcomeRow(uid=uid, dynamic_id=did)
            session.add(row)
        row.keep = keep
        audit(session, uid, did, "keep", did, "kept" if keep else "released", "更新保留标记")


def confirm_link(api, uid: str, did: str, up_uid: str, repost_id: str, follow_added: bool):
    if did not in activities_for_uid(uid):
        raise ValueError("活动不属于当前账号")
    original = api.dynamic(did)
    author = ((original or {}).get("modules") or {}).get("module_author") or {}
    if str(author.get("mid")) != up_uid:
        raise ValueError("无法验证 UP 主与原活动的关联")
    if repost_id:
        item = api.dynamic(repost_id)
        if not item or not own_repost(item, uid, did):
            raise ValueError("转发动态不属于当前账号或未对应此活动")
    with _lock, session_scope() as session:
        row = session.get(ParticipationLinkRow, (uid, did))
        if row is None:
            row = ParticipationLinkRow(uid=uid, dynamic_id=did)
            session.add(row)
        # Never retarget an operation that was already attempted.
        if row.repost_id and row.repost_id != repost_id and session.get(CleanupStepRow, (uid, "delete", row.repost_id)):
            raise ValueError("原转发已有清理记录，不能替换关联")
        row.up_uid, row.repost_id = up_uid, repost_id
        row.follow_added, row.followed_before = follow_added, not follow_added
        row.repost_recorded, row.origin = bool(repost_id), "confirmed"
        audit(session, uid, did, "confirm_link", repost_id or up_uid, "confirmed", "用户核对历史关联与新增关注")


def state_for(uid, action, target):
    with session_scope() as session:
        row = session.get(CleanupStepRow, (uid, action, target))
        return row.state if row else "pending"


def record_step(uid, did, action, target, state, message):
    with _lock, session_scope() as session:
        row = session.get(CleanupStepRow, (uid, action, target))
        if row is None:
            row = CleanupStepRow(uid=uid, action=action, target_id=target, dynamic_id=did)
            session.add(row)
        row.state, row.message, row.updated_at = state, message[:500], int(time.time())
        if state == "running":
            row.attempts += 1
        audit(session, uid, did, action, target, state, message)


def eligible(outcome, link, now):
    return (outcome["outcome"] == "lost" and outcome["lost_at"] > 0
            and now >= outcome["lost_at"] + GRACE_SECONDS and not outcome["keep"]
            and link["origin"] in ("new", "confirmed"))


def link_rows(uid):
    with session_scope() as session:
        return {r.dynamic_id: r.model_dump() for r in session.exec(select(ParticipationLinkRow).where(ParticipationLinkRow.uid == uid)).all()}


def execute_step(api, uid, did, action, target, original):
    if state_for(uid, action, target) == "done":
        return False
    try:
        api.verify_login()
        if action == "delete":
            item = api.dynamic(target)
            if item is None:
                record_step(uid, did, action, target, "done", "对应动态已不存在")
                return False
            if not own_repost(item, uid, original):
                record_step(uid, did, action, target, "blocked", "归属或原动态关联不符，已保留")
                return False
        elif not api.following(target):
            record_step(uid, did, action, target, "done", "已不再关注")
            return False
        check_cancelled()
        record_step(uid, did, action, target, "running", "已核验，正在执行")
        if action == "delete":
            api.delete_repost(target)
        else:
            api.unfollow(target)
        record_step(uid, did, action, target, "done", "已删除对应转发" if action == "delete" else "已取关")
        return True
    except TaskCancelled:
        raise
    except MaintenanceBlocked:
        record_step(uid, did, action, target, "unknown", "登录失效或限流，停止维护；下次先核验状态")
        raise
    except Exception:
        record_step(uid, did, action, target, "unknown", "结果待核实；再次执行前先查询当前状态")
        return False


def run_maintenance(uid: str, *, clean=False, progress=None, api=None, only_id: str = "", manual_clean: bool = True):
    if api is None:
        from src.bilibili_client import BilibiliClient
        with BilibiliClient(timeout=15, warmup=False) as client:
            return run_maintenance(uid, clean=clean, progress=progress, api=CleanupApi(client, uid), only_id=only_id, manual_clean=manual_clean)
    api.verify_login()
    from src.message_review import check_messages, review_status, cleanup_block_reason
    message_check = check_messages(uid, getattr(api, "client", None))
    message_checked_at = time.monotonic()
    if clean and manual_clean:
        from src.message_review import release_cleanup_hold
        message_check = release_cleanup_hold(uid)
    def ensure_messages_clear():
        nonlocal message_check, message_checked_at
        # Never hide newer local evidence behind this round's successful snapshot.
        latest = review_status(uid)
        if latest["blocked"]:
            raise MaintenanceBlocked(cleanup_block_reason(latest))
        if time.monotonic() - message_checked_at >= MESSAGE_RECHECK_SECONDS:
            message_check = check_messages(uid, getattr(api, "client", None))
            message_checked_at = time.monotonic()
        if message_check["blocked"]:
            raise MaintenanceBlocked(cleanup_block_reason(message_check))

    items = activities_for_uid(uid)
    sync_candidates(uid, items)
    deleted = unfollowed = checked_count = uncertain = 0
    delete_attempts = unfollow_attempts = 0
    try:
        for did, item in items.items():
            check_cancelled()
            # All related records must be checked, even on a single-item retry.
            check_outcome(api, uid, did, item)
            checked_count += 1
            if progress:
                progress(step=checked_count, total=max(len(items), 1), message=f"已核验 {checked_count}/{len(items)} 个活动")
        if clean and settings(uid)["enabled"] and not message_check["blocked"]:
            links = link_rows(uid)
            def oldest_first(pair):
                did, link = pair
                # Draw date describes age; imported links may have no creation date.
                stamp = int(items.get(did, {}).get("lottery_time") or link.get("created_at") or 0)
                return (stamp if stamp > 0 else float("inf"), int(did) if did.isdigit() else 0, did)
            links = dict(sorted(links.items(), key=oldest_first))
            for did, link in links.items():
                check_cancelled()
                cfg = settings(uid)
                if not cfg["enabled"] or delete_attempts >= CLEANUP_BATCH_LIMIT:
                    break
                if state_for(uid, "delete", link["repost_id"]) == "done":
                    continue
                if only_id and did != only_id:
                    continue
                outcome = get_outcome(uid, did)
                if not eligible(outcome, link, int(time.time())):
                    continue
                outcome = check_outcome(api, uid, did, items[did])
                if not eligible(outcome, link, int(time.time())):
                    continue
                if cfg["delete_reposts"] and link["repost_id"] and link["repost_recorded"]:
                    # Serialize user keep/settings edits with each checked destructive action.
                    with _lock:
                        if settings(uid)["enabled"] and settings(uid)["delete_reposts"] and not get_outcome(uid, did)["keep"]:
                            ensure_messages_clear()
                            delete_attempts += 1
                            deleted += execute_step(api, uid, did, "delete", link["repost_id"], did)
                            uncertain += state_for(uid, "delete", link["repost_id"]) in ("unknown", "blocked")
            # Unknown historical UP associations could belong to any group. Resolve before unfollowing.
            if settings(uid)["enabled"] and settings(uid)["unfollow"] and all(l["up_uid"] for l in links.values()):
                groups = {}
                for did, link in links.items():
                    if link["up_uid"]:
                        groups.setdefault(link["up_uid"], []).append((did, link))
                for up, group in groups.items():
                    check_cancelled()
                    cfg = settings(uid)
                    if not cfg["enabled"] or not cfg["unfollow"] or unfollow_attempts >= CLEANUP_BATCH_LIMIT:
                        break
                    if state_for(uid, "unfollow", up) == "done":
                        continue
                    if only_id and all(did != only_id for did, _ in group):
                        continue
                    if up in cfg["protected_ups"] or not any(l["follow_added"] for _, l in group):
                        continue
                    if any(not eligible(get_outcome(uid, did), link, int(time.time())) for did, link in group):
                        continue
                    if cfg["delete_reposts"] and any(
                        # A forward requiring deletion with no verified ID blocks unfollow too.
                        (items[did].get("lottery_type") != "预约抽奖" and not link["repost_recorded"])
                        or (link["repost_id"] and state_for(uid, "delete", link["repost_id"]) != "done")
                        for did, link in group):
                        continue
                    # Recheck all draws associated with this UP immediately before unfollow.
                    refreshed = [check_outcome(api, uid, did, items[did]) for did, _ in group]
                    if any(not eligible(out, link, int(time.time())) for out, (_, link) in zip(refreshed, group)):
                        continue
                    with _lock:
                        cfg = settings(uid)
                        if (cfg["enabled"] and cfg["unfollow"] and up not in cfg["protected_ups"]
                                and not any(get_outcome(uid, did)["keep"] for did, _ in group)):
                            ensure_messages_clear()
                            unfollow_attempts += 1
                            unfollowed += execute_step(api, uid, group[0][0], "unfollow", up, group[0][0])
                            uncertain += state_for(uid, "unfollow", up) in ("unknown", "blocked")
    except MaintenanceBlocked as exc:
        safe_enqueue(uid, "failure", f"maintenance-blocked:{int(time.time()) // 1800}", "维护已暂停", str(exc))
        raise
    finally:
        if clean and (deleted or unfollowed or uncertain):
            safe_enqueue(uid, "cleanup", f"cleanup:{time.time_ns()}", "清理批次汇总", f"已删除 {deleted} 条转发，已取关 {unfollowed} 位 UP，{uncertain} 项需核实。详细结果见开奖与清理。")
    if message_check["blocked"]:
        return {"ok": False, "message": "开奖结果已核验；" + cleanup_block_reason(message_check),
                "result": {"checked": checked_count, "deleted": deleted, "unfollowed": unfollowed, "message_review": message_check}}
    return {"ok": not uncertain, "message": f"核验 {checked_count} 个活动；删除 {deleted} 条转发，取关 {unfollowed} 位 UP" + ("；本轮已达批次上限，剩余活动留待下轮按旧到新处理" if max(delete_attempts, unfollow_attempts) >= CLEANUP_BATCH_LIMIT else "") + (f"；{uncertain} 项需核实或已保留，请查看执行记录" if uncertain else ""),
            "result": {"checked": checked_count, "deleted": deleted, "unfollowed": unfollowed, **({"needs_verification": uncertain} if uncertain else {})}}


def list_candidates(uid: str) -> list[dict]:
    items = activities_for_uid(uid)
    sync_candidates(uid, items)
    cfg, links = settings(uid), link_rows(uid)
    from src.repost_discovery import associations
    association_info = associations(uid, links)
    result = []
    for did, item in items.items():
        row, link = get_outcome(uid, did), links[did]
        reasons = []
        if row["keep"]:
            reasons.append("已标记保留")
        if row["outcome"] != "lost":
            reasons.append("已中奖，保留" if row["outcome"] == "won" else "尚未确认未中奖")
        if link["origin"] == "historical":
            reasons.append("历史记录待核对")
        if link["up_uid"] in cfg["protected_ups"]:
            reasons.append("UP 在保留名单")
        if not link["follow_added"]:
            reasons.append("非本软件新增关注，保留关注")
        if any(not other["up_uid"] for other in links.values()):
            reasons.append("存在 UP 关联未知的参与记录，暂缓取关")
        if not link["repost_recorded"] and item.get("lottery_type") != "预约抽奖":
            reasons.append("本人转发待核对")
        siblings = [other for other, l in links.items() if other != did and link["up_uid"] and l["up_uid"] == link["up_uid"]]
        if any(get_outcome(uid, other)["outcome"] != "lost" or get_outcome(uid, other)["keep"] for other in siblings):
            reasons.append("同一 UP 尚有关联活动需保留")
        due = row["lost_at"] + GRACE_SECONDS if row["lost_at"] else 0
        if not cfg["enabled"]:
            reasons.append("自动清理未开启")
        elif row["outcome"] == "lost" and due > int(time.time()):
            reasons.append("等待 24 小时保护期结束")
        prizes = item.get("prizes") or []
        title = str((prizes[0].get("description") if prizes and isinstance(prizes[0], dict) else "") or item.get("title") or did)
        result.append({**row, "evidence_json": None, "link": link, "title": title, "association": association_info[did],
            "delete_judgement": "已完成" if state_for(uid, "delete", link["repost_id"]) == "done" else
                "删除开关未开启" if not cfg["enabled"] or not cfg["delete_reposts"] else
                "本人转发尚未核验" if not link["repost_recorded"] or not link["repost_id"] else
                "满足本地条件，执行前复核" if eligible(row, link, int(time.time())) else "尚未满足未中奖、保护期或关联条件",
            "unfollow_judgement": "已完成" if state_for(uid, "unfollow", link["up_uid"]) == "done" else
                "取关开关未开启" if not cfg["enabled"] or not cfg["unfollow"] else
                "原有关注／未证明新增，保留" if not any(l["follow_added"] for l in links.values() if l["up_uid"] == link["up_uid"]) else
                "按同一 UP 全部活动合并核验；先确认关联转发已删除",
            "due_at": due, "reasons": reasons, "delete_state": state_for(uid, "delete", link["repost_id"]),
            "unfollow_state": state_for(uid, "unfollow", link["up_uid"])})
    return sorted(result, key=lambda row: (row["outcome"] != "won", -(row["checked_at"] or 0), row["dynamic_id"]))


def discover_associations(api, uid, *, mode="recent", progress=None):
    from src.repost_discovery import discover
    return discover(api, uid, mode=mode, progress=progress)


def audit_history(uid, did=""):
    with session_scope() as session:
        query = select(MaintenanceAuditRow).where(MaintenanceAuditRow.uid == uid)
        if did:
            query = query.where(MaintenanceAuditRow.dynamic_id == did)
        return [r.model_dump() for r in session.exec(query.order_by(MaintenanceAuditRow.id.desc()).limit(100)).all()]


def cleanup_history(uid, before=0):
    """Account-scoped cleanup results, excluding noisy draw/follow bookkeeping."""
    with session_scope() as session:
        steps = session.exec(select(CleanupStepRow).where(CleanupStepRow.uid == uid)).all()
        outcomes = session.exec(select(DrawOutcomeRow).where(DrawOutcomeRow.uid == uid)).all()
        query = select(MaintenanceAuditRow).where(MaintenanceAuditRow.uid == uid,
            MaintenanceAuditRow.action.in_(["delete", "unfollow"]))
        if before:
            query = query.where(MaintenanceAuditRow.id < before)
        rows = session.exec(query.order_by(MaintenanceAuditRow.id.desc()).limit(51)).all()
        return {"items": [r.model_dump() for r in rows[:50]],
            "next_before": rows[49].id if len(rows) > 50 else None,
            "deleted": sum(r.action == "delete" and r.state == "done" for r in steps),
            "unfollowed": sum(r.action == "unfollow" and r.state == "done" for r in steps),
            "needs_review": sum(r.state in ("unknown", "blocked") for r in steps),
            "outcomes": {key: sum(r.outcome == key for r in outcomes) for key in ("pending", "unknown", "won", "lost")},
            "settings": settings(uid)}
