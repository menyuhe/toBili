"""首页聚合。

一次给全，前端不做五次请求。这个接口是"界面有多快"的关键 —— 它只读本地数据，
不联网，所以永远不会因为 B 站慢而卡住。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...container import Container
from ..deps import get_container

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/overview")
def overview(box: Container = Depends(get_container)) -> dict[str, object]:
    return box.overview()
