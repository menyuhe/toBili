"""订阅源与扫描。"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...container import Container
from ...domain.errors import MissingCredential
from ...runtime.lane import BusyAccount
from ...runtime.redact import safe_exception
from ...usecases.manage_feeds import FeedRejected
from ..deps import get_container

router = APIRouter(prefix="/api/feeds", tags=["feeds"])


class RefreshIn(BaseModel):
    account_id: int = Field(gt=0)
    enabled: bool = False
    interval_min: int = Field(default=60, ge=30, le=1440)


@router.get("/refresh")
def refresh_status(box: Container = Depends(get_container)):
    account = box.account()
    if not account:
        raise HTTPException(401, "请先登录")
    return {
        "enabled": box.repos.settings.get(account[0], "sources.refresh", "0") == "1",
        "interval_min": int(box.repos.settings.get(account[0], "sources.interval", "60")),
    }


@router.put("/refresh")
def save_refresh(body: RefreshIn, box: Container = Depends(get_container)):
    account = box.account()
    if not account or account[0] != body.account_id:
        raise HTTPException(409, "账号已切换，请重新设置")
    config = box.tasks.config(account[0])
    if (
        body.enabled
        and config["running"]
        and any(
            t["enabled"] and t["key"] in ("topics", "collections", "watch") for t in config["tasks"]
        )
    ):
        raise HTTPException(409, "来源正在由定时任务更新，请到定时任务页调整")
    box.repos.settings.put(account[0], "sources.interval", str(body.interval_min))
    box.repos.settings.put(account[0], "sources.refresh", "1" if body.enabled else "0")
    return refresh_status(box)


class FeedIn(BaseModel):
    kind: str = Field(default="space", description="space=某个 UP 的空间动态；following=我的关注流")
    target: str = Field(default="", description="空间来源填 UID；关注流留空")
    label: str = ""


class FeedPatch(BaseModel):
    enabled: bool | None = None
    label: str | None = None


class DiscoverIn(BaseModel):
    keyword: str = Field(min_length=1, description="关键词，例如 转发抽奖")
    limit: int = Field(default=10, ge=1, le=50)
    apply: bool = Field(default=False, description="为真时把候选直接加入订阅源")


@router.get("")
def list_feeds(box: Container = Depends(get_container)) -> dict[str, object]:
    account = box.account()
    if account is None:
        return {"items": []}
    return {"items": [asdict(item) for item in box.manage_feeds().list(account[0])]}


@router.post("")
def add_feed(body: FeedIn, box: Container = Depends(get_container)) -> dict[str, object]:
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    try:
        feed_id = box.manage_feeds().add(
            account[0], kind=body.kind, target=body.target, label=body.label
        )
    except FeedRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": feed_id}


@router.delete("/{feed_id}")
def delete_feed(feed_id: int, box: Container = Depends(get_container)) -> dict[str, str]:
    """真的删掉这个订阅源。

    只断"从哪发现活动"这条线，已经收进库的活动一条不少 —— 删订阅源不等于
    放弃已经发现的东西，这两件事混在一起会让人不敢删。
    """
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    try:
        box.manage_feeds().delete(account[0], feed_id)
    except FeedRejected as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"state": "deleted"}


@router.patch("/{feed_id}")
def patch_feed(
    feed_id: int, body: FeedPatch, box: Container = Depends(get_container)
) -> dict[str, str]:
    """改启用状态或备注。两项都可以只传一个。"""
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    manage = box.manage_feeds()
    try:
        if body.enabled is not None:
            if body.enabled:
                manage.enable(account[0], feed_id)
            else:
                manage.remove(account[0], feed_id)
        if body.label is not None:
            manage.rename(account[0], feed_id, body.label)
    except FeedRejected as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"state": "ok"}


@router.post("/discover")
def discover(body: DiscoverIn, box: Container = Depends(get_container)) -> dict[str, object]:
    """找的是"可能值得订阅的人"，不是活动。搜索接口不返回动态。"""
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    try:
        report = box.discover().run(account[0], body.keyword, limit=body.limit, apply=body.apply)
    except MissingCredential as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=safe_exception(exc)) from exc

    return {
        "keyword": report.keyword,
        "candidates": [
            {
                "uid": item.user.uid,
                "name": item.user.name,
                "fans": item.user.fans,
                "sign": item.user.sign,
                "score": item.score,
                "reasons": list(item.reasons),
            }
            for item in report.picked
        ],
        "added": list(report.added),
        "problems": list(report.problems),
    }


@router.post("/scan")
def scan(
    pages: int = Query(default=1, ge=1, le=20),
    limit: int = Query(default=20, ge=1, le=100),
    history: bool = False,
    feed_id: int | None = Query(default=None, ge=1),
    feed_ids: list[int] | None = Query(default=None),
    box: Container = Depends(get_container),
) -> dict[str, object]:
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    try:
        with box.lanes.exclusive(account[0]):
            report = box.scan_feeds().run(
                account[0],
                per_feed=limit,
                pages=pages,
                history=history,
                feed_id=feed_id,
                **({"feed_ids": feed_ids} if feed_ids is not None else {}),
            )
    except BusyAccount as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MissingCredential as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:  # 扫描失败要让人看到原因，不能静默返回 0 条
        raise HTTPException(status_code=502, detail=safe_exception(exc)) from exc

    box.bus.publish(
        "scan",
        {
            "account_id": account[0],
            **asdict(report),
            "title": f"订阅扫描 · 读取 {report.scanned} 条，活动 {report.matched} 条"
            + (" · " + report.problems[0] if report.problems else ""),
        },
    )
    return {
        "feeds": report.feeds,
        "scanned": report.scanned,
        "matched": report.matched,
        "unique": report.unique,
        "duplicates": report.duplicates,
        "sources": report.sources,
        "problems": list(report.problems),
        "model_calls": report.model_calls,
        "model_cache_hits": report.model_cache_hits,
        "model_extracted": report.model_extracted,
        "reused": report.reused,
        "official": report.official,
        "guessed": report.guessed,
    }
