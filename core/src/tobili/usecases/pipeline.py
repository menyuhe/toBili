"""一轮完整作业：扫描 → 选取 → 参与。

调度器只管"什么时候跑"，管"跑什么"的是这里。分开的理由很实际：定时逻辑很难测，
而选谁去参与、哪一步跳过，才是真正容易出错的地方。

账号之间并行、账号内串行。这不是为了快 —— 一个账号同时在写两件事，是这类工具被
风控的最短路径。账号之间互不相干，并行是安全的。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..runtime.lane import BusyAccount, Lanes
from ..runtime.redact import safe_exception
from .batch_enroll import BatchEnroll
from .scan_feeds import ScanFeeds


@dataclass(frozen=True, slots=True)
class AccountOutcome:
    account_id: int
    scanned: int = 0
    matched: int = 0
    unique: int = 0
    candidates: int = 0
    settled: int = 0
    blocked: int = 0
    failed: int = 0
    planned: int = 0
    incomplete: int = 0
    skipped: str = ""
    stop_reason: str = ""
    problems: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.problems and not self.failed and not self.skipped


@dataclass(frozen=True, slots=True)
class RoundReport:
    accounts: tuple[AccountOutcome, ...] = field(default=())

    @property
    def scanned(self) -> int:
        return sum(a.scanned for a in self.accounts)

    @property
    def settled(self) -> int:
        return sum(a.settled for a in self.accounts)

    @property
    def problems(self) -> int:
        return sum(len(a.problems) for a in self.accounts) + sum(
            1 for a in self.accounts if a.skipped
        )


class Pipeline:
    def __init__(
        self,
        *,
        scanner_for: Callable[[int], ScanFeeds],
        batch_for: Callable[[int], BatchEnroll],
        lanes: Lanes,
    ) -> None:
        # 收工厂而不是实例：同一轮里每个账号要跑自己的网关。装配一次再并行使用，
        # 就是拿第一个账号的 cookie 去写所有人（见测试 test_each_account_uses_own_reader）。
        self._scanner_for = scanner_for
        self._batch_for = batch_for
        self._lanes = lanes

    def run(
        self,
        accounts: Sequence[int],
        *,
        pages: int = 1,
        per_feed: int = 20,
        limit: int = 10,
        floor: float = 0.8,
        apply: bool = False,
    ) -> RoundReport:
        if not accounts:
            return RoundReport()

        def one(account_id: int) -> AccountOutcome:
            return self._one(
                account_id,
                pages=pages,
                per_feed=per_feed,
                limit=limit,
                floor=floor,
                apply=apply,
            )

        if len(accounts) == 1:
            return RoundReport(accounts=(one(accounts[0]),))
        with ThreadPoolExecutor(max_workers=min(4, len(accounts))) as pool:
            return RoundReport(accounts=tuple(pool.map(one, accounts)))

    def _one(
        self,
        account_id: int,
        *,
        pages: int,
        per_feed: int,
        limit: int,
        floor: float,
        apply: bool,
    ) -> AccountOutcome:
        try:
            with self._lanes.exclusive(account_id):
                scan = self._scanner_for(account_id).run(account_id, per_feed=per_feed, pages=pages)
                batch = self._batch_for(account_id).run(
                    account_id, limit=limit, floor=floor, dry_run=not apply
                )
        except BusyAccount:
            # 有人正在手动操作这个账号。这一轮跳过它，不排队 —— 排队会把定时任务
            # 变成一颗定时炸弹：人一停手，积压的几轮同时开始写。
            return AccountOutcome(account_id=account_id, skipped="账号正在执行其他任务")
        except Exception as exc:
            return AccountOutcome(
                account_id=account_id, problems=(f"整轮失败：{safe_exception(exc)}",)
            )

        return AccountOutcome(
            account_id=account_id,
            scanned=scan.scanned,
            matched=scan.matched,
            unique=scan.unique,
            candidates=batch.candidates,
            settled=batch.settled,
            blocked=batch.blocked,
            failed=batch.failed,
            planned=batch.planned,
            incomplete=batch.unfinished,
            stop_reason=batch.stop_reason,
            problems=scan.problems + batch.problems,
        )
