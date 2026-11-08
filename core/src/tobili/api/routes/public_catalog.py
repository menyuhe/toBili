"""Read the public activity directory without credentials or private plugins."""

import json
import threading
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...catalogwire import Listing
from ...container import Container
from ...usecases.public_updates import URL as UPDATES_URL
from ...usecases.public_updates import read_updates
from ..deps import get_container

router = APIRouter(prefix="/api/public-catalog", tags=["feeds"])
PUBLIC_URL = "https://tobili.4wdo.cn/api/v1/public/catalog"
_lock = threading.Lock()
_cursor = "public_catalog.cursor"


class ReadIn(BaseModel):
    restart: bool = False


@router.get("")
def status(box: Container = Depends(get_container)):
    return {
        "url": UPDATES_URL,
        "updates_url": UPDATES_URL,
        "last_read": box.repos.settings.get(0, "public_catalog.at", ""),
    }


def import_page(box, page, cursor):
    rows, following = page.get("items"), page.get("next")
    if not isinstance(rows, list) or len(rows) > 100 or not isinstance(following, str):
        raise ValueError("目录分页无效")
    if following and (not following.isdecimal() or len(following) > 24):
        raise ValueError("目录游标无效")
    if rows and following and int(following) <= int(cursor or "0"):
        raise ValueError("目录游标未前进")
    now = box.clock.now()
    cards = []
    for row in rows:
        try:
            if row.get("draw_status", "") not in ("active", ""):
                continue
            stamp = int(row.get("lottery_time") or 0)
            deadline = datetime.fromtimestamp(stamp, UTC) if stamp > 0 else None
            if deadline and (deadline <= now or deadline.year > 2100):
                continue
            cards.append(
                Listing(
                    dyn_id=str(row["dynamic_id"]),
                    up_uid=str(row.get("sender_uid") or ""),
                    title=row["title"],
                    reward=row.get("reward") or "",
                    up_name=row.get("sender_name") or "",
                    deadline_at=deadline,
                    kind={
                        "转发抽奖": "share",
                        "评论抽奖": "comment",
                        "预约抽奖": "reserve",
                        "打卡抽奖": "check_in",
                    }.get(row.get("lottery_type"), "unknown"),
                )
            )
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError):
            continue
    added = 0
    with box.db.write() as db:
        for card in cards:
            if db.execute(
                "SELECT 1 FROM up_block WHERE uid=? AND active=1", (card.up_uid,)
            ).fetchone():
                continue
            # No participation, account state, confidence, or existing manual edits are imported.
            added += db.execute(
                "INSERT OR IGNORE INTO campaign(dyn_id,up_uid,title,up_name,reward,kind,deadline_at,source_ref,updated_at,first_seen_at) "
                "VALUES(:dyn_id,:up_uid,:title,:up_name,:reward,:kind,:deadline_at,'公开活动目录',:at,:at)",
                {**card.values(), "at": now.isoformat()},
            ).rowcount
        saved = following or cursor
        for key, value in ((_cursor, saved), ("public_catalog.at", now.isoformat())):
            db.execute(
                "INSERT OR REPLACE INTO setting(account_id,key,value) VALUES(0,?,?)", (key, value)
            )
    return {
        "read": len(rows),
        "added": added,
        "skipped": len(rows) - added,
        "cursor": saved,
        "more": bool(rows and following),
    }


def read_directory(box, client, restart=False):
    cursor = "" if restart else box.repos.settings.get(0, _cursor, "")
    totals = {"read": 0, "added": 0, "skipped": 0, "more": False}
    for _ in range(3):
        with client.stream("GET", PUBLIC_URL, params={"after": cursor, "limit": 100}) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 2_000_000:
                    raise ValueError("目录响应过大")
        result = import_page(box, json.loads(data), cursor)
        for key in ("read", "added", "skipped"):
            totals[key] += result[key]
        cursor, totals["more"] = result["cursor"], result["more"]
        if not totals["more"]:
            break
    return totals


@router.post("/read")
def read(body: ReadIn, box: Container = Depends(get_container)):
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "正在读取公开目录")
    try:
        # Independent transport: never inherit Bilibili cookies, Pro keys, or proxy credentials.
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            result = read_updates(box, client, body.restart)
            result["skipped"] = result["read"] - result["added"] - result["updated"]
            box.repos.settings.put(0, "public_catalog.at", box.clock.now().isoformat())
            return result
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(502, "公开目录暂不可用，已成功的批次保留，可继续读取") from exc
    finally:
        _lock.release()
