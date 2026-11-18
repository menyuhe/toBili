"""Account-scoped cleanup of confirmed losing reposts and task-added follows."""

import json

from ..domain.action import ActionKind
from ..domain.result import confirmed_loss
from ..domain.tidy import TidyPolicy
from .execute_tidy import TidyBlocked


def _finished_losses(box, owner, uid):
    db = box.db.connect()
    rows = db.execute(
        """
        SELECT c.id,c.dyn_id,v.outcome,v.evidence,v.source FROM campaign c
        LEFT JOIN verdict v ON v.account_id=:owner AND v.campaign_id=c.id
        WHERE c.up_uid=:uid AND c.id IN (
            SELECT campaign_id FROM enrollment WHERE account_id=:owner UNION
            SELECT campaign_id FROM participation_ref WHERE account_id=:owner UNION
            SELECT campaign_id FROM intent WHERE account_id=:owner UNION
            SELECT campaign_id FROM verdict WHERE account_id=:owner)
    """,
        {"uid": uid, "owner": owner},
    ).fetchall()
    if not rows or any(r["outcome"] != "lost" or not confirmed_loss(dict(r)) for r in rows):
        return False
    kept = box.repos.keep.all_of(owner)
    return not any(
        k["value"] == uid if k["kind"] == "up" else any(r["dyn_id"] == k["value"] for r in rows)
        for k in kept
    )


def _pending_follows(box, owner):
    return (
        box.db.connect()
        .execute(
            """
        SELECT f.* FROM task_follow f WHERE f.account_id=? AND EXISTS (
            SELECT 1 FROM tidy_item i JOIN tidy_plan p ON p.id=i.plan_id
            WHERE p.account_id=f.account_id AND i.campaign_id=f.campaign_id
            AND i.action='drop_share' AND i.state='done')
        ORDER BY f.mtime,f.up_uid
    """,
            (owner,),
        )
        .fetchall()
    )


def clean_lost_shares(box, owner, *, apply, active):
    policy = TidyPolicy(grace_hours=0)
    planner = box.plan_tidy(policy=policy)
    preview = planner.run(owner, automatic=True, persist=False)
    if not apply or not (preview.clearable or _pending_follows(box, owner)):
        return dict(
            scanned=len(preview.lines),
            written=0,
            note=f"符合清理条件的未中奖转发 {preview.clearable} 条",
        )

    if box.check_writer:
        box.check_writer(owner)
    profile = box.repos.accounts.by_id(owner)
    if not profile or box.gateway_for(owner).whoami() != profile.uid:
        raise TidyBlocked("账号身份不一致，已暂停清理")
    decision = box.cleaning_gate(owner).check(owner)
    if not decision.open:
        raise TidyBlocked(decision.explain())

    def protected(account_id, campaign_id):
        verdict = box.repos.verdicts.detail(campaign_id, account_id)
        return (
            account_id != owner
            or not confirmed_loss(verdict)
            or box.protected(account_id, campaign_id, policy=policy)
        )

    def before_send(campaign_id, action):
        if not active() or protected(owner, campaign_id):
            raise TidyBlocked("排期或中奖结论已改变，已停止清理")
        if action.kind is ActionKind.UNFOLLOW:
            uid = action.payload["up_uid"]
            row = (
                box.db.connect()
                .execute(
                    "SELECT mtime FROM task_follow WHERE account_id=? AND up_uid=? AND campaign_id=?",
                    (owner, uid, campaign_id),
                )
                .fetchone()
            )
            if (
                not row
                or str(row["mtime"]) != action.payload.get("follow_mtime")
                or not _finished_losses(box, owner, uid)
            ):
                raise TidyBlocked("保留关注：关注来源或活动状态已改变")
        elif action.kind is not ActionKind.DELETE_DYN:
            raise TidyBlocked("清理动作不匹配")

    executor = box.execute_tidy(owner, protected=protected, action_guard=before_send)

    def execute(plan_id):
        executor.confirm(plan_id)
        try:
            return executor.run(plan_id, apply=True)
        except Exception as exc:
            box.repos.tidy.finish_plan(
                plan_id, state="aborted", note=type(exc).__name__, now=box.clock.now()
            )
            raise

    plan = planner.run(owner, automatic=True)
    removed = skipped = failed = unfollowed = retained = 0
    ok = True
    if plan.persisted:
        report = execute(plan.plan_id)
        removed, skipped, failed = report.done, report.skipped, report.failed
        ok = not failed and report.plan_state == "done"
    pending = _pending_follows(box, owner) if ok else []
    for row in pending:
        uid, cid = row["up_uid"], row["campaign_id"]
        if protected(owner, cid) or not _finished_losses(box, owner, uid):
            retained += 1
            continue
        reason = box.gateway_for(owner).unfollow_protection(uid, str(row["mtime"]))
        if reason:
            box.bus.publish(
                "tidy.unfollow",
                {"account_id": owner, "up_uid": uid, "title": "保留关注：" + reason},
            )
            retained += 1
            # A named group, special follow or later follow is a user decision.
            # Relinquish ownership permanently. Inconclusive reads stay pending.
            if reason.startswith(("位于分组", "已加入关注分组", "特别关注", "当前关注与")):
                with box.db.write() as db:
                    db.execute(
                        "DELETE FROM task_follow WHERE account_id=? AND up_uid=? AND mtime=?",
                        (owner, uid, row["mtime"]),
                    )
            continue
        if not box.quota(owner).check(owner).allowed:
            retained += 1
            continue
        tidy = box.repos.tidy
        follow_plan = tidy.create_plan(owner, now=box.clock.now())
        tidy.add_item(
            follow_plan,
            campaign_id=cid,
            action="unfollow",
            snapshot=json.dumps({"follow_mtime": row["mtime"]}),
        )
        tidy.mark_previewed(follow_plan, now=box.clock.now())
        result = execute(follow_plan)
        if result.done:
            unfollowed += result.done
            with box.db.write() as db:
                db.execute(
                    "DELETE FROM task_follow WHERE account_id=? AND up_uid=? AND mtime=?",
                    (owner, uid, row["mtime"]),
                )
        else:
            retained += 1
        if result.failed or result.plan_state != "done":
            ok = False
            break
    return dict(
        scanned=len(preview.lines),
        written=removed + unfollowed,
        ok=ok,
        note=f"未中奖转发清理：已完成 {removed} 条，跳过 {skipped} 条，失败 {failed} 条；取关完成 {unfollowed}，保留关注 {retained}",
        plan_id=plan.plan_id,
    )
