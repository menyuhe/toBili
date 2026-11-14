"""任务历史。

事件总线上的东西以前只活在内存里，界面一刷新就什么都不剩。现在总线每发一条就
顺手落库，这一组端点把落库的那些读回来。

刻意保留 `seq`：界面上"第 37 条"这种编号不是装饰，它是排查问题时唯一能对齐
日志和操作的东西。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

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
    "exchange.review",
)

# 定时任务是同一个 topic（`schedule.round`），但跑的是完全不同的东西。
# 以前把整个 topic 归到"参与"，于是一轮话题更新在日志里长得像一次报名，
# 排查时会直接把人带偏。任务名才是分类依据。
TASK_CATEGORY = {
    "topics": "sources",
    "collections": "sources",
    "watch": "sources",
    "participate": "participation",
    "results": "results",
    "messages": "results",
    "cleanup": "results",
}
TASK_LABEL = {
    "topics": "话题",
    "collections": "合集",
    "watch": "动态",
    "participate": "参与",
    "results": "核验",
    "messages": "消息",
    "cleanup": "清理",
}
# 老记录没有再带任务名，只能靠标题前缀认。
TITLE_CATEGORY = (
    ("话题更新", "sources"),
    ("UP 合集更新", "sources"),
    ("UP 动态更新", "sources"),
    ("参与活动", "participation"),
    ("开奖核验", "results"),
    ("中奖消息", "results"),
    ("清理检查", "results"),
)


def round_category(payload: dict) -> str:
    task = str(payload.get("task") or "")
    if task in TASK_CATEGORY:
        return TASK_CATEGORY[task]
    title = str(payload.get("title") or "")
    for prefix, bucket in TITLE_CATEGORY:
        if title.startswith(prefix):
            return bucket
    return ""


def round_label(payload: dict) -> str:
    task = str(payload.get("task") or "")
    if task in TASK_LABEL:
        return TASK_LABEL[task]
    title = str(payload.get("title") or "")
    for prefix, _ in TITLE_CATEGORY:
        if title.startswith(prefix):
            return prefix.replace("UP ", "").replace("更新", "").replace("活动", "") or "任务"
    return "任务"


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
        "participation": ("enroll", "write", "participation.issue"),
        "results": (
            "settle",
            "verdict.override",
            "tidy.run",
            "tidy.blocked",
            "result.discard",
        ),
        "model": ("llm", "exchange.review"),
    }
    wanted = (
        categories.get(category)
        or tuple(t.strip() for t in topics.split(",") if t.strip())
        or TOPICS
    )
    # 分类视图要连着定时任务的汇总行一起筛：一轮"开奖核验"的结论只出现在
    # schedule.round 里，漏掉它会让"开奖 / 清理"这一栏看起来什么都没发生。
    with_rounds = category in ("", "sources", "participation", "results")
    if not issues_only and with_rounds:
        wanted = tuple(dict.fromkeys((*wanted, "schedule.round")))
    account = box.account()
    fetch = min(500, limit * 4) if category in ("sources", "participation", "results") else limit
    rows = box.repos.events.recent(
        limit=fetch,
        topics=None if issues_only else wanted,
        issues_only=issues_only,
        account_id=account[0] if account else None,
        before_seq=before_seq,
    )
    if category in ("sources", "participation", "results"):
        rows = [
            row
            for row in rows
            if row["topic"] != "schedule.round" or round_category(row["payload"]) == category
        ][:limit]
    return {
        "items": [
            {
                "seq": row["seq"],
                "topic": row["topic"],
                "label": round_label(row["payload"]) if row["topic"] == "schedule.round" else "",
                "payload": row["payload"],
                "at": row["at"].isoformat() if row["at"] else None,  # type: ignore[union-attr]
            }
            for row in rows
        ],
        "topics": list(wanted),
    }


class DismissIn(BaseModel):
    seq: int = Field(gt=0)
    note: str = Field(default="", max_length=200)


@router.post("/dismiss")
def dismiss(body: DismissIn, box: Container = Depends(get_container)) -> dict[str, object]:
    """「知道了」：这条异常不再出现，但忽略动作本身留痕。

    不删记录，也不改记录 —— 同一个问题下次再发生是新的 seq，仍然会被看见。
    """
    row = (
        box.db.connect().execute("SELECT topic FROM run_event WHERE seq=?", (body.seq,)).fetchone()
    )
    box.repos.events.dismiss(
        seq=body.seq,
        topic=str(row["topic"]) if row else "",
        note=body.note,
        at=box.clock.now(),
    )
    return {"seq": body.seq, "dismissed": True}


class DismissManyIn(BaseModel):
    seqs: list[int] = Field(min_length=1, max_length=200)


@router.post("/dismiss/bulk")
def dismiss_many(body: DismissManyIn, box: Container = Depends(get_container)) -> dict[str, object]:
    """整页忽略。

    异常列表里同一类旧问题会积很多条（一次网络抖动就是十几条"无法确认是否已执行"），
    逐条点等于让人做机械劳动。整页压掉之后，只有**新**发生的才会再出现。
    """
    rows = {
        int(row["seq"]): str(row["topic"])
        for row in box.db.connect()
        .execute(
            f"SELECT seq, topic FROM run_event WHERE seq IN ({','.join('?' * len(body.seqs))})",
            tuple(body.seqs),
        )
        .fetchall()
    }
    now = box.clock.now()
    for seq in dict.fromkeys(body.seqs):
        box.repos.events.dismiss(seq=seq, topic=rows.get(seq, ""), note="整页忽略", at=now)
    return {"dismissed": len(body.seqs)}
