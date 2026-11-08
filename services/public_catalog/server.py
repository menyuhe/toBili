"""Independent public database. Anonymous reads; authenticated metadata-only writes."""

import hmac
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from tobili.publicwire import PublicPrize


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[PublicPrize] = Field(max_length=100)


def create_app(path, key):
    def current_key():
        return key() if callable(key) else key

    if len(current_key()) < 32:
        raise ValueError("Publisher key must be at least 32 characters")
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def database():
        con = sqlite3.connect(path, timeout=20)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    with database() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            "CREATE TABLE IF NOT EXISTS card(dyn TEXT PRIMARY KEY,rev INTEGER UNIQUE,payload TEXT NOT NULL,updated_at TEXT NOT NULL)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS counter(id INTEGER PRIMARY KEY CHECK(id=1),rev INTEGER NOT NULL)"
        )
        con.execute("INSERT OR IGNORE INTO counter VALUES(1,0)")
        con.execute(
            "CREATE TABLE IF NOT EXISTS publication_source(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL)"
        )
        con.execute("INSERT OR IGNORE INTO publication_source VALUES(1,0)")
    app = FastAPI(title="toBili public prizes", docs_url=None, redoc_url=None)

    def publisher(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization.encode(), ("Bearer " + current_key()).encode()):
            raise HTTPException(401, "Publishing key required")

    @app.post("/v1/catalog", dependencies=[Depends(publisher)])
    def publish(
        batch: Batch,
        authorization: str = Header(default=""),
        x_catalog_revision: int | None = Header(default=None),
    ):
        changed = 0
        with database() as con:
            con.execute("BEGIN IMMEDIATE")
            # Re-check inside the write transaction: an old publisher cannot commit after handover.
            publisher(authorization)
            previous = con.execute("SELECT revision FROM publication_source WHERE id=1").fetchone()[
                0
            ]
            if x_catalog_revision is not None:
                if x_catalog_revision < previous:
                    raise HTTPException(409, "Older checkpoint rejected")
                con.execute(
                    "UPDATE publication_source SET revision=? WHERE id=1", (x_catalog_revision,)
                )
            for item in batch.items:
                payload = item.model_dump_json()
                old = con.execute("SELECT payload FROM card WHERE dyn=?", (item.dyn_id,)).fetchone()
                if old and old[0] == payload:
                    continue
                con.execute("UPDATE counter SET rev=rev+1 WHERE id=1")
                rev = con.execute("SELECT rev FROM counter WHERE id=1").fetchone()[0]
                con.execute(
                    "INSERT OR REPLACE INTO card VALUES(?,?,?,?)",
                    (item.dyn_id, rev, payload, datetime.now(UTC).isoformat(timespec="seconds")),
                )
                changed += 1
        return {"accepted": len(batch.items), "changed": changed}

    @app.get("/v1/catalog")
    def read(after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
        with database() as con:
            rows = con.execute(
                "SELECT * FROM card WHERE rev>? ORDER BY rev LIMIT ?", (after, limit + 1)
            ).fetchall()
        selected = rows[:limit]
        return {
            "items": [json.loads(r["payload"]) for r in selected],
            "next": selected[-1]["rev"] if selected else after,
            "more": len(rows) > limit,
        }

    @app.get("/", response_class=HTMLResponse)
    def index(page: int = Query(1, ge=1)):
        with database() as con:
            total = con.execute("SELECT count(*) FROM card").fetchone()[0]
            rows = con.execute(
                "SELECT * FROM card ORDER BY rev DESC LIMIT 50 OFFSET ?", ((page - 1) * 50,)
            ).fetchall()
        body = []
        for row in rows:
            c = json.loads(row["payload"])
            date = c["deadline_at"] or (
                (c["deadline_date"] + " · 时分未提供") if c["deadline_date"] else "未提供"
            )
            body.append(
                f'<tr><td><a href="https://t.bilibili.com/{c["dyn_id"]}" target="_blank" rel="noopener noreferrer">{escape(c["reward"])}</a></td><td>{escape(c["up_name"])}</td><td>{escape(date)}</td></tr>'
            )
        previous = f'<a href="?page={page - 1}">上一页</a>' if page > 1 else ""
        following = f'<a href="?page={page + 1}">下一页</a>' if page * 50 < total else ""
        return (
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>toBili 公开奖品目录</title><style>body{font:16px/1.6 system-ui;background:#f5f7fb;color:#17253a;max-width:1100px;margin:32px auto;padding:20px}table{width:100%;background:white;border:1px solid #ccd6e4;border-radius:12px;border-spacing:0}td,th{padding:16px;border-bottom:1px solid #e5eaf1;text-align:left}th{background:#e7effb}a{color:#2163ad;text-decoration:none}nav{display:flex;gap:24px;justify-content:center;margin:24px}td:first-child{width:60%}</style><h1>公开活动奖品</h1><p>仅含公开动态资料 · 共 '
            + str(total)
            + " 条</p><table><thead><tr><th>活动奖品</th><th>UP</th><th>开奖时间</th></tr></thead><tbody>"
            + "".join(body)
            + f"</tbody></table><nav>{previous}<span>{page}</span>{following}</nav></html>"
        )

    return app
