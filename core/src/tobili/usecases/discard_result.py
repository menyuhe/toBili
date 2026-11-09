"""Explicit removal of unusable result records; delete only the owner's exact repost."""

from ..domain.action import Action, ActionKind
from ..domain.tidy import may_delete_share
from ..runtime.journal import Outcome
from ..runtime.redact import safe_exception


def eligible(error):
    return error == "缺少 UP 信息"


def discard(box, owner, cid):
    db = box.db.connect()
    row = db.execute(
        """SELECT c.dyn_id,p.error,r.share_id FROM enrollment e
        JOIN campaign c ON c.id=e.campaign_id
        LEFT JOIN result_probe p ON p.campaign_id=e.campaign_id AND p.account_id=e.account_id
        LEFT JOIN participation_ref r ON r.campaign_id=e.campaign_id AND r.account_id=e.account_id
        WHERE e.account_id=? AND c.id=?""",
        (owner, cid),
    ).fetchone()
    if not row or not eligible(row["error"]):
        raise ValueError("只移除缺失必要资料的记录；临时接口失败不属于失效活动")
    if db.execute(
        "SELECT 1 FROM verdict WHERE account_id=? AND campaign_id=? AND outcome='won'", (owner, cid)
    ).fetchone():
        raise ValueError("该条已有中奖结论，不能按失效资料清理")
    old = db.execute(
        "SELECT * FROM result_dismissal WHERE account_id=? AND campaign_id=?", (owner, cid)
    ).fetchone()
    if old and old["state"] == "done":
        return dict(old)
    box.check_writer(owner)
    at = box.clock.now().isoformat()
    with box.db.write() as con:
        con.execute(
            "INSERT OR IGNORE INTO result_dismissal(account_id,campaign_id,reason,at) VALUES(?,?,?,?)",
            (owner, cid, row["error"], at),
        )
        con.execute(
            "INSERT OR IGNORE INTO catalog_removed(dyn_id,reason,removed_at) VALUES(?,'核验资料缺失',?)",
            (row["dyn_id"], at),
        )
        con.execute("DELETE FROM pool_item WHERE campaign_id=?", (cid,))
    gateway = box.gateway_for(owner)
    state, note, target = "pending", "", str(row["share_id"] or "")
    try:
        if target:
            from ..domain.probe import ProbeState

            if gateway.did(ActionKind.DELETE_DYN, dyn_id=target, payload={}) is ProbeState.PRESENT:
                state, note = "done", "对应转发已不存在"
                ref = None
            else:
                ref = gateway.share_ref(target)
        else:
            ref = gateway.find_share(row["dyn_id"], pages=4)
        if state != "done":
            valid = may_delete_share(
                ref, me=box.repos.accounts.by_id(owner).uid, campaign_dyn_id=row["dyn_id"]
            )
            if ref is None:
                state, note = "not_found", "资料已移除；本次未找到对应转发，未执行删除"
            elif not valid.allowed:
                state, note = "blocked", valid.reason
            else:
                target = ref.dyn_id
                # Persist the target before IO so an ambiguous delete can be checked later.
                with box.db.write() as con:
                    con.execute(
                        "UPDATE result_dismissal SET share_id=?,state='running' WHERE account_id=? AND campaign_id=?",
                        (target, owner, cid),
                    )
                step = box.journal(gateway, owner).attempt(
                    account_id=owner,
                    campaign_id=cid,
                    action=Action(ActionKind.DELETE_DYN, target, {"orig_dyn_id": row["dyn_id"]}),
                )
                state = (
                    "done"
                    if step.outcome in (Outcome.SETTLED, Outcome.SKIPPED_PRESENT)
                    else "pending"
                )
                note = "资料及本人转发已删除" if state == "done" else step.message
    except Exception as exc:
        state, note = "pending", safe_exception(exc)
    with box.db.write() as con:
        con.execute(
            "UPDATE result_dismissal SET share_id=?,state=?,note=?,at=? WHERE account_id=? AND campaign_id=?",
            (target, state, note, at, owner, cid),
        )
        if state == "done" and target:
            con.execute(
                "INSERT OR REPLACE INTO prior_cleanup(account_id,external_id,dyn_id,action,target,state,note,at) VALUES(?,?,?,'drop_share',?,'done',?,?)",
                (owner, f"result-discard-{cid}", row["dyn_id"], target, note, at),
            )
    result = dict(account_id=owner, campaign_id=cid, share_id=target, state=state, note=note)
    box.bus.publish(
        "result.discard",
        dict(result, title=f"{row['dyn_id']} · {note}", ok=state in ("done", "not_found")),
    )
    return result
