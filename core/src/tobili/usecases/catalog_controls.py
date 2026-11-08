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
