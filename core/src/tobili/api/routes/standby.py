"""Local-only overview of every account. Never switches accounts or polls Bilibili."""

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from ...container import Container
from ...runtime.taskbook import TASKS
from ..deps import get_container

router = APIRouter(prefix="/api/standby", tags=["schedule"])


@router.get("")
def view(box: Container = Depends(get_container)):
    items = []
    for account in box.accounts():
        owner = account["id"]
        plan = box.tasks.view(owner)
        current = plan["active"] or dict(getattr(box, "source_progress", {}).get(owner, {}))
        if current:
            current = {
                **current,
                "label": TASKS.get(current.get("key"), (current.get("label", "处理中"),))[0],
            }
        legacy = box.schedule(owner).status()
        upcoming = plan["next"]
        if legacy.running and legacy.next_at:
            old = {"label": "定时参与", "key": "participate", "at": legacy.next_at.timestamp()}
            if not upcoming or old["at"] < upcoming["at"]:
                upcoming = old
        # Unlike the history view, do not mix account-less global events into each card.
        row = (
            box.db.connect()
            .execute(
                "SELECT at,payload FROM run_event WHERE json_extract(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,'$.account_id')=? AND topic IN ('schedule.round','scan','participation.issue','settle','enroll') ORDER BY seq DESC LIMIT 1",
                (owner,),
            )
            .fetchone()
        )
        recent = {"at": row[0], "note": json.loads(row[1]).get("title", "")} if row else None
        pending = (
            box.db.connect()
            .execute(
                "SELECT count(*) FROM pool_item WHERE account_id=? AND state NOT IN ('success','skipped')",
                (owner,),
            )
            .fetchone()[0]
        )
        enabled = (
            [TASKS[t["key"]][0] for t in plan["tasks"] if t["enabled"]] if plan["running"] else []
        )
        items.append(
            {
                **account,
                "active": current if account["ready"] else {},
                "next": upcoming if account["ready"] else None,
                "running": bool(
                    account["ready"]
                    and (plan["running"] or plan["sources_running"] or legacy.running)
                ),
                "enabled": enabled,
                "pending": pending,
                "hourly_used": plan["hourly_used"],
                "recent": recent,
            }
        )
    return {"items": items, "at": datetime.now(UTC).isoformat()}
