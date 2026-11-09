"""Apply public metadata without changing account state or local manual decisions."""

import json
from datetime import UTC, datetime, timedelta, timezone

from ..publicwire import UNVERIFIED_REWARD, PublicPrize

URL = "https://tobili.4wdo.cn/public/v1/catalog"
CURSOR = "public_catalog.revision"


def apply_page(box, page, cursor):
    rows, following, more = page["items"], page["next"], page["more"]
    if (
        not isinstance(rows, list)
        or len(rows) > 100
        or type(following) is not int
        or type(more) is not bool
    ):
        raise ValueError("Invalid public page")
    if following < cursor or (rows and following <= cursor) or (more and not rows):
        raise ValueError("Invalid public cursor")
    cards = [PublicPrize.model_validate(row) for row in rows]
    added = updated = 0
    at = box.clock.now().isoformat()
    with box.db.write() as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS public_import_state(dyn TEXT PRIMARY KEY,payload TEXT NOT NULL)"
        )
        manual = {
            r[0]
            for r in con.execute(
                "SELECT c.dyn_id FROM deadline_override d JOIN campaign c ON c.id=d.campaign_id"
            )
        }
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='pro_edits'").fetchone():
            manual |= {
                r[0]
                for r in con.execute("SELECT dyn,accepted FROM pro_edits")
                if "deadline_at" in json.loads(r[1])
            }
        for card in cards:
            if con.execute(
                "SELECT 1 FROM catalog_removed WHERE dyn_id=?", (card.dyn_id,)
            ).fetchone():
                continue
            if con.execute(
                "SELECT 1 FROM up_block WHERE uid=? AND active=1", (card.up_uid,)
            ).fetchone():
                continue
            row = con.execute("SELECT * FROM campaign WHERE dyn_id=?", (card.dyn_id,)).fetchone()
            old = con.execute(
                "SELECT payload FROM public_import_state WHERE dyn=?", (card.dyn_id,)
            ).fetchone()
            previous = json.loads(old[0]) if old else {}
            value = card.model_dump(mode="json")
            deadline = (
                card.deadline_at.astimezone(UTC).isoformat(timespec="seconds")
                if card.deadline_at
                else None
            )
            if not row:
                if card.deadline_at and card.deadline_at <= box.clock.now():
                    continue
                if (
                    card.deadline_date
                    and card.deadline_date
                    < box.clock.now().astimezone(timezone(timedelta(hours=8))).date()
                ):
                    continue
                con.execute(
                    "INSERT INTO campaign(dyn_id,up_uid,up_name,title,reward,kind,deadline_at,published_at,reward_status,source_ref,updated_at,first_seen_at) VALUES(?,?,?,?,?,?,?,?,'verified','公开活动目录',?,?)",
                    (
                        card.dyn_id,
                        card.up_uid,
                        card.up_name,
                        card.reward[:500],
                        card.reward,
                        card.kind,
                        deadline,
                        value["published_at"],
                        at,
                        at,
                    ),
                )
                added += 1
                row = con.execute(
                    "SELECT * FROM campaign WHERE dyn_id=?", (card.dyn_id,)
                ).fetchone()
            else:
                changes = {}
                if not row["reward_locked"] and row["reward_status"] != "manual":
                    changes.update(reward=card.reward, reward_status="verified")
                # Existing local dates have precedence. Only replace a date this feed supplied.
                before = previous.get("deadline_at")
                local_date = (
                    datetime.fromisoformat(row["deadline_at"]) if row["deadline_at"] else None
                )
                old_date = datetime.fromisoformat(before) if before else None
                if card.dyn_id not in manual and (
                    not local_date or (old_date and local_date == old_date)
                ):
                    changes["deadline_at"] = deadline
                if not row["up_name"]:
                    changes["up_name"] = card.up_name
                if not row["up_uid"]:
                    changes["up_uid"] = card.up_uid
                if not row["published_at"]:
                    changes["published_at"] = value["published_at"]
                changes = {k: v for k, v in changes.items() if row[k] != v}
                if changes:
                    con.execute(
                        "UPDATE campaign SET "
                        + ",".join(k + "=?" for k in changes)
                        + " WHERE id=?",
                        (*changes.values(), row["id"]),
                    )
                    updated += 1
            if (
                card.reward == UNVERIFIED_REWARD
                and not row["reward_locked"]
                and row["reward_status"] != "manual"
            ):
                con.execute(
                    "UPDATE campaign SET reward='',reward_status='unknown' WHERE id=?", (row["id"],)
                )
            row = con.execute("SELECT * FROM campaign WHERE id=?", (row["id"],)).fetchone()
            if card.deadline_date and not row["deadline_at"] and card.dyn_id not in manual:
                # Date-only information stays separate; it must not become a made-up minute.
                con.execute(
                    "INSERT OR IGNORE INTO deadline_check(campaign_id,body_text,published_at,before_at,candidate_at,precision,evidence,state,checked_at) VALUES(?,?,?,NULL,?,'day','公开活动目录','date_only',?)",
                    (
                        row["id"],
                        row["body_text"],
                        row["published_at"],
                        str(card.deadline_date) + "T00:00:00+08:00",
                        at,
                    ),
                )
                prior_day = previous.get("deadline_date")
                if prior_day:
                    con.execute(
                        "UPDATE deadline_check SET candidate_at=?,checked_at=? WHERE campaign_id=? AND evidence='公开活动目录' AND state='date_only' AND candidate_at=?",
                        (
                            str(card.deadline_date) + "T00:00:00+08:00",
                            at,
                            row["id"],
                            prior_day + "T00:00:00+08:00",
                        ),
                    )
            con.execute(
                "INSERT OR REPLACE INTO public_import_state VALUES(?,?)",
                (card.dyn_id, json.dumps(value)),
            )
        con.execute(
            "INSERT OR REPLACE INTO setting(account_id,key,value) VALUES(0,?,?)",
            (CURSOR, str(following)),
        )
    return dict(read=len(rows), added=added, updated=updated, more=more)


def read_updates(box, client, restart=False):
    cursor = 0 if restart else int(box.repos.settings.get(0, CURSOR, "0"))
    totals = dict(read=0, added=0, updated=0, more=False)
    for _ in range(3):
        with client.stream("GET", URL, params=dict(after=cursor, limit=100)) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 2_000_000:
                    raise ValueError("Public response too large")
        page = json.loads(data)
        result = apply_page(box, page, cursor)
        for k in ("read", "added", "updated"):
            totals[k] += result[k]
        totals["more"] = result["more"]
        cursor = page["next"]
        if not result["more"]:
            break
    return totals
