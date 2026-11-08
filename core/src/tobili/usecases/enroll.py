"""单个活动的报名闭环。

这是整个系统的信心的分水岭：能把一条真实活动从"解析"走到"做完并记账"，
剩下的都是工作量。

注意这里的 `dry_run` 不是"少做几步"，而是**只探测不写**。它和真正执行的唯一
区别在于调 `inspect` 还是 `attempt`，走的是同一套判断，所以预演结果可信。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..domain.action import Action, ActionKind
from ..domain.campaign import Campaign, CampaignKind
from ..domain.enrollment import IllegalTransition, Stage
from ..domain.extract import confidence_of, deadline_from, kind_from, reward_from
from ..ports.bili import Gateway
from ..ports.collab import Clock, Composer
from ..ports.repo import CampaignRepo, EnrollmentRepo
from ..runtime.journal import Journal, Outcome, Step
from ..runtime.quota import Quota


@dataclass(frozen=True, slots=True)
class EnrollReport:
    dyn_id: str
    campaign_id: int
    enrollment_id: int
    stage: Stage
    steps: tuple[tuple[str, Step], ...] = field(default=())

    @property
    def wrote_anything(self) -> bool:
        return any(step.wrote for _name, step in self.steps)


class Enroll:
    def __init__(
        self,
        *,
        gateway: Gateway,
        campaigns: CampaignRepo,
        enrollments: EnrollmentRepo,
        journal: Journal,
        clock: Clock,
        composer: Composer | None = None,
        quota: Quota | None = None,
        events=None,
    ) -> None:
        self._gateway = gateway
        self._campaigns = campaigns
        self._enrollments = enrollments
        self._journal = journal
        self._clock = clock
        self._composer = composer
        self._quota = quota
        self._events = events

    def run(
        self,
        account_id: int,
        dyn_id: str,
        *,
        dry_run: bool = False,
        include_like: bool = False,
    ) -> EnrollReport:
        if not dry_run and self._journal.preflight:
            self._journal.preflight(account_id)
        now = self._clock.now()
        local = self._campaigns.by_dyn(dyn_id)
        if local:
            stored = self._campaigns.get(local[0]) or {}
            expiry = stored.get("expires_at")
            if (
                not stored.get("deadline_at")
                and expiry
                and datetime.fromisoformat(str(expiry)) <= now
            ):
                from ..domain.errors import WriteUnavailable

                raise WriteUnavailable("这条活动已过开奖日期")
        if local and self._campaigns.shelved(account_id, local[0]):
            from ..domain.errors import WriteUnavailable

            raise WriteUnavailable("这条活动已移到往期架，请先取回")
        if local and self._campaigns.blocked(local[1].up_uid):
            from ..domain.errors import WriteUnavailable

            raise WriteUnavailable("该 UP 已列入黑名单，禁止参与")
        detail = self._gateway.detail(dyn_id)
        if self._campaigns.blocked(detail.up_uid):
            from ..domain.errors import WriteUnavailable

            raise WriteUnavailable("该 UP 已列入黑名单，禁止参与")
        text = detail.text

        kind = kind_from(text)
        published = detail.published_at or (local[1].published_at if local else None)
        reference = (published or now).astimezone(timezone(timedelta(hours=8)))
        deadline = deadline_from(text, now=reference, allow_relative=published is not None)
        campaign = Campaign(
            dyn_id=detail.dyn_id,
            up_uid=detail.up_uid,
            title=_headline(text),
            kind=kind,
            up_name=detail.up_name,
            reward=reward_from(text),
            deadline=deadline,
            confidence=confidence_of(text, kind, deadline),
            raw_ref=detail.raw_ref,
            seen_at=now,
            published_at=published,
            body_text=text,
        )
        campaign_id = self._campaigns.upsert(campaign)
        enrollment_id = self._enrollments.create(account_id, campaign_id, now=now)

        if not campaign.is_open(now):
            self._settle(enrollment_id, Stage.SETTLED, now, "活动已截止")
            return EnrollReport(detail.dyn_id, campaign_id, enrollment_id, Stage.SETTLED)

        actions = self._plan(detail, campaign, include_like=include_like)
        if not actions:
            self._settle(enrollment_id, Stage.BLOCKED, now, "没看出要做什么动作")
            return EnrollReport(detail.dyn_id, campaign_id, enrollment_id, Stage.BLOCKED)

        # 配额放在"已经确定要写"之后：预演不占用写额度，也不应该被额度挡住。
        if not dry_run and self._quota is not None:
            gate = self._quota.check(account_id)
            if not gate.allowed:
                self._settle(enrollment_id, Stage.BLOCKED, now, gate.explain())
                return EnrollReport(detail.dyn_id, campaign_id, enrollment_id, Stage.BLOCKED)

        self._settle(enrollment_id, Stage.RUNNING, now, "")
        results: list[tuple[str, Step]] = []
        for label, action in actions:
            step = (
                self._journal.inspect(account_id=account_id, action=action)
                if dry_run
                else self._journal.attempt(
                    account_id=account_id,
                    campaign_id=campaign_id,
                    action=action,
                    content=action.payload.get("text", ""),
                )
            )
            results.append((label, step))
            if not dry_run and step.outcome is Outcome.UNKNOWN:
                # 结果未知就该停手，继续做后面的动作只会让状态更乱。
                break

        stage = _stage_of(results, dry_run=dry_run)
        reason = _reason_of(results)
        self._settle(enrollment_id, stage, self._clock.now(), reason)
        return EnrollReport(
            dyn_id=detail.dyn_id,
            campaign_id=campaign_id,
            enrollment_id=enrollment_id,
            stage=stage,
            steps=tuple(results),
        )

    def _plan(self, detail, campaign: Campaign, *, include_like: bool):
        published = str(int(detail.published_at.timestamp())) if detail.published_at else ""
        base = {"published_at": published}
        draft = self._draft(campaign, detail)
        plan: list[tuple[str, Action]] = []

        if "关注" in detail.text and detail.up_uid:
            plan.append(
                (
                    "关注",
                    Action(ActionKind.FOLLOW, detail.dyn_id, {**base, "up_uid": detail.up_uid}),
                )
            )
        if campaign.kind is CampaignKind.SHARE:
            plan.append(("转发", Action(ActionKind.SHARE, detail.dyn_id, {**base, "text": draft})))
        if campaign.kind is CampaignKind.COMMENT:
            plan.append(
                (
                    "评论",
                    Action(
                        ActionKind.COMMENT,
                        detail.dyn_id,
                        {
                            **base,
                            "text": draft,
                            "rid": detail.rid or detail.dyn_id,
                            "comment_type": str(detail.comment_type or 17),
                        },
                    ),
                )
            )
        if campaign.kind is CampaignKind.RESERVE and detail.reserve_id:
            plan.append(
                (
                    "预约",
                    Action(
                        ActionKind.RESERVE,
                        detail.dyn_id,
                        {
                            **base,
                            "reserve_id": detail.reserve_id,
                            "reserve_total": str(detail.reserve_total),
                        },
                    ),
                )
            )
        if include_like and "点赞" in detail.text:
            plan.append(("点赞", Action(ActionKind.LIKE, detail.dyn_id, dict(base))))
        return plan

    def _draft(self, campaign: Campaign, detail=None) -> str:  # type: ignore[no-untyped-def]
        if self._composer is not None:
            text = self._composer.draft(
                action="share",
                campaign_title=campaign.title,
                up_name=campaign.up_name,
                dyn_id=campaign.dyn_id,
                # 正文给模型参考用。转发类是 orig 的正文（parser 已经归一化过）。
                body=getattr(detail, "text", "") or campaign.title,
            )
            if text.strip():
                return text.strip()
        return "转发抽奖"

    def _settle(self, enrollment_id: int, stage: Stage, now, reason: str) -> None:
        current = self._enrollments.get(enrollment_id)
        if current is None:
            return
        if self._events and stage in (Stage.BLOCKED, Stage.FAILED) and reason:
            campaign = self._campaigns.get(current.campaign_id) or {}
            self._events.publish(
                "participation.issue",
                {
                    "account_id": current.account_id,
                    "dyn_id": campaign.get("dyn_id", ""),
                    "title": campaign.get("reward") or campaign.get("title", ""),
                    "level": "error" if stage is Stage.FAILED else "warning",
                    "message": reason,
                },
            )
        if current.stage is stage:
            if reason:
                self._enrollments.move(enrollment_id, stage, now=now, reason=reason)
            return
        try:
            self._enrollments.move(enrollment_id, stage, now=now, reason=reason)
        except IllegalTransition:
            # 状态机拒绝的迁移说明流程有 bug，但不应让整轮失败；记录原因即可。
            self._enrollments.move(
                enrollment_id, Stage.FAILED, now=now, reason=f"非法迁移至 {stage.value}"
            )


def _stage_of(results: list[tuple[str, Step]], *, dry_run: bool) -> Stage:
    if dry_run:
        return Stage.PLANNED
    outcomes = {step.outcome for _name, step in results}
    if Outcome.UNKNOWN in outcomes or Outcome.SKIPPED_UNKNOWN in outcomes:
        return Stage.BLOCKED
    if Outcome.REJECTED in outcomes:
        return Stage.FAILED
    return Stage.SETTLED


def _reason_of(results: list[tuple[str, Step]]) -> str:
    for name, step in results:
        if step.outcome in {Outcome.SKIPPED_UNKNOWN, Outcome.REJECTED, Outcome.UNKNOWN}:
            return f"{name}：{step.message}"
    return ""


def _headline(text: str, limit: int = 80) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    return text.strip()[:limit]
