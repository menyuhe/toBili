"""Account-local scheduling facts. Reading them does not contact the platform."""

from datetime import datetime

GAP = 300


def pacing(box, owner):
    row = (
        box.db.connect()
        .execute(
            """
        SELECT action, COALESCE(finished_at,created_at) AS at FROM intent
        WHERE account_id=? AND action!='unfollow' ORDER BY at DESC LIMIT 1
    """,
            (owner,),
        )
        .fetchone()
    )
    if row is None:
        return 0, ""
    at = datetime.fromisoformat(row["at"]).timestamp() + GAP
    return at, "刚尝试删除" if row["action"] == "delete_dyn" else "刚尝试参与"


def status(box, owner):
    settings = box.repos.settings
    paused = settings.get(owner, "tidy.paused", "0") == "1"
    reason = settings.get(owner, "tidy.pause_reason", "") if paused else ""
    if reason == "执行清理时遇到风控":
        rows = (
            box.db.connect()
            .execute(
                """
            SELECT action,message,finished_at FROM intent WHERE account_id=? AND
            action IN ('delete_dyn','unfollow') AND error_class='risk' ORDER BY id DESC LIMIT 2
        """,
                (owner,),
            )
            .fetchall()
        )
        if len(rows) == 1 and rows[0]["finished_at"]:
            row = rows[0]
            stamp = datetime.fromisoformat(row["finished_at"]).astimezone().strftime("%m/%d %H:%M")
            action = "删除转发" if row["action"] == "delete_dyn" else "取消关注"
            reason = f"{stamp} {action} · {row['message']}"
    floor, why = pacing(box, owner)
    return dict(
        paused=paused,
        reason=reason,
        earliest=floor,
        waiting=why if floor > box.clock.now().timestamp() else "",
        backlog=settings.get(owner, "tidy.backlog", ""),
    )
