"""Evidence-only review of stored dates. Never replaces an existing or human-pinned date."""

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from ..adapters.sqlite.db import parse_stamp, stamp
from ..domain.extract import _ABS_TIME, _REL_TIME, _context_window, deadline_from

CN = timezone(timedelta(hours=8))


def infer(body, published):
    if re.search(r"抽奖黑幕|抽给自己|避雷名单|抽奖避雷", body):
        return None
    candidates = []
    for piece in re.split(r"[。！？!？;；\n]", body):
        if re.search(r"另.{0,12}(?:直播|今晚)", piece):
            continue
        if not re.search(r"截止|截至|开奖|抽取|抽出|揭晓", piece) or re.search(
            r"预计|大概|左右|待定|另行|择日|上期|上次|往期|已经开奖|已开奖", piece
        ):
            continue
        parse_text = re.sub(r"抽取|抽出|揭晓", "开奖", piece)
        parse_text = re.sub(
            r"(\d{1,2}(?:[月./-]\d{1,2})?[日号]?)的(?=\s*(?:凌晨|早上|上午|中午|下午|晚上|晚间)?\d{1,2}[:：点时])",
            r"\1",
            parse_text,
        )
        excerpt = _context_window(parse_text)
        # Multiple dates in one sentence need human interpretation (rescheduling,
        # winner claims, retrospective summaries). Do not pick one arbitrarily.
        if len(list(_ABS_TIME.finditer(piece))) + len(list(_REL_TIME.finditer(piece))) > 1:
            continue
        rel = _REL_TIME.search(excerpt)
        absolute = _ABS_TIME.search(excerpt)
        if not excerpt or (not published and (rel or not absolute or not absolute.group(1))):
            continue
        reference = published.astimezone(CN) if published else datetime(2000, 1, 1, tzinfo=CN)
        value = deadline_from(parse_text, now=reference, allow_relative=published is not None)
        if value:
            precision = (
                "minute"
                if (rel and rel.group("hour")) or (absolute and absolute.group(4))
                else "day"
            )
            # "17:00 前抽取" gives an upper bound, not the actual draw minute.
            if precision == "minute" and re.search(re.escape(excerpt) + r"\s*[前后]", parse_text):
                precision = "day"
            priority = 0 if re.search(r"截止|截至", piece) else 1
            candidates.append((priority, value, precision, piece.strip()))
    if not candidates:
        return None
    priority = min(r[0] for r in candidates)
    candidates = [r for r in candidates if r[0] == priority]
    if len({(r[1], r[2]) for r in candidates}) != 1:
        return None
    _, value, precision, evidence = candidates[0]
    return value, precision, evidence


def review_existing(db, now, excerpts=None):
    counts = Counter()
    changes = []
    with db.write() as con:
        manual = set()
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='pro_edits'").fetchone():
            for row in con.execute("SELECT dyn,accepted FROM pro_edits"):
                if "deadline_at" in json.loads(row["accepted"]):
                    manual.add(row["dyn"])
        for row in con.execute(
            "SELECT id,dyn_id,body_text,published_at,deadline_at FROM campaign"
        ).fetchall():
            state = "unresolved"
            candidate = None
            precision = ""
            evidence = ""
            if row["dyn_id"] in manual:
                state = "manual"
            elif not row["body_text"]:
                state = "no_body"
            else:
                fact = infer(row["body_text"], parse_stamp(row["published_at"]))
                excerpt = (excerpts or {}).get(row["dyn_id"], "")
                if (
                    not fact
                    and excerpt
                    and excerpt in row["body_text"]
                    and not re.search(r"抽奖黑幕|抽给自己|避雷名单|抽奖避雷", row["body_text"])
                ):
                    fact = infer(excerpt, parse_stamp(row["published_at"]))
                if fact:
                    value, precision, evidence = fact
                    candidate = stamp(value)
                    if row["deadline_at"]:
                        old = parse_stamp(row["deadline_at"])
                        agrees = (
                            old == value
                            if precision == "minute"
                            else old.astimezone(CN).date() == value.date()
                        )
                        state = "unchanged" if agrees else "conflict"
                    elif precision == "day":
                        state = "date_only"
                    else:
                        con.execute(
                            "UPDATE campaign SET deadline_at=? WHERE id=? AND deadline_at IS NULL",
                            (candidate, row["id"]),
                        )
                        state = "filled"
                        changes.append(
                            dict(
                                id=row["id"],
                                dyn=row["dyn_id"],
                                before=None,
                                after=candidate,
                                evidence=evidence,
                            )
                        )
            con.execute(
                "INSERT OR REPLACE INTO deadline_check VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    row["id"],
                    row["body_text"],
                    row["published_at"],
                    row["deadline_at"],
                    candidate,
                    precision,
                    evidence,
                    state,
                    stamp(now),
                ),
            )
            counts[state] += 1
    return dict(total=sum(counts.values()), counts=dict(counts), changes=changes)
