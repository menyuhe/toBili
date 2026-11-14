"""收件箱。

读三类消息（私信、回复我的、@我的），标出"可能在说中奖"，并记下"我看过了"。

**这个接口不会清掉 B 站的红点。** 没有那样的公开接口，"已看"只是本机记录。
所以返回体里带着 `system_count`（只有条数、没有内容）和一个说明字段 ——
把人会误会的地方写在数据里，比写在文档里管用。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...container import Container
from ...domain.errors import MissingCredential
from ...runtime.lane import BusyAccount
from ...runtime.redact import safe_exception
from ..deps import get_container

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

# 消息缓存：读一次要打三四个 B 站接口，而"翻回消息页""侧边栏轮询""点开又来一遍"
# 在几分钟内读到的是同一批内容。`refresh=1` 可以强制绕开。
#
# 为什么不做得更长：中奖通知晚几分钟看到不致命，但人手动点「刷新」还拿到旧结果
# 是会挨骂的 —— 所以强制刷新这条口子必须留着，而且刷完要把新结果写回缓存。
#
# 为什么挂在容器上而不是模块上：模块级的键只有 account_id，换库、删号、跑测试
# 都会读到上一份数据。这种脏读最难查，因为它看起来像"接口返回错了"。
_CACHE_TTL = 120.0
_Cache = dict[tuple[int, int, int], tuple[float, dict[str, object]]]


def _store(box: Container) -> _Cache:
    store = getattr(box, "_inbox_cache", None)
    if not isinstance(store, dict):
        store = {}
        box._inbox_cache = store
    return store


def _now_mono() -> float:
    import time

    return time.monotonic()


def _sync_unread(box: Container, account_id: int) -> None:
    """把缓存里这一号的未读数抄到 setting。

    只抄已有的缓存，不去回源：这条路径的触发点是"点了已看"，用户等的是这一下
    立刻生效，不是再等一轮网络。
    """
    picked: tuple[int, dict[str, object]] | None = None
    for key, (_, payload) in _store(box).items():
        if key[0] != account_id:
            continue
        if picked is None or key[1] < picked[0]:
            picked = (key[1], payload)
    if picked is None:
        return
    try:
        counts = picked[1].get("counts") or {}
        unread = int(counts.get("unread", 0))  # type: ignore[union-attr]
        box.repos.settings.put(account_id, "inbox.unread", str(unread))
    except Exception:
        pass


def _forget(box: Container, account_id: int) -> None:
    store = _store(box)
    for key in [k for k in store if k[0] == account_id]:
        store.pop(key, None)


def _patch_seen(box: Container, account_id: int, keys: list[str]) -> None:
    """点了「已看」就地把缓存里的标记改掉。

    不整份作废：作废之后界面马上又会去读一遍 B 站，为了一个本地标记重新拉
    几十条消息，正是这次要消掉的那笔浪费。
    """
    wanted = set(keys)
    for key, (_, payload) in _store(box).items():
        if key[0] != account_id:
            continue
        items = payload.get("items")
        if not isinstance(items, list):
            continue
        changed = False
        for item in items:
            if isinstance(item, dict) and item.get("key") in wanted and item.get("unread"):
                item["unread"] = False
                changed = True
        if changed:
            counts = dict(payload.get("counts") or {})  # type: ignore[arg-type]
            counts["unread"] = sum(1 for i in items if isinstance(i, dict) and i.get("unread"))
            payload["counts"] = counts


class ReviewIn(BaseModel):
    account_id: int
    key: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["irrelevant", "auto"]


@router.post("/review")
def review_message(body: ReviewIn, box: Container = Depends(get_container)):
    from .schedule import owner

    account_id = owner(box, body.account_id)
    box.repos.inbox.review(account_id, body.key, body.decision == "irrelevant")
    # 判定「与抽奖无关」会改这一条的 prize 标记，缓存整份作废比逐条改省事。
    _forget(box, account_id)
    return {"reviewed": body.decision == "irrelevant"}


class SeenIn(BaseModel):
    keys: list[str]
    account_id: int | None = None


def _account(box: Container) -> int:
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    return account[0]


@router.get("")
def read_inbox(
    pages: int = 1,
    limit: int = 60,
    refresh: bool = False,
    box: Container = Depends(get_container),
) -> dict[str, object]:
    account_id = _account(box)
    key = (account_id, max(1, pages), max(1, limit))
    store = _store(box)
    hit = store.get(key)
    if not refresh and hit and _now_mono() - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        view = box.inbox(account_id).fetch(account_id, pages=max(1, pages), limit=limit)
    except MissingCredential as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except BusyAccount as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=safe_exception(exc)) from exc

    payload: dict[str, object] = {
        "items": [
            {
                **asdict(item),
                "at": item.at.isoformat() if item.at else None,
                "short": item.short,
            }
            for item in view.items
        ],
        "counts": view.counts,
        "system_count": view.system_count,
        "problems": list(view.problems),
        # 界面直接用这句话，别在别处再写一遍"已读只是本机记录"。
        "note": "「已看」只记在本机，不会清掉 B 站的红点。系统通知只有条数，B 站没有提供列表接口。",
    }
    store[key] = (_now_mono(), payload)
    # 顺手把未读数落到 setting 里。挂机运作页要按账号显示"这个号有几条消息"，
    # 而那一页是 5 秒轮询、纯本地读取，绝不能为了一个数字去打 B 站三四个接口。
    # 落库之后两边口径也一致：侧栏那个合计就是卡上数字相加。
    try:
        unread = int((payload.get("counts") or {}).get("unread", 0))  # type: ignore[union-attr]
        box.repos.settings.put(account_id, "inbox.unread", str(unread))
    except Exception:
        pass
    return payload


@router.post("/seen")
def mark_seen(body: SeenIn, box: Container = Depends(get_container)) -> dict[str, object]:
    from .schedule import owner

    account_id = owner(box, body.account_id)
    count = box.inbox(account_id).mark_seen(account_id, body.keys)
    _patch_seen(box, account_id, body.keys)
    # 已读数变了，落库的那份也要跟着变，否则挂机运作页上的数字停在上一次读取。
    _sync_unread(box, account_id)
    return {"marked": count}


@router.delete("/seen")
def forget_seen(box: Container = Depends(get_container)) -> dict[str, str]:
    """清掉本机记录，让所有消息重新算未读。B 站那边一条都不会少。"""
    account_id = _account(box)
    box.inbox(account_id).forget_seen(account_id)
    _forget(box, account_id)
    return {"state": "cleared"}
