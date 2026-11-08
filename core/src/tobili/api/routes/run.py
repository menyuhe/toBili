"""一轮作业：扫描 + 批量参与。

路由只做两件事：把参数搬给用例、把结果摊平成 JSON。任何"要不要做"的判断都在
用例里 —— 尤其是 `apply` 这个开关，默认必须是预演，接口不能有一个"默认就写"的洞。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...container import Container
from ...domain.errors import MissingCredential
from ...runtime.redact import safe_exception
from ..deps import get_container

router = APIRouter(prefix="/api/run", tags=["run"])


@router.post("")
def run_once(
    limit: int = 10,
    floor: float = 0.8,
    pages: int = 1,
    per_feed: int = 20,
    apply: bool = False,
    all_accounts: bool = False,
    box: Container = Depends(get_container),
) -> dict[str, object]:
    account = box.account()
    if account is None:
        raise HTTPException(status_code=401, detail="还没有登录")
    if not 0.0 <= floor <= 1.0:
        raise HTTPException(status_code=400, detail="置信度门槛必须在 0..1 之间")

    # 默认只跑当前账号。多跑一个号就多一份写操作，"顺手全做一遍"不该是默认值。
    targets = [account[0]]
    if all_accounts:
        targets = [int(item["id"]) for item in box.accounts() if item["ready"]]
        if not targets:
            raise HTTPException(status_code=409, detail="没有凭据可用的账号")

    try:
        report = box.pipeline().run(
            targets,
            pages=max(1, pages),
            per_feed=max(1, per_feed),
            limit=max(0, limit),
            floor=floor,
            apply=apply,
        )
    except MissingCredential as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=safe_exception(exc)) from exc

    return {
        "mode": "apply" if apply else "preview",
        "scanned": report.scanned,
        "settled": report.settled,
        "accounts": [
            {
                "account_id": outcome.account_id,
                "scanned": outcome.scanned,
                "matched": outcome.matched,
                "unique": outcome.unique,
                "candidates": outcome.candidates,
                "settled": outcome.settled,
                "blocked": outcome.blocked,
                "failed": outcome.failed,
                "planned": outcome.planned,
                "incomplete": outcome.incomplete,
                "skipped": outcome.skipped,
                "stop_reason": outcome.stop_reason,
                "problems": list(outcome.problems),
            }
            for outcome in report.accounts
        ],
    }
