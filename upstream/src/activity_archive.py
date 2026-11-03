"""Non-destructive local archiving, separate from synchronized activity payloads."""
import json
import time
from sqlmodel import select
from src.db.models import ActivityArchiveRow, ActivityRow, AccountStateRow
from src.db.session import session_scope
from src.db.activity_codec import row_to_activity_dict
from src.lottery_time import lottery_time_unix


def _read(session, uid, key, default):
    row = session.get(AccountStateRow, (uid, key))
    return json.loads(row.value) if row else default


def _write(session, uid, key, value):
    row = session.get(AccountStateRow, (uid, key))
    if row is None:
        row = AccountStateRow(uid=uid, key=key)
        session.add(row)
    row.value = json.dumps(value, ensure_ascii=False)


def archived_activity_ids() -> set[str]:
    from src.account_context import current_account
    from src.accounts import read_state
    context = current_account.get()
    if context is not None:
        return set(read_state(context.uid, "archives", []))
    with session_scope() as session:
        return set(session.exec(select(ActivityArchiveRow.dynamic_id)).all())


def archive_activity_ids(ids: set[str], *, reason: str = "manual", now: int | None = None) -> int:
    """Manual removal is explicit; automatic rules must respect the draw deadline."""
    from src.account_context import current_account
    from src.accounts import lock
    if reason not in {"manual", "ended", "publication_age"}:
        raise ValueError("未知归档原因")
    context = current_account.get()
    uid = context.uid if context is not None else "__legacy__"
    timestamp = int(time.time()) if now is None else now
    with lock, session_scope() as session:
        ids = set(ids)
        if reason != "manual":
            eligible = set()
            for did in ids:
                row = session.get(ActivityRow, did)
                if row is None:
                    continue
                deadline = lottery_time_unix(row_to_activity_dict(row))
                if deadline and deadline > timestamp:
                    continue
                # Publication age alone is not evidence that a draw has ended.
                if reason == "publication_age" and not deadline:
                    continue
                eligible.add(did)
            ids = eligible
        existing = (set(_read(session, uid, "archives", [])) if context is not None
                    else set(session.exec(select(ActivityArchiveRow.dynamic_id)).all()))
        new_ids = ids - existing
        reasons = _read(session, uid, "archive_reasons", {})
        # Explicit removal also protects an already archived item from repairs.
        reasons.update({did: reason for did in (ids if reason == "manual" else new_ids)})
        if ids:
            _write(session, uid, "archive_reasons", reasons)
        if context is not None:
            times = _read(session, uid, "archive_times", {})
            times.update({did: timestamp for did in new_ids})
            _write(session, uid, "archive_times", times)
            _write(session, uid, "archives", sorted(existing | ids))
        else:
            for dynamic_id in new_ids:
                session.add(ActivityArchiveRow(dynamic_id=dynamic_id, archived_at=timestamp))
        return len(new_ids)


def restore_archived_activity(dynamic_id: str) -> bool:
    from src.account_context import current_account
    from src.accounts import lock
    context = current_account.get()
    uid = context.uid if context is not None else "__legacy__"
    with lock, session_scope() as session:
        if context is not None:
            existing = set(_read(session, uid, "archives", []))
            found = dynamic_id in existing
            if not found:
                return False
            _write(session, uid, "archives", sorted(existing - {dynamic_id}))
            times = _read(session, uid, "archive_times", {})
            times.pop(dynamic_id, None)
            _write(session, uid, "archive_times", times)
        else:
            row = session.get(ActivityArchiveRow, dynamic_id)
            if row is None:
                return False
            session.delete(row)
        reasons = _read(session, uid, "archive_reasons", {})
        reasons.pop(dynamic_id, None)
        _write(session, uid, "archive_reasons", reasons)
        return True


def restore_publication_archives(uid: str, evidence: dict, *, now: int | None = None,
                                 dry_run: bool = True) -> list[str]:
    """Restore only unchanged entries proven by an original publication-cleanup report.

    Never infer provenance from the deadline, age, or current display status alone.
    This is an explicit repair, not an automatic startup migration.
    """
    from src.accounts import lock
    stamp = int(evidence["time"])
    if stamp <= 0 or int(evidence["cutoff"]) >= stamp:
        raise ValueError("历史清理报告无效")
    known_ids = {str(did) for did in evidence["archived_ids"]}
    timestamp = int(time.time()) if now is None else now
    with lock, session_scope() as session:
        ids = set(_read(session, uid, "archives", []))
        times = _read(session, uid, "archive_times", {})
        reasons = _read(session, uid, "archive_reasons", {})
        restored = []
        for did in sorted(ids & known_ids):
            if times.get(did) != stamp or reasons.get(did) not in (None, "publication_age"):
                continue
            row = session.get(ActivityRow, did)
            if row is None or row.draw_status != "active":
                continue
            deadline = lottery_time_unix(row_to_activity_dict(row))
            if deadline and deadline > timestamp:
                restored.append(did)
        if restored and not dry_run:
            for did in restored:
                ids.discard(did)
                times.pop(did, None)
                reasons.pop(did, None)
            _write(session, uid, "archives", sorted(ids))
            _write(session, uid, "archive_times", times)
            _write(session, uid, "archive_reasons", reasons)
            history = _read(session, uid, "archive_repairs", [])
            history.append({"reason": "future_draw_after_publication_cleanup", "at": timestamp,
                            "evidence_time": stamp, "restored_ids": restored})
            _write(session, uid, "archive_repairs", history)
        return restored
