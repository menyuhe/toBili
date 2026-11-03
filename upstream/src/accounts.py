"""Account registry and durable account-local state, sharing the application DB."""
import json
import re
import shutil
import threading
import time
from sqlmodel import select
from src.account_context import current_account, account_scope
from src.db.models import AccountStateRow
from src.db.session import session_scope

lock = threading.RLock()


def read_state(uid, key, default=None):
    with session_scope() as session:
        row = session.get(AccountStateRow, (str(uid), key))
        return json.loads(row.value) if row else default


def write_state(uid, key, value):
    with session_scope() as session:
        row = session.get(AccountStateRow, (str(uid), key))
        if row is None:
            row = AccountStateRow(uid=str(uid), key=key)
            session.add(row)
        row.value = json.dumps(value, ensure_ascii=False)


def cookie_path(uid):
    from src.app_paths import config_dir
    if not str(uid).isdigit() or int(uid) <= 0:
        raise ValueError("账号 UID 无效")
    return config_dir() / "accounts" / str(uid) / "cookies.txt"


def register_cookie(content, expected_uid=""):
    from src.secure_files import write_text_secret
    from src.config_validate import validate_cookie_string
    content = validate_cookie_string(content)
    match = re.search(r"(?:^|;\s*)DedeUserID=(\d+)", content)
    if not match:
        raise ValueError("登录凭证缺少账号 UID")
    uid = match[1]
    if expected_uid and expected_uid != uid:
        raise ValueError("扫码账号与待重新登录账号不一致，请使用指定账号扫码")
    with lock:
        write_text_secret(cookie_path(uid), content)
        meta = read_state(uid, "account", {})
        write_state(uid, "account", {**meta, "uid": uid, "enabled": True,
                                     "expired": False, "created_at": meta.get("created_at", int(time.time()))})
    if not read_state("__global__", "editorial_uid"):
        write_state("__global__", "editorial_uid", uid)
    return uid


def list_accounts():
    with session_scope() as session:
        rows = session.exec(select(AccountStateRow).where(AccountStateRow.key == "account")).all()
        result = [json.loads(row.value) for row in rows]
    for item in result:
        item.update(read_state(item["uid"], "profile", {}))
        item["logged_in"] = bool(item.get("enabled") and not item.get("expired") and cookie_path(item["uid"]).exists())
    return sorted(result, key=lambda x: (x.get("created_at", 0), int(x["uid"])))


def default_uid():
    return next((a["uid"] for a in list_accounts() if a["logged_in"]), "")


def logout(uid):
    with lock:
        cookie_path(uid).unlink(missing_ok=True)
        meta = read_state(uid, "account", {})
        write_state(uid, "account", {**meta, "uid": uid, "enabled": False, "auto_enabled": False})


def migrate_legacy():
    """Called after schema creation; never assign unknown historical owners."""
    from src.app_paths import config_dir
    from src.db.models import ActivityRow, ActivityArchiveRow, UserSettingsRow, AccountProfileCacheRow
    from src.db.activity_codec import row_to_activity_dict
    with lock:
        if read_state("__global__", "accounts_migrated", False):
            return
        legacy = config_dir() / "cookies.txt"
        content = legacy.read_text(encoding="utf-8").strip() if legacy.exists() else ""
        match = re.search(r"(?:^|;\s*)DedeUserID=(\d+)", content)
        if match:
            backup = legacy.with_name("cookies.pre-multi-account.txt")
            if not backup.exists():
                from src.secure_files import write_text_secret
                write_text_secret(backup, content)
            uid = register_cookie(content)
            with session_scope() as session:
                activities = [row_to_activity_dict(r) for r in session.exec(select(ActivityRow)).all()]
                archive_times = {r.dynamic_id: r.archived_at for r in session.exec(select(ActivityArchiveRow)).all()}
                archives = list(archive_times)
                cfg = session.get(UserSettingsRow, "__global__")
                if cfg and session.get(UserSettingsRow, uid) is None:
                    session.add(UserSettingsRow(**{**cfg.model_dump(), "uid": uid}))
                profile = session.get(AccountProfileCacheRow, 1)
                profile_data = json.loads(profile.raw_json or "{}") if profile and str(profile.mid) == uid else {}
            for item in activities:
                save_activity_state(uid, item)
            write_state(uid, "archives", archives)
            write_state(uid, "archive_times", archive_times)
            legacy_reasons = read_state("__legacy__", "archive_reasons", {})
            if legacy_reasons:
                write_state(uid, "archive_reasons", legacy_reasons)
            if profile_data:
                write_state(uid, "profile", profile_data)
        write_state("__global__", "accounts_migrated", True)


ACTIVITY_FIELDS = ("platform_participated", "reserve_reserved", "user_status_source", "skipped", "skip_reason")


def save_activity_state(uid, item, session=None):
    did = str(item.get("dynamic_id") or "")
    fields = {k: item[k] for k in ACTIVITY_FIELDS if k in item}
    if item.get("activity_status") == "已参加":
        fields["platform_participated"] = True
    if did and fields:
        if session is not None:
            row = session.get(AccountStateRow, (uid, "activity:" + did))
            old = json.loads(row.value) if row else {}
            if row is None:
                row = AccountStateRow(uid=uid, key="activity:" + did)
                session.add(row)
            row.value = json.dumps({**old, **fields}, ensure_ascii=False)
        else:
            old = read_state(uid, "activity:" + did, {})
            write_state(uid, "activity:" + did, {**old, **fields})


def overlay_activities(items):
    context = current_account.get()
    if context is None:
        return items
    with session_scope() as session:
        rows = session.exec(select(AccountStateRow).where(AccountStateRow.uid == context.uid,
                                                          AccountStateRow.key.startswith("activity:"))).all()
        states = {r.key[9:]: json.loads(r.value) for r in rows}
    for item in items:
        for key in ACTIVITY_FIELDS:
            item.pop(key, None)
        if item.get("activity_status") == "已参加":
            item["activity_status"] = "未参加"
        item.update(states.get(str(item.get("dynamic_id")), {}))
        if item.get("platform_participated") and item.get("draw_status") != "ended":
            item["activity_status"] = "已参加"
    return items
