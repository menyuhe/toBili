"""Editable collection catalog. Deleted defaults stay deleted across upgrades."""
from __future__ import annotations

import json
import re
import threading
import time
from src.app_paths import user_home

_lock = threading.RLock()
DEFAULTS = [
    (2, "番茄薯条喵", "3546836235193146"), (3, "你的抽奖工具人", "100680137"),
    (4, "J君名", "126038161"), (6, "糯米是个背包", "492426375"),
    (8, "柚笺橘子味汽水", "6435997"), (9, "抽奖大王-超级无敌全", "3546589855484661"),
    (10, "导演A的剪辑", "14062306"), (7, "大锦鲤", "226257459"), (11, "C0Lex", "56334559"),
]


def catalog() -> list[dict]:
    path = user_home() / "config" / "collections.json"
    with _lock:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["sources"]
        return [dict(id=f"DS-{i}", name=name, mid=mid, backup=i in (7, 11)) for i, name, mid in DEFAULTS]


def save_source(values: dict, source_id: str | None = None) -> dict:
    name, mid = str(values.get("name", "")).strip(), str(values.get("mid", "")).strip()
    if not name or len(name) > 80 or not re.fullmatch(r"[1-9][0-9]{0,15}", mid):
        raise ValueError("请填写 UP 名称与有效数字 UID")
    with _lock:
        rows = catalog()
        if any(r["mid"] == mid and r["id"] != source_id for r in rows):
            raise ValueError("该 UP 已在合集名单中")
        old = next((r for r in rows if r["id"] == source_id), None)
        if source_id and old is None:
            raise ValueError("合集来源不存在")
        # A new UID receives a fresh identity, so old checkpoints cannot leak into it.
        new_id = old["id"] if old and old["mid"] == mid else f"DS-{time.time_ns()}"
        item = dict(id=new_id, name=name, mid=mid, backup=bool(values.get("backup")))
        rows = [item if r["id"] == source_id else r for r in rows] if old else [*rows, item]
        _write(rows)
        return item


def remove_source(source_id: str):
    with _lock:
        rows = catalog()
        if not any(r["id"] == source_id for r in rows):
            raise ValueError("合集来源不存在")
        _write([r for r in rows if r["id"] != source_id])


def reorder_sources(source_ids: list[str]) -> list[dict]:
    with _lock:
        rows = catalog()
        by_id = {row["id"]: row for row in rows}
        if len(source_ids) != len(by_id) or set(source_ids) != set(by_id):
            raise ValueError("合集名单已变化，请刷新后重新排序")
        ordered = [by_id[source_id] for source_id in source_ids]
        _write(ordered)
        return ordered


def _write(rows):
    path = user_home() / "config" / "collections.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"sources": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def handlers(defaults):
    from src.sources.article_source import ArticleSource
    original = {sid: (check, save) for sid, check, save in defaults}
    mids = {f"DS-{i}": mid for i, _, mid in DEFAULTS}
    result = []
    for item in catalog():
        source = ArticleSource(item["id"], int(item["mid"]))
        pair = original.get(item["id"]) if mids.get(item["id"]) == item["mid"] else None
        result.append((item, *(pair or (source.check_update, source.save_result))))
    return result
