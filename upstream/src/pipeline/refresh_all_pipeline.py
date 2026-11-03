from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Callable, Literal

from src.activity_store import append_activities, known_activity_ids
from src.bilibili_client import BilibiliClient
from src.forward_parser import DynamicContentFetchError
from src.lottery_enricher import (
    ENRICH_SKIP_REASON,
    EnrichedActivity,
    EnrichSkippedError,
    enrich_activity,
    is_enrich_detail_skip_error,
)
from src.lottery_classifier import LotteryType
from src.participation_store import load_participations
from src.pipeline.classify_step import ClassifyOutcome, classify_new_link
from src.pipeline.status_step import apply_initial_status
from src.sources.common import CheckResult, normalize_activity_id
from src.task_control import check_cancelled
from src.dead_links import DynamicDeletedError

ProgressCallback = Callable[[int, int, str], None]


@dataclass
class PipelineResult:
    ok: bool
    pipeline_skipped: bool
    raw_link_count: int
    new_link_count: int
    classified_count: int
    skipped_count: int
    enriched_count: int
    persisted_count: int
    skip_reasons: dict[str, int] = field(default_factory=dict)
    message: str = ""
    deferred_ids: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _dedupe_new_dynamic_ids(raw_urls: list[str]) -> list[str]:
    known = known_activity_ids()
    seen: set[str] = set()
    ordered: list[str] = []
    for raw_url in raw_urls:
        dynamic_id = normalize_activity_id(str(raw_url))
        if not dynamic_id or dynamic_id in known or dynamic_id in seen:
            continue
        seen.add(dynamic_id)
        ordered.append(dynamic_id)
    return ordered


def _collect_updated_links(ds_results: list[CheckResult]) -> list[str]:
    links: list[str] = []
    for result in ds_results:
        if not result.updated:
            continue
        links.extend(result.activity_links or [])
    return links


def run_new_links_pipeline(
    raw_urls: list[str],
    *,
    workers: int = 4,
    on_progress: ProgressCallback | None = None,
    defer_unavailable_content: bool = False,
) -> PipelineResult:
    """Step 2～5：仅处理新链接（内存流转，末步落库）。"""
    check_cancelled()
    dynamic_ids = _dedupe_new_dynamic_ids(raw_urls)
    if not dynamic_ids:
        return PipelineResult(
            ok=True,
            pipeline_skipped=True,
            raw_link_count=len(raw_urls),
            new_link_count=0,
            classified_count=0,
            skipped_count=0,
            enriched_count=0,
            persisted_count=0,
            message="无新链接，流水线结束",
        )

    participations = load_participations()
    skip_reasons: dict[str, int] = {}
    tasks: list[tuple[str, LotteryType]] = []
    classify_outcome_by_id: dict[str, ClassifyOutcome] = {}
    deferred_ids: list[str] = []
    diagnostics: list[str] = []
    total = len(dynamic_ids)

    if on_progress:
        on_progress(0, total, f"正在分类 {total} 条新链接…")

    with BilibiliClient() as shared_client:
        done = 0
        for dynamic_id in dynamic_ids:
            check_cancelled()
            try:
                outcome = classify_new_link(shared_client, dynamic_id)
            except DynamicContentFetchError as exc:
                if not defer_unavailable_content:
                    raise
                deferred_ids.append(dynamic_id)
                diagnostics.append(f"待重试 https://www.bilibili.com/opus/{dynamic_id}：{exc}")
                done += 1
                if on_progress:
                    on_progress(done, total, f"动态 {dynamic_id} 正文暂不可用，已保留待重试；继续分类")
                continue
            check_cancelled()
            done += 1
            if outcome.skipped:
                reason = outcome.skip_reason or "skipped"
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                if reason in ('链接失效', '动态页面不可访问（404）'):
                    diagnostics.append(f"已跳过 https://www.bilibili.com/opus/{dynamic_id}：{reason}")
            elif outcome.lottery_type in ("互动抽奖", "预约抽奖", "转发抽奖"):
                tasks.append((outcome.dynamic_id, outcome.lottery_type))
                classify_outcome_by_id[outcome.dynamic_id] = outcome
            if on_progress:
                on_progress(done, total, "分类进度")

    worker_count = max(1, min(workers, len(tasks) if tasks else 1))

    if not tasks:
        return PipelineResult(
            ok=True,
            pipeline_skipped=False,
            raw_link_count=len(raw_urls),
            new_link_count=len(dynamic_ids),
            classified_count=0,
            skipped_count=sum(skip_reasons.values()),
            enriched_count=0,
            persisted_count=0,
            skip_reasons=skip_reasons,
            message="部分动态正文暂不可用，已保留待重试" if deferred_ids else "新链接均已跳过（非抽奖/充电/失效）",
            deferred_ids=deferred_ids,
            diagnostics=diagnostics,
        )

    if on_progress:
        on_progress(0, len(tasks), f"正在拉取 {len(tasks)} 条活动详情…")

    enriched_rows: list[dict] = []
    enrich_total = len(tasks)

    def _enrich_one(
        client: BilibiliClient,
        dynamic_id: str,
        lottery_type: LotteryType,
        outcome: ClassifyOutcome,
    ) -> EnrichedActivity:
        check_cancelled()
        return enrich_activity(
            client,
            dynamic_id=dynamic_id,
            lottery_type=lottery_type,
            participation=participations.get(dynamic_id),
            classify_content=outcome.classify_content or None,
            classify_detail=outcome.detail_item,
            classify_notice=outcome.lottery_notice,
            classify_notice_business_type=outcome.notice_business_type,
            classify_notice_business_id=outcome.notice_business_id,
        )

    with BilibiliClient() as enrich_client:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    contextvars.copy_context().run,
                    _enrich_one,
                    enrich_client,
                    did,
                    lt,
                    classify_outcome_by_id[did],
                ): did
                for did, lt in tasks
            }
            done = 0
            for future in as_completed(futures):
                check_cancelled()
                try:
                    activity = future.result()
                except DynamicContentFetchError as exc:
                    if not defer_unavailable_content:
                        raise
                    deferred_ids.append(futures[future])
                    diagnostics.append(f"待重试 https://www.bilibili.com/opus/{futures[future]}：{exc}")
                    done += 1
                    if on_progress:
                        on_progress(done, enrich_total, f"动态 {futures[future]} 正文暂不可用，已保留待重试；继续详情")
                    continue
                except EnrichSkippedError:
                    skip_reasons[ENRICH_SKIP_REASON] = skip_reasons.get(ENRICH_SKIP_REASON, 0) + 1
                    done += 1
                    if on_progress:
                        on_progress(done, enrich_total, "详情进度")
                    continue
                except RuntimeError as exc:
                    if isinstance(exc, DynamicDeletedError) or is_enrich_detail_skip_error(exc):
                        diagnostics.append(f"已跳过 https://www.bilibili.com/opus/{futures[future]}：{exc}")
                        skip_reasons[ENRICH_SKIP_REASON] = skip_reasons.get(ENRICH_SKIP_REASON, 0) + 1
                        done += 1
                        if on_progress:
                            on_progress(done, enrich_total, "详情进度")
                        continue
                    raise
                if activity.skipped:
                    raise RuntimeError(f"活动 {activity.dynamic_id} 不应为 skipped")
                if not activity.repost_fetched and on_progress:
                    on_progress(done, enrich_total, f"活动 {activity.dynamic_id} 热度暂缺，已留空；继续入库")
                row = apply_initial_status(activity.to_dict())
                enriched_rows.append(row)
                done += 1
                if on_progress:
                    on_progress(done, enrich_total, "详情进度")

    if on_progress and enriched_rows:
        on_progress(0, len(enriched_rows), "正在写入活动库…")

    check_cancelled()
    persisted = append_activities(enriched_rows)
    if on_progress and enriched_rows:
        on_progress(
            persisted,
            max(1, len(enriched_rows)),
            f"入库完成，新增 {persisted} 条活动",
        )

    return PipelineResult(
        ok=True,
        pipeline_skipped=False,
        raw_link_count=len(raw_urls),
        new_link_count=len(dynamic_ids),
        classified_count=len(tasks),
        skipped_count=sum(skip_reasons.values()),
        enriched_count=len(enriched_rows),
        persisted_count=persisted,
        skip_reasons=skip_reasons,
        message=f"新入库 {persisted} 条活动" + (f"，{len(deferred_ids)} 条暂不可读，已保留待重试" if deferred_ids else ''),
        deferred_ids=deferred_ids,
        diagnostics=diagnostics,
    )


def run_refresh_all_pipeline(
    ds_results: list[CheckResult],
    *,
    workers: int = 4,
    on_progress: ProgressCallback | None = None,
) -> PipelineResult:
    """完整 refresh_all：Step 1 结果 → Step 2～5。"""
    from src.source_content_retry import include_pending, save_pending
    ds_results = [include_pending(result) for result in ds_results]
    if not any(result.updated for result in ds_results):
        return PipelineResult(
            ok=True,
            pipeline_skipped=True,
            raw_link_count=0,
            new_link_count=0,
            classified_count=0,
            skipped_count=0,
            enriched_count=0,
            persisted_count=0,
            message="所有数据源容器均未变更，流水线结束",
        )

    raw_links = _collect_updated_links(ds_results)
    result = run_new_links_pipeline(raw_links, workers=workers, on_progress=on_progress, defer_unavailable_content=True)
    save_pending(ds_results, result.deferred_ids)
    return result
