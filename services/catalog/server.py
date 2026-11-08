"""自建活动资料簿：与桌面端分开部署，不读取桌面数据库。"""

import os
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from tobili.catalogwire import Listing


class Parcel(BaseModel):
    items: list[Listing] = Field(max_length=100)


def create_app(path: Path, key: str) -> FastAPI:
    if len(key) < 32 or not key.isascii():
        raise ValueError("目录密钥需至少 32 个 ASCII 字符")
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as con, con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS listing(seq INTEGER PRIMARY KEY, dyn TEXT UNIQUE NOT NULL, card TEXT NOT NULL)"
        )

    def authorize(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization.encode(), ("Bearer " + key).encode()):
            raise HTTPException(401, "目录密钥无效")

    app = FastAPI(title="toBili 资料簿", dependencies=[Depends(authorize)])

    @app.post("/v1/listings")
    def accept(parcel: Parcel):
        with closing(sqlite3.connect(path)) as con, con:
            con.executemany(
                "INSERT OR IGNORE INTO listing(dyn,card) VALUES(?,?)",
                [(x.dyn_id, x.model_dump_json()) for x in parcel.items],
            )
            return {"added": con.total_changes}

    @app.get("/v1/listings")
    def page(after: int = Query(default=0, ge=0)):
        with closing(sqlite3.connect(path)) as con:
            rows = con.execute(
                "SELECT seq,card FROM listing WHERE seq>? ORDER BY seq LIMIT 100", (after,)
            ).fetchall()
        return {
            "items": [Listing.model_validate_json(card) for _, card in rows],
            "next": rows[-1][0] if rows else after,
        }

    return app


def factory():
    return create_app(
        Path(os.environ.get("TOBILI_CATALOG_DB", "listings.sqlite")),
        os.environ["TOBILI_CATALOG_KEY"],
    )
