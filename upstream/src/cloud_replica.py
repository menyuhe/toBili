"""Allowlisted desktop projection and durable sync outbox. No cookie/config export."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager

from sqlmodel import select

from src.cloud_protocol import PAYLOADS, canonical


def snapshot(uid):
    from src.db.models import (ActivityRow, ParticipationRow, ParticipationLinkRow, DrawOutcomeRow,
                               CleanupStepRow, MaintenanceSettingsRow, ProtectedUpRow, ActivityArchiveRow, UserSettingsRow)
    from src.db.session import session_scope
    from src.db.activity_codec import row_to_activity_dict
    from src.log_redact import redact_text
    from src.accounts import read_state, overlay_activities
    from src.account_context import account_scope
    registered = bool(read_state(uid, "account"))
    result = {}

    def add(kind, key, value):
        result[(kind, key)] = {"deleted": False, "value": PAYLOADS[kind].model_validate(value).model_dump()}

    with session_scope() as session:
        rows = session.exec(select(ActivityRow)).all()
        projected = [row_to_activity_dict(row) for row in rows]
        if registered:
            with account_scope(uid):
                projected = overlay_activities(projected)
        for row, item in zip(rows, projected):
            prizes = item.get("prizes") or []
            title = item.get("activity_title") or item.get("title") or (prizes[0].get("description") if prizes and isinstance(prizes[0], dict) else "") or row.dynamic_id
            if not row.dynamic_id.isdigit():
                continue
            add("activity", row.dynamic_id, {
                "dynamic_id": row.dynamic_id, "title": redact_text(str(title))[:1000],
                "lottery_type": str(row.lottery_type or ""), "lottery_time": row.lottery_time,
                "sender_uid": str(item.get("sender_uid") or ""), "business_id": str(row.business_id or ""),
                "business_type": str(row.business_type or ""), "activity_status": str(item.get("activity_status") or ""),
                "draw_status": str(row.draw_status or ""),
            })
        for row in session.exec(select(ParticipationRow).where(ParticipationRow.uid == uid)).all():
            add("participation", row.dynamic_id, {"user_status": row.user_status, "updated_at": row.updated_at})
        for row in session.exec(select(ParticipationLinkRow).where(ParticipationLinkRow.uid == uid)).all():
            add("link", row.dynamic_id, {key: getattr(row, key) for key in PAYLOADS["link"].model_fields})
        for row in session.exec(select(DrawOutcomeRow).where(DrawOutcomeRow.uid == uid)).all():
            value = {key: getattr(row, key) for key in PAYLOADS["outcome"].model_fields}
            if value.get("source") == "message":
                value["source"] = "manual"  # existing relay protocol; evidence stays local
            value["reason"] = redact_text(value["reason"])[:500]
            add("outcome", row.dynamic_id, value)
            add("keep", row.dynamic_id, {"keep": row.keep})
        cfg = session.get(MaintenanceSettingsRow, uid)
        if cfg:
            add("cleanup_settings", "main", {key: getattr(cfg, key) for key in PAYLOADS["cleanup_settings"].model_fields})
        for row in session.exec(select(ProtectedUpRow).where(ProtectedUpRow.uid == uid)).all():
            add("protected_up", row.up_uid, {"protected": True})
        for row in session.exec(select(CleanupStepRow).where(CleanupStepRow.uid == uid)).all():
            value = {key: getattr(row, key) for key in PAYLOADS["cleanup_step"].model_fields}
            value["message"] = redact_text(value["message"])[:500]
            add("cleanup_step", f"{row.action}:{row.target_id}", value)
        if registered:
            times = read_state(uid, "archive_times", {})
            for did in read_state(uid, "archives", []):
                add("archive", did, {"archived_at": times.get(did, 1)})
        else:
            for row in session.exec(select(ActivityArchiveRow)).all():
                add("archive", row.dynamic_id, {"archived_at": row.archived_at})
        user_settings = session.get(UserSettingsRow, uid)
        if user_settings:
            add("participation_settings", "main", {key: getattr(user_settings, key) for key in PAYLOADS["participation_settings"].model_fields})
    from src.participation_pool import _path, snapshot as pool_snapshot
    if _path(uid).exists():
        add("selection", "main", {"ids": [r["dynamic_id"] for r in pool_snapshot(uid)["items"]]})
    return result


def apply_record(uid, record):
    from src.db.models import (ActivityRow, ParticipationRow, ParticipationLinkRow, DrawOutcomeRow,
                               CleanupStepRow, MaintenanceSettingsRow, ProtectedUpRow, ActivityArchiveRow, UserSettingsRow)
    from src.db.session import session_scope
    from src.db.activity_codec import activity_dict_to_row, row_to_activity_dict
    kind, key = record["kind"], record["key"]
    value = record["value"]
    # Validate server responses too, before they enter a local database.
    from src.cloud_protocol import Change
    Change(change_id="validate_record", kind=kind, key=key, base_version=0,
           deleted=record["deleted"], value=value)
    from src.accounts import read_state, write_state, lock, save_activity_state, ACTIVITY_FIELDS
    registered = bool(read_state(uid, "account"))
    if kind == "archive" and registered:
        with lock:
            ids = set(read_state(uid, "archives", []))
            times = read_state(uid, "archive_times", {})
            reasons = read_state(uid, "archive_reasons", {})
            if record["deleted"]:
                ids.discard(key)
                times.pop(key, None)
                reasons.pop(key, None)
            else:
                if key not in ids or times.get(key) != value["archived_at"]:
                    reasons[key] = "synced"
                ids.add(key)
                times[key] = value["archived_at"]
            write_state(uid, "archives", sorted(ids))
            write_state(uid, "archive_times", times)
            write_state(uid, "archive_reasons", reasons)
        return
    if kind == "selection":
        from src import participation_pool as pool
        with pool._lock, session_scope() as session:
            previous = pool._load(uid)
            existing = {r['dynamic_id']: r for r in previous['items']}
            if list(existing) == value['ids']:
                return
            rows = []
            for did in value["ids"]:
                if did in existing:
                    rows.append(existing[did])
                    continue
                activity = session.get(ActivityRow, did)
                item = row_to_activity_dict(activity) if activity else {}
                rows.append({"dynamic_id": did, "title": item.get("activity_title") or did,
                             "lottery_type": item.get("lottery_type") or "", "state": "pending", "message": "从云端同步"})
            if str(uid) in pool._active:
                raise RuntimeError("预选池执行中，稍后再同步")
            # Keep a recoverable copy before any cloud replacement.
            from src.app_paths import data_dir
            import time
            backup = data_dir() / 'pool-sync-backups' / str(uid)
            backup.mkdir(parents=True, exist_ok=True)
            (backup / f'{time.time_ns()}.json').write_text(canonical(previous), encoding='utf-8')
            pool._save(uid, {**previous, "items": rows, "paused": True})
        return
    with session_scope() as session:
        if kind == "activity":
            old = session.get(ActivityRow, key)
            item = row_to_activity_dict(old) if old else {"dynamic_id": key, "source_url": f"https://www.bilibili.com/opus/{key}"}
            item.update(value)
            item["activity_title"] = value["title"]
            if registered:
                save_activity_state(uid, {**value, "dynamic_id": key}, session=session)
                for field in ACTIVITY_FIELDS:
                    item.pop(field, None)
                if item.get("activity_status") == "已参加":
                    item["activity_status"] = "未参加"
            # Existing rich details stay local. New replicas re-enrich before participating.
            if not old:
                item["cloud_summary_only"] = True
            session.merge(activity_dict_to_row(item))
            from web.activity_service import invalidate_activity_cache
            invalidate_activity_cache()
            return
        models = {"participation": ParticipationRow, "link": ParticipationLinkRow, "outcome": DrawOutcomeRow,
                  "keep": DrawOutcomeRow, "cleanup_settings": MaintenanceSettingsRow, "protected_up": ProtectedUpRow,
                  "archive": ActivityArchiveRow, "cleanup_step": CleanupStepRow, "participation_settings": UserSettingsRow}
        model = models[kind]
        if kind == "cleanup_step":
            action, target = key.split(":", 1)
            identity, initial = (uid, action, target), {"uid": uid, "action": action, "target_id": target}
        elif kind in {"cleanup_settings", "participation_settings"}:
            identity, initial = uid, {"uid": uid}
        elif kind == "protected_up":
            identity, initial = (uid, key), {"uid": uid, "up_uid": key}
        elif kind == "archive":
            identity, initial = key, {"dynamic_id": key}
        else:
            identity, initial = (uid, key), {"uid": uid, "dynamic_id": key}
        row = session.get(model, identity)
        if row is not None and kind in {"outcome", "keep"} and row.source == "message":
            return  # never discard locally confirmed prize evidence through an older relay
        if record["deleted"]:
            if row:
                session.delete(row)
            return
        if row is None:
            # Required fields (e.g. participation.user_status) must be set at construction.
            row = model(**initial, **({k: v for k, v in value.items() if k != "protected"}))
            session.add(row)
        else:
            for field, field_value in value.items():
                if field != "protected":
                    setattr(row, field, field_value)
    try:
        from web.activity_service import invalidate_activity_cache
        invalidate_activity_cache()
    except ImportError:
        pass


class Ledger:
    def __init__(self, path, namespace):
        self.path, self.namespace = path, namespace
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS shadows(namespace TEXT, kind TEXT, key TEXT, record TEXT,
                    PRIMARY KEY(namespace,kind,key));
                CREATE TABLE IF NOT EXISTS outbox(namespace TEXT, kind TEXT, key TEXT, change TEXT,
                    PRIMARY KEY(namespace,kind,key));
                CREATE TABLE IF NOT EXISTS conflicts(namespace TEXT, kind TEXT, key TEXT, local TEXT, remote TEXT,
                    PRIMARY KEY(namespace,kind,key));
                CREATE TABLE IF NOT EXISTS cursors(namespace TEXT PRIMARY KEY, value INTEGER);
            """)

    @contextmanager
    def db(self):
        with sqlite3.connect(self.path, timeout=20) as db:
            yield db

    def shadows(self):
        with self.db() as db:
            return {(kind, key): json.loads(record) for kind, key, record in db.execute(
                "SELECT kind,key,record FROM shadows WHERE namespace=?", (self.namespace,))}

    def remember(self, record):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO shadows VALUES(?,?,?,?)",
                       (self.namespace, record["kind"], record["key"], canonical(record)))

    def capture(self, current):
        shadows = self.shadows()
        with self.db() as db:
            conflicted = {(k, key) for k, key in db.execute("SELECT kind,key FROM conflicts WHERE namespace=?", (self.namespace,))}
            pending = {(k, key) for k, key in db.execute("SELECT kind,key FROM outbox WHERE namespace=?", (self.namespace,))}
            for pair in set(current) | set(shadows):
                if pair in conflicted or pair in pending:
                    continue
                old, value = shadows.get(pair), current.get(pair)
                if value is None:
                    if pair[0] not in {"archive", "protected_up"} or not old or old["deleted"]:
                        continue
                    value = {"deleted": True, "value": {}}
                if old and old["deleted"] == value["deleted"] and old["value"] == value["value"]:
                    continue
                change = {"change_id": uuid.uuid4().hex, "kind": pair[0], "key": pair[1],
                          "base_version": old["version"] if old else 0, **value}
                db.execute("INSERT INTO outbox VALUES(?,?,?,?)", (self.namespace, *pair, canonical(change)))

    def pending(self):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT change FROM outbox WHERE namespace=? ORDER BY kind,key LIMIT 100", (self.namespace,))]

    def pop(self, pair):
        with self.db() as db:
            db.execute("DELETE FROM outbox WHERE namespace=? AND kind=? AND key=?", (self.namespace, *pair))

    def conflict(self, pair, local, remote):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO conflicts VALUES(?,?,?,?,?)",
                       (self.namespace, *pair, canonical(local), canonical(remote)))
            db.execute("DELETE FROM outbox WHERE namespace=? AND kind=? AND key=?", (self.namespace, *pair))

    def conflicts(self):
        with self.db() as db:
            return [{"kind": k, "key": key, "local": json.loads(local), "remote": json.loads(remote)}
                    for k, key, local, remote in db.execute("SELECT kind,key,local,remote FROM conflicts WHERE namespace=?", (self.namespace,))]

    def clear_conflict(self, pair):
        with self.db() as db:
            db.execute("DELETE FROM conflicts WHERE namespace=? AND kind=? AND key=?", (self.namespace, *pair))

    def cursor(self, value=None):
        with self.db() as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO cursors VALUES(?,?)", (self.namespace, value))
            row = db.execute("SELECT value FROM cursors WHERE namespace=?", (self.namespace,)).fetchone()
            return row[0] if row else 0


def merge_selection(base, local, remote, *, prefer_remote=False):
    """Three-way membership merge: independent additions survive; removals stay removed."""
    before = set((base or {}).get('value', {}).get('ids', []))
    left, right = local['value']['ids'], remote['value']['ids']
    removed = (before - set(left)) | (before - set(right))
    remote_order = prefer_remote or (base is not None and left == base['value']['ids'])
    order = [*right, *left] if remote_order else [*left, *right]
    ids = list(dict.fromkeys(did for did in order if did not in removed))
    if len(ids) > 200:
        raise ValueError('合并后预选超过 200 条，两端内容均已保留，请移出部分活动后重试')
    return {'deleted': False, 'value': {'ids': ids}}


def reconcile_selection(uid, ledger, remote, *, prefer_remote=False):
    from src import participation_pool as pool
    with pool._lock:
        # Read at the moment of application, never use a conflict dialog's old snapshot.
        local = {'deleted': False, 'value': {'ids': [r['dynamic_id'] for r in pool._load(uid)['items']]}}
        base = ledger.shadows().get(('selection', 'main'))
        if base and remote['version'] < base['version']:
            remote = base
        merged = merge_selection(base, local, remote, prefer_remote=prefer_remote)
        apply_record(uid, {'kind': 'selection', 'key': 'main', **merged})
        ledger.remember(remote)
        ledger.pop(('selection', 'main'))
        ledger.clear_conflict(('selection', 'main'))


def synchronize(uid, ledger, request):
    # Repair legacy whole-pool conflicts using the current queue, including edits
    # made since the conflict was first recorded. Never restore the old local copy.
    for conflict in ledger.conflicts():
        if conflict['kind'] == 'selection' and conflict['remote']:
            reconcile_selection(uid, ledger, conflict['remote'])
    for _ in range(3):
        result = _synchronize_pass(uid, ledger, request)
        ledger.capture(snapshot(uid))
        result['pending'] = len(ledger.pending())
        if not result['pending']:
            break
    return result


def _synchronize_pass(uid, ledger, request):
    """CAS + durable operation receipts, then cursor pull; conflicts block execution."""
    # A second pass catches local edits that arrived while a pending send was retried.
    for _round in range(2):
        ledger.capture(snapshot(uid))
        while batch := ledger.pending():
            result = request("POST", "/sync/push", {"bili_uid": uid, "changes": batch})
            by_id = {c["change_id"]: c for c in batch}
            for response in result["results"]:
                change = by_id[response["change_id"]]
                pair = (change["kind"], change["key"])
                if "conflict" in response:
                    remote = response["conflict"]
                    if pair[0] == 'selection' and remote:
                        reconcile_selection(uid, ledger, remote)
                        continue
                    # Concurrent identical updates need no manual decision.
                    if remote and remote["deleted"] == change["deleted"] and remote["value"] == change["value"]:
                        ledger.remember(remote)
                        ledger.pop(pair)
                    else:
                        ledger.conflict(pair, {"deleted": change["deleted"], "value": change["value"]}, remote)
                else:
                    ledger.remember(response["record"])
                    ledger.pop(pair)
    while True:
        page = request("GET", f"/sync/pull?bili_uid={uid}&after={ledger.cursor()}&limit=200")
        conflicts = {(c["kind"], c["key"]) for c in ledger.conflicts()}
        shadows, current = ledger.shadows(), snapshot(uid)
        for record in page["items"]:
            pair = (record["kind"], record["key"])
            old = shadows.get(pair)
            # A push acknowledgement may already be newer than this page. Echoes
            # must not overwrite edits made locally while the request was in flight.
            if old and record['version'] <= old['version']:
                continue
            if pair[0] == 'selection':
                reconcile_selection(uid, ledger, record)
                continue
            if pair in conflicts:
                # Always retain the newest remote revision for resolution.
                local = next(c["local"] for c in ledger.conflicts() if (c["kind"], c["key"]) == pair)
                ledger.conflict(pair, local, record)
                continue
            old, local = shadows.get(pair), current.get(pair)
            old_value = {"deleted": old["deleted"], "value": old["value"]} if old else None
            remote_value = {"deleted": record["deleted"], "value": record["value"]}
            if local is None and old and not old["deleted"] and pair[0] in {"archive", "protected_up"}:
                local = {"deleted": True, "value": {}}
            if local and local != old_value and local != remote_value:
                ledger.conflict(pair, local, record)
                continue
            apply_record(uid, record)
            ledger.remember(record)
        ledger.cursor(page["cursor"])
        if not page["has_more"]:
            return {"revision": page["revision"], "conflicts": len(ledger.conflicts()), "pending": len(ledger.pending())}
