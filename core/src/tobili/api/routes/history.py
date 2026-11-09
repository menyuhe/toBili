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


@router.get("/attention")
def attention(box: Container = Depends(get_container)):
    account = box.account()
    if not account:
        return {"account_id": 0, "results": 0}
    owner = account[0]
    count = (
        box.db.connect()
        .execute(
            """SELECT count(DISTINCT e.campaign_id) FROM enrollment e
      LEFT JOIN participation_ref r ON r.campaign_id=e.campaign_id AND r.account_id=e.account_id
      LEFT JOIN result_probe p ON p.campaign_id=e.campaign_id AND p.account_id=e.account_id
      WHERE e.account_id=? AND NOT EXISTS(SELECT 1 FROM result_dismissal d WHERE d.account_id=e.account_id AND d.campaign_id=e.campaign_id)
      AND (r.link_state IN ('candidate','conflict') OR p.error='缺少 UP 信息')""",
            (owner,),
        )
        .fetchone()[0]
    )
    return {"account_id": owner, "results": count}


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
    "source.update",
    "result.discard",
)


@router.get("")
def history(
    limit: int = Query(default=120, ge=1, le=500),
    topics: str = Query(default="", description="逗号分隔，留空用默认集合"),
    issues_only: bool = False,
    before_seq: int = 0,
    category: str = "",
    box: Container = Depends(get_container),
) -> dict[str, object]:
    categories = {
        "sources": ("source.update", "scan"),
        "participation": ("enroll", "write", "participation.issue", "schedule.round"),
        "results": ("settle", "verdict.override", "tidy.run", "tidy.blocked", "result.discard"),
        "model": ("llm",),
    }
    wanted = (
        categories.get(category)
        or tuple(t.strip() for t in topics.split(",") if t.strip())
        or TOPICS
    )
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
