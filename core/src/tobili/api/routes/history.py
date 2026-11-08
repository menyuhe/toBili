"""任务历史。

事件总线上的东西以前只活在内存里，界面一刷新就什么都不剩。现在总线每发一条就
顺手落库，这一组端点把落库的那些读回来。

刻意保留 `seq`：界面上"第 37 条"这种编号不是装饰，它是排查问题时唯一能对齐
日志和操作的东西。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...container import Container
from ..deps import get_container

router = APIRouter(prefix="/api/history", tags=["history"])

# 这些主题是"发生了什么"，值得留在历史里。`probe` 太吵（每条动作两次），
# 默认不返回，但可以通过 topics 参数显式要。
TOPICS = (
    "write",
    "verdict.override",
    "tidy.run",
    "tidy.blocked",
    "scan",
    "enroll",
    "settle",
    "llm",
    "notify",
    "schedule.round",
    "participation.issue",
)


@router.get("")
def history(
    limit: int = Query(default=120, ge=1, le=500),
    topics: str = Query(default="", description="逗号分隔，留空用默认集合"),
    issues_only: bool = False,
    before_seq: int = 0,
    box: Container = Depends(get_container),
) -> dict[str, object]:
    wanted = tuple(t.strip() for t in topics.split(",") if t.strip()) or TOPICS
    account = box.account()
    rows = box.repos.events.recent(
        limit=limit,
        topics=None if issues_only else wanted,
        issues_only=issues_only,
        account_id=account[0] if account else None,
        before_seq=before_seq,
    )
    return {
        "items": [
            {
                "seq": row["seq"],
                "topic": row["topic"],
                "payload": row["payload"],
                "at": row["at"].isoformat() if row["at"] else None,  # type: ignore[union-attr]
            }
            for row in rows
        ],
        "topics": list(wanted),
    }
