"""事件流。

界面的进度条不该靠轮询数据库：轮询要么慢（间隔长）、要么吵（间隔短），而且它
看到的是"结果已经落库"之后的状态，中间那几百毫秒的"正在做什么"永远看不到。

这里用 SSE 而不是 WebSocket，因为数据只往一个方向流。单向流用双向协议要多维护
一套心跳、重连、序列化，换不来任何东西。

`id:` 是总线里的自增序号，浏览器断线重连时会带 `Last-Event-ID` 回来，于是能只补
发漏掉的那几条 —— 这也是序号存在的原因，不是装饰。
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ...container import Container
from ..deps import get_container

router = APIRouter(prefix="/api", tags=["events"])

# 没有事件时多久发一次注释行。反向代理和浏览器都会掐掉长时间静默的连接。
HEARTBEAT_SECONDS = 10.0


def _frame(event_id: int, payload: dict[str, object]) -> str:
    return f"id: {event_id}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/events")
async def stream(request: Request, box: Container = Depends(get_container)) -> StreamingResponse:
    """服务端推送。只读：这里不触发任何写操作。

    未登录也允许连 —— 登录这件事本身产生事件（扫码确认后会推 `auth.signed_in`），
    要求先登录才能看，等于要求先有鸡。
    """
    bus = box.bus

    async def frames():  # type: ignore[no-untyped-def]
        # 客户端带 Last-Event-ID 回来时从断点补发；否则从现在开始，不重放历史。
        raw = request.headers.get("last-event-id", "")
        try:
            seq = int(raw) if raw else bus.last_seq
        except ValueError:
            seq = bus.last_seq

        yield ": connected\n\n"
        while not await request.is_disconnected():
            # 总线是阻塞的，丢到线程里等，别让一个空闲连接占住事件循环。
            events = await asyncio.to_thread(bus.wait, seq, timeout=HEARTBEAT_SECONDS)
            if not events:
                yield ": ping\n\n"
                continue
            for event in events:
                seq = event.seq
                yield _frame(event.seq, {"topic": event.topic, "at": event.at, **event.payload})

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # nginx 默认攒够一块才发，SSE 会变成假死
        },
    )
