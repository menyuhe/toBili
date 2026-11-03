"""Read-only timeline discovery with a checkpoint committed after each page."""
from __future__ import annotations

import json
import time
from sqlmodel import select

from src.cleanup_api import own_repost
from src.db.models import ParticipationLinkRow, RepostDiscoveryRow, RepostScanRow
from src.db.session import session_scope
from src.task_control import check_cancelled

LABELS = {
    "confirmed": "已核验关联", "candidate": "已匹配待核对",
    "ambiguous": "存在多条候选／信息冲突", "not_found": "扫描范围内未找到",
    "unscanned": "尚未扫描",
}


def scan_status(uid):
    with session_scope() as session:
        row = session.get(RepostScanRow, uid)
        data = row.model_dump() if row else RepostScanRow(uid=uid).model_dump()
    data["can_continue"] = bool(data.pop("offset") and not data["finished"])
    data.pop("uid")
    return data


def association(link, discovery=None):
    choices = json.loads(discovery.choices_json) if discovery else {}
    # Preserve candidates discovered by earlier versions without granting eligibility.
    if link["repost_id"] and not link["repost_recorded"]:
        choices.setdefault(link["repost_id"], link["up_uid"])
    conflict = any(link["up_uid"] and up and up != link["up_uid"] for up in choices.values())
    status = ("confirmed" if link["repost_recorded"] or link["origin"] == "confirmed" else
              "ambiguous" if len(choices) > 1 or conflict else
              "candidate" if choices else "not_found" if discovery and discovery.checked_at else "unscanned")
    return {"state": status, "label": LABELS[status], "checked_at": discovery.checked_at if discovery else 0,
            "candidates": [{"repost_id": rid, "up_uid": up} for rid, up in choices.items()]}


def associations(uid, links):
    with session_scope() as session:
        records = {row.dynamic_id: row for row in session.exec(select(RepostDiscoveryRow).where(RepostDiscoveryRow.uid == uid))}
        return {did: association(link, records.get(did)) for did, link in links.items()}


def discover(api, uid, *, mode="recent", progress=None):
    from src import maintenance as m
    if mode not in ("recent", "continue"):
        raise ValueError("无效的扫描方式")
    items = m.activities_for_uid(uid)
    m.sync_candidates(uid, items)
    if not items:
        return {"ok": True, "message": "暂无已参与活动可匹配", "result": {"matched": 0, "ambiguous": 0}}
    with session_scope() as session:
        saved = session.get(RepostScanRow, uid)
        if mode == "continue" and (not saved or saved.finished or not saved.offset):
            raise ValueError("没有可继续的扫描，请先扫描最近动态")
        checkpoint = saved.model_dump() if mode == "continue" else RepostScanRow(uid=uid).model_dump()
    visited = set()
    for page in range(5):
        check_cancelled()
        offset = checkpoint["offset"]
        if offset in visited:
            raise RuntimeError("动态分页重复，已停止扫描；请从最近动态重新扫描")
        visited.add(offset)
        data = api.repost_page(offset)
        next_offset = data["offset"] if data["has_more"] else ""
        if data["has_more"] and next_offset in visited:
            raise RuntimeError("动态分页重复，已保留扫描进度")
        found = {}
        oldest = checkpoint["oldest_at"]
        for item in data["items"]:
            check_cancelled()
            author = (item.get("modules") or {}).get("module_author") or {}
            published = author.get("pub_ts")
            if isinstance(published, int) and published > 0:
                oldest = min(oldest or published, published)
            original = str((item.get("orig") or {}).get("id_str") or "")
            repost_id = str(item.get("id_str") or "")
            if original in items and repost_id.isdigit() and own_repost(item, uid, original):
                up = ((item.get("orig") or {}).get("modules") or {}).get("module_author") or {}
                found.setdefault(original, {})[repost_id] = str(up.get("mid") or "")
        api.assert_account()
        now = int(time.time())
        # Cursor and matches commit together. A failed/cancelled page can be retried safely.
        with m._lock, session_scope() as session:
            for did in items:
                check_cancelled()
                link = session.get(ParticipationLinkRow, (uid, did))
                if not link or link.repost_recorded or link.origin == "confirmed":
                    continue
                row = session.get(RepostDiscoveryRow, (uid, did))
                if row is None:
                    row = RepostDiscoveryRow(uid=uid, dynamic_id=did)
                    session.add(row)
                choices = json.loads(row.choices_json)
                if link.repost_id:
                    choices.setdefault(link.repost_id, link.up_uid)
                choices.update(found.get(did, {}))
                row.choices_json = json.dumps(choices)
                row.checked_at = now
                info = association(link.model_dump(), row)
                if info["state"] == "candidate":
                    repost_id, up = next(iter(choices.items()))
                    if link.repost_id != repost_id:
                        m.audit(session, uid, did, "association_candidate", repost_id, "unknown", "找到本人转发候选，等待人工核对")
                    link.repost_id = repost_id
                    if not link.up_uid and up.isdigit():
                        link.up_uid = up
                elif info["state"] == "ambiguous":
                    link.repost_id = ""
            checkpoint.update(offset=next_offset, pages=checkpoint["pages"] + 1,
                              scanned=checkpoint["scanned"] + len(data["items"]), oldest_at=oldest,
                              finished=not bool(data["has_more"]), updated_at=now)
            stored = session.get(RepostScanRow, uid)
            if stored is None:
                stored = RepostScanRow(uid=uid)
                session.add(stored)
            for key, value in checkpoint.items():
                setattr(stored, key, value)
        if progress:
            progress(step=page + 1, total=5, message=f"已扫描 {checkpoint['pages']} 页、{checkpoint['scanned']} 条动态；逐页保存进度")
        if checkpoint["finished"]:
            break
    states = associations(uid, m.link_rows(uid))
    counts = {key: sum(item["state"] == key for item in states.values()) for key in LABELS}
    tail = "已到动态列表末尾" if checkpoint["finished"] else "可点击“继续扫描更早动态”"
    return {"ok": True, "message": f"已匹配 {counts['candidate']} 条待核对，{counts['ambiguous']} 条多候选／冲突，{counts['not_found']} 条在扫描范围内未找到；{tail}。未找到不代表未转发。",
            "result": {"matched": counts["candidate"], "ambiguous": counts["ambiguous"], "association_counts": counts, "scan": scan_status(uid)}}
