"""Explicit user corrections and an installation-wide author deny list."""

from datetime import UTC, datetime

from ..domain.action import ActionKind
from ..domain.errors import WriteUnavailable

PARTICIPATION = {
    ActionKind.FOLLOW,
    ActionKind.SHARE,
    ActionKind.COMMENT,
    ActionKind.RESERVE,
    ActionKind.LIKE,
    ActionKind.FAVORITE,
}


def blocked(db, uid):
    return bool(
        db.connect()
        .execute("SELECT 1 FROM up_block WHERE uid=? AND active=1", (str(uid),))
        .fetchone()
    )


def put_block(box, uid, name, reason, active, *, revision=0, sync_state="local"):
    with box.db.write() as db:
        db.execute(
            "INSERT INTO up_block(uid,name,reason,active,revision,sync_state,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(uid) DO UPDATE SET name=excluded.name,reason=excluded.reason,active=excluded.active,revision=excluded.revision,sync_state=excluded.sync_state,updated_at=excluded.updated_at",
            (uid, name, reason, int(active), revision, sync_state, datetime.now(UTC).isoformat()),
        )
        if active:
            db.execute(
                "UPDATE pool_item SET state='skipped',message='UP 黑名单' WHERE state IN ('pending','failed') AND campaign_id IN (SELECT id FROM campaign WHERE up_uid=?)",
                (uid,),
            )


def guard_action(box, campaign_id, action):
    if action.kind not in PARTICIPATION:
        return
    row = box.repos.campaigns.get(campaign_id)
    if (
        row
        and box.db.connect()
        .execute("SELECT 1 FROM catalog_removed WHERE dyn_id=?", (row["dyn_id"],))
        .fetchone()
    ):
        raise WriteUnavailable("该活动已从奖品目录删除")
    uid = str((row or {}).get("up_uid") or action.payload.get("up_uid") or "")
    if blocked(box.db, uid):
        raise WriteUnavailable("该 UP 已列入黑名单，禁止参与")


def edit_prize(box, cid, reward, expected, evidence):
    with box.db.write() as db:
        row = db.execute("SELECT reward FROM campaign WHERE id=?", (cid,)).fetchone()
        if not row:
            raise LookupError("活动不存在")
        if row[0] != expected:
            raise ValueError("奖品已被更新，请重新打开后修改")
        pin = getattr(box, "pin_reward", None)
        if pin:
            pin(db, cid, reward)
        now = box.clock.now().isoformat()
        db.execute(
            "INSERT INTO prize_edit(campaign_id,before_text,after_text,evidence,at) VALUES(?,?,?,?,?)",
            (cid, row[0], reward, evidence, now),
        )
        db.execute(
            "UPDATE campaign SET reward=?,reward_status='manual',reward_locked=1,reward_evidence=?,updated_at=? WHERE id=?",
            (reward, evidence, now, cid),
        )
    return {"reward": reward, "reward_status": "manual"}


def edit_deadline(box, cid, value, expected):
    from datetime import datetime

    from ..adapters.sqlite.db import parse_stamp, stamp

    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        dt = None
    if not dt or dt.tzinfo is None:
        raise ValueError("请提供带时区的开奖时间")
    with box.db.write() as db:
        row = db.execute("SELECT deadline_at,dyn_id FROM campaign WHERE id=?", (cid,)).fetchone()
        if not row:
            raise LookupError("活动不存在")
        if parse_stamp(row[0]) != parse_stamp(expected):
            raise ValueError("时间已更新，请重新打开后修改")
        db.execute(
            "INSERT OR REPLACE INTO deadline_override VALUES(?,?,?)",
            (cid, stamp(dt), stamp(box.clock.now())),
        )
        db.execute("UPDATE campaign SET deadline_at=? WHERE id=?", (stamp(dt), cid))
        db.execute("DELETE FROM deadline_check WHERE campaign_id=?", (cid,))
        db.execute("DELETE FROM deadline_origin WHERE campaign_id=?", (cid,))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='pro_edits'").fetchone():
            import json

            old = db.execute("SELECT accepted FROM pro_edits WHERE dyn=?", (row[1],)).fetchone()
            accepted = json.loads(old[0]) if old else {}
            accepted["deadline_at"] = stamp(dt)
            db.execute(
                "INSERT OR REPLACE INTO pro_edits VALUES(?,?)", (row[1], json.dumps(accepted))
            )
    return {"deadline_at": stamp(dt)}
