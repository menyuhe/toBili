"""一轮作业的编排。

这里只测一件事，但那件事最贵：**每个账号必须用自己那套东西**。

Pipeline 原来收的是装配好的 ScanFeeds / BatchEnroll，改成收工厂就是为了这个 ——
一条实例被两个线程共用，写操作就会落在错误的账号名下。测试盯着"工厂被按账号
调用"，而不是"跑完了"。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from tobili.runtime.lane import Lanes
from tobili.usecases.batch_enroll import BatchReport
from tobili.usecases.pipeline import Pipeline
from tobili.usecases.scan_feeds import ScanReport


@dataclass
class Spy:
    """记下每次被调用时的 account_id，并按账号返回不同结果。"""

    scans: list[int] = field(default_factory=list)
    batches: list[int] = field(default_factory=list)

    def scanner_for(self, account_id: int):  # type: ignore[no-untyped-def]
        self.scans.append(account_id)

        class _Scanner:
            def run(self, target: int, **_kwargs: int) -> ScanReport:
                assert target == account_id, "扫描的账号和装配时的账号不一致"
                return ScanReport(scanned=account_id, matched=account_id, unique=account_id)

        return _Scanner()

    def batch_for(self, account_id: int):  # type: ignore[no-untyped-def]
        self.batches.append(account_id)

        class _Batch:
            def run(self, target: int, **_kwargs: object) -> BatchReport:
                assert target == account_id, "参与的账号和装配时的账号不一致"
                return BatchReport(candidates=account_id, settled=account_id)

        return _Batch()


def _run(accounts: Sequence[int]) -> tuple[Spy, object]:
    spy = Spy()
    pipeline = Pipeline(scanner_for=spy.scanner_for, batch_for=spy.batch_for, lanes=Lanes())
    report = pipeline.run(list(accounts), pages=1, per_feed=5, limit=5)
    return spy, report


def test_each_account_gets_its_own_assembly() -> None:
    spy, _ = _run([1, 2])
    assert sorted(spy.scans) == [1, 2]
    assert sorted(spy.batches) == [1, 2]


def test_single_account_does_not_go_through_thread_pool() -> None:
    """单账号是常态，不该为它开线程池 —— 也不该因此改变装配次数。"""
    spy, report = _run([7])
    assert spy.scans == [7]
    assert spy.batches == [7]
    assert report.accounts[0].account_id == 7  # type: ignore[attr-defined]


def test_no_accounts_is_a_no_op() -> None:
    spy, report = _run([])
    assert spy.scans == [] and spy.batches == []
    assert report.accounts == ()  # type: ignore[attr-defined]


def test_busy_account_is_skipped_not_queued() -> None:
    """有人正在手动操作这个账号时，这一轮跳过它，不排队。

    排队会把定时任务变成一颗定时炸弹：人一停手，积压的几轮同时开始写。
    """
    spy = Spy()
    lanes = Lanes()
    pipeline = Pipeline(scanner_for=spy.scanner_for, batch_for=spy.batch_for, lanes=lanes)

    with lanes.exclusive(1):
        report = pipeline.run([1], pages=1, per_feed=5, limit=5)

    assert report.accounts[0].skipped == "账号正在执行其他任务"
    assert spy.scans == []


def test_one_account_failing_does_not_stop_the_other() -> None:
    """一个账号炸了，另一个照跑完 —— 否则一个坏号会让整轮静默停摆。"""

    class _Boom:
        def run(self, _target: int, **_kwargs: int) -> ScanReport:
            raise RuntimeError("上游改了字段")

    class _Fine:
        def run(self, target: int, **_kwargs: int) -> ScanReport:
            return ScanReport(scanned=1, matched=1, unique=1)

    scanners = {1: _Boom(), 2: _Fine()}
    batches = {1: BatchReport(), 2: BatchReport(settled=1)}
    pipeline = Pipeline(
        scanner_for=lambda account_id: scanners[account_id],
        batch_for=lambda account_id: type(
            "B", (), {"run": lambda _self, target, **_kw: batches[target]}
        )(),
        lanes=Lanes(),
    )

    report = pipeline.run([1, 2], pages=1, per_feed=5, limit=5)
    by_id = {outcome.account_id: outcome for outcome in report.accounts}
    assert "上游改了字段" in by_id[1].problems[0]
    assert by_id[2].settled == 1
