"""Persistent account result ledger and read-only discovery of owned reposts."""

import json


def ledger(box, owner, *, query="", outcome="", link="", offset=0, limit=50):
    db = box.db.connect()
    rows = [
        dict(r)
        for r in db.execute(
            """
        SELECT c.id campaign_id,c.dyn_id,c.title,c.reward,c.up_name,c.deadline_at,e.stage,
        COALESCE(r.share_id,'') share_id,COALESCE(r.link_state,'unscanned') link_state,
        COALESCE(r.choices,'{}') choices,r.followed_before,r.follow_added,
        COALESCE(v.evidence,'') evidence,COALESCE(v.source,'') result_source,
        COALESCE(v.decided_at,'') checked_at,
        CASE WHEN v.outcome IN ('won','lost','void') THEN v.outcome
             WHEN c.deadline_at > ? THEN 'pending' ELSE COALESCE(r.result_state,'unknown') END outcome,
        EXISTS(SELECT 1 FROM tidy_keep k WHERE k.account_id=e.account_id AND
               ((k.kind='campaign' AND k.value=c.dyn_id) OR (k.kind='up' AND k.value=c.up_uid))) protected,
        EXISTS(SELECT 1 FROM tidy_item t JOIN tidy_plan p ON p.id=t.plan_id
               WHERE p.account_id=e.account_id AND t.campaign_id=c.id AND t.action='drop_share' AND t.state='done') cleaned
        FROM enrollment e JOIN campaign c ON c.id=e.campaign_id
        LEFT JOIN participation_ref r ON r.account_id=e.account_id AND r.campaign_id=c.id
        LEFT JOIN verdict v ON v.account_id=e.account_id AND v.campaign_id=c.id
        WHERE e.account_id=? AND (e.stage='settled' OR r.campaign_id IS NOT NULL)
        ORDER BY COALESCE(v.decided_at,e.updated_at) DESC,c.id DESC
        """,
            (box.clock.now().isoformat(), owner),
        )
    ]
    summary = {
        key: sum(r["outcome"] == key for r in rows)
        for key in ("pending", "unknown", "won", "lost", "void")
    }
    links = {
        key: sum(r["link_state"] == key for r in rows)
        for key in ("verified", "candidate", "conflict", "missing", "unscanned")
    }
    counts = dict(
        db.execute(
            """SELECT t.action,count(*) FROM tidy_item t JOIN tidy_plan p ON p.id=t.plan_id
        WHERE p.account_id=? AND t.state='done' GROUP BY t.action""",
            (owner,),
        ).fetchall()
    )
    selected = [
        r
        for r in rows
        if (not outcome or r["outcome"] == outcome)
        and (not link or r["link_state"] == link)
        and (
            not query
            or query.lower()
            in ((r["reward"] or "") + " " + r["up_name"] + " " + r["dyn_id"]).lower()
        )
    ]
    for row in selected:
        row["choices"] = json.loads(row["choices"])
    scan = json.loads(box.repos.settings.get(owner, "results.scan", "{}"))
    return dict(
        items=selected[offset : offset + limit],
        total=len(selected),
        all_total=len(rows),
        outcomes=summary,
        links=links,
        cleanup=counts,
        scan=scan,
    )


def discover(box, owner, *, resume=False, pages=3):
    saved = json.loads(box.repos.settings.get(owner, "results.scan", "{}"))
    if resume and (not saved.get("cursor") or saved.get("finished")):
        raise ValueError("没有可继续的扫描")
    progress = saved if resume else dict(cursor="", pages=0, scanned=0, finished=False)
    gateway = box.gateway_for(owner)
    uid = box.repos.accounts.by_id(owner).uid
    visited = set()
    for _ in range(pages):
        cursor = progress["cursor"]
        if cursor in visited:
            raise ValueError("分页重复，扫描已停止")
        visited.add(cursor)
        page = gateway.own_shares(cursor=cursor)
        if page["uid"] != uid:
            raise ValueError("账号归属不一致")
        next_cursor = page["cursor"]
        if next_cursor and next_cursor in visited:
            raise ValueError("分页重复，扫描已停止")
        at = box.clock.now().isoformat()
        with box.db.write() as db:
            rows = db.execute(
                """SELECT c.id,c.dyn_id,c.up_uid,r.* FROM enrollment e
                JOIN campaign c ON c.id=e.campaign_id
                LEFT JOIN participation_ref r ON r.account_id=e.account_id AND r.campaign_id=c.id
                WHERE e.account_id=? AND (e.stage='settled' OR r.campaign_id IS NOT NULL)""",
                (owner,),
            ).fetchall()
            for r in rows:
                choices = json.loads(r["choices"] or "{}")
                for item in page["items"]:
                    if item["original"] == r["dyn_id"]:
                        choices[item["id"]] = item["up_uid"]
                state = r["link_state"] or "unscanned"
                if state != "verified":
                    state = (
                        "conflict"
                        if len(choices) > 1
                        or any(up and r["up_uid"] and up != r["up_uid"] for up in choices.values())
                        else "candidate"
                        if choices
                        else "missing"
                    )
                db.execute(
                    """INSERT INTO participation_ref(account_id,campaign_id,link_state,choices,checked_at)
                    VALUES(?,?,?,?,?) ON CONFLICT(account_id,campaign_id) DO UPDATE SET
                    link_state=excluded.link_state,choices=excluded.choices,checked_at=excluded.checked_at""",
                    (owner, r["id"], state, json.dumps(choices), at),
                )
            progress.update(
                cursor=next_cursor,
                pages=progress["pages"] + 1,
                scanned=progress["scanned"] + page["scanned"],
                finished=not next_cursor,
                at=at,
            )
            db.execute(
                """INSERT INTO setting VALUES(?,?,?) ON CONFLICT(account_id,key)
                DO UPDATE SET value=excluded.value""",
                (owner, "results.scan", json.dumps(progress)),
            )
        if not next_cursor:
            break
    return progress
