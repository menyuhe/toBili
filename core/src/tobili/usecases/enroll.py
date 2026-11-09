"""单个活动的报名闭环。

这是整个系统的信心的分水岭：能把一条真实活动从"解析"走到"做完并记账"，
剩下的都是工作量。

注意这里的 `dry_run` 不是"少做几步"，而是**只探测不写**。它和真正执行的唯一
区别在于调 `inspect` 还是 `attempt`，走的是同一套判断，所以预演结果可信。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from datetime import datetime, timedelta, timezone

from ..domain.action import Action, ActionKind
from ..domain.campaign import Campaign, CampaignKind
from ..domain.enrollment import IllegalTransition, Stage
from ..domain.errors import Fault, RemoteError
from ..domain.extract import confidence_of, deadline_from, kind_from, reward_from
from ..domain.participation import manual_requirement, required_interactions
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
    skipped: bool = False
    reason: str = ""

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
        standard_actions: bool = False,
    ) -> None:
        self._gateway = gateway
        self._campaigns = campaigns
        self._enrollments = enrollments
        self._journal = journal
        self._clock = clock
        self._composer = composer
        self._quota = quota
        self._events = events
        self._standard_actions = standard_actions

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
        if local and getattr(self._campaigns,'removed',lambda _:False)(dyn_id):
            eid=self._enrollments.create(account_id,local[0],now=now)
            self._settle(eid,Stage.BLOCKED,now,'该活动已从奖品目录删除')
            return EnrollReport(dyn_id,local[0],eid,Stage.BLOCKED,skipped=True,reason='该活动已从奖品目录删除')
        if local:
            stored = self._campaigns.get(local[0]) or {}
            expiry = stored.get("expires_at")
            if stored.get('draw_state')=='ended' or (expiry and datetime.fromisoformat(str(expiry)) <= now):
                eid=self._enrollments.create(account_id,local[0],now=now)
                reason=str(stored.get('draw_reason') or '活动奖品已截止')
                self._settle(eid,Stage.BLOCKED,now,reason)
                return EnrollReport(dyn_id,local[0],eid,Stage.BLOCKED,skipped=True,reason=reason)
        if local and self._campaigns.shelved(account_id, local[0]):
            from ..domain.errors import WriteUnavailable

            raise WriteUnavailable("这条活动已移到往期架，请先取回")
        if local and self._campaigns.blocked(local[1].up_uid):
            from ..domain.errors import WriteUnavailable

            raise WriteUnavailable("该 UP 已列入黑名单，禁止参与")
        try:
            detail = self._gateway.detail(dyn_id)
        except RemoteError as exc:
            if not local or exc.fault not in (Fault.REMOTE_BUSINESS, Fault.PARSE):
                raise
            eid = self._enrollments.create(account_id, local[0], now=now)
            current = self._enrollments.get(eid)
            if current and current.stage is Stage.FAILED:
                self._settle(eid, Stage.RUNNING, now, "")
            self._settle(eid, Stage.BLOCKED, now, str(exc))
            return EnrollReport(dyn_id, local[0], eid, Stage.BLOCKED, reason=str(exc))
        if self._campaigns.blocked(detail.up_uid):
            from ..domain.errors import WriteUnavailable

            raise WriteUnavailable("该 UP 已列入黑名单，禁止参与")
        text = detail.text
        from ..domain.participation import is_roundup
        if detail.draw is None and is_roundup(text):
            from ..domain.draw import DrawFacts
            from dataclasses import replace
            detail = replace(detail, draw=DrawFacts(state='unsupported', reason='抽奖汇总文章，请进入所列原动态参与'))
        if local and stored.get('draw_business_type') in (1,10,12) and detail.draw is None:
            from ..domain.draw import DrawFacts
            from dataclasses import replace
            detail=replace(detail,draw=DrawFacts(str(stored.get('draw_business_id') or ''),
                int(stored['draw_business_type']),state='unverified',reason='原官方抽奖组件未能读取，未按普通活动参与'))
        if not detail.draw and re.search(r'已(?:经)?开奖|开奖公示|(?:获奖|中奖)名单\s*(?:如下|公布|公示|[：:])|抽奖已结束',text):
            from ..domain.draw import DrawFacts
            from dataclasses import replace
            detail=replace(detail,draw=DrawFacts(state='ended',reason='正文显示已开奖或公布获奖名单'))

        draw=detail.draw
        kind = CampaignKind.RESERVE if draw and draw.business_type==10 else CampaignKind.SHARE if draw and draw.business_type==1 else kind_from(text)
        published = detail.published_at or (local[1].published_at if local else None)
        reference = (published or now).astimezone(timezone(timedelta(hours=8)))
        deadline = detail.deadline or deadline_from(text, now=reference, allow_relative=published is not None) or (local[1].deadline if local else None)
        if draw is None and deadline and deadline<=now:
            from ..domain.draw import DrawFacts
            draw=DrawFacts(deadline=deadline,state='ended',reason='正文开奖时间已过')
        campaign = Campaign(
            dyn_id=detail.dyn_id,
            up_uid=detail.up_uid,
            title=_headline(text),
            kind=kind,
            up_name=detail.up_name,
            reward=draw.reward if draw and draw.reward else reward_from(text),
            deadline=deadline,
            confidence=confidence_of(text, kind, deadline),
            raw_ref=detail.raw_ref,
            seen_at=now,
            published_at=published,
            body_text=text,
            draw=draw,
        )
        campaign_id = self._campaigns.upsert(campaign)
        enrollment_id = self._enrollments.create(account_id, campaign_id, now=now)

        if draw and not draw.verified and draw.state!='ended':
            reason=draw.reason or '官方抽奖状态待核实，未参与'
            self._settle(enrollment_id,Stage.BLOCKED,now,reason)
            return EnrollReport(detail.dyn_id,campaign_id,enrollment_id,Stage.BLOCKED,reason=reason)

        if not campaign.is_open(now):
            reason=draw.reason if draw and draw.reason else '活动奖品已截止'
            self._settle(enrollment_id, Stage.BLOCKED, now, reason)
            return EnrollReport(detail.dyn_id, campaign_id, enrollment_id, Stage.BLOCKED, skipped=True,reason=reason)

        unsupported = manual_requirement(text, detail.comment_type)
        if self._standard_actions and not detail.up_uid:
            unsupported = '缺少作者 UID，无法核实关注，未开始参与'
        elif self._standard_actions and detail.up_uid == self._gateway.whoami():
            unsupported = '这是本账号发布的内容，不参与自己的抽奖'
        if kind is CampaignKind.CHECK_IN:
            unsupported = "打卡活动需要手动完成指定任务，请查看原动态"
        elif kind is CampaignKind.RESERVE and not detail.reserve_id:
            unsupported = "未找到可执行的预约入口，请查看原动态"
        elif kind is CampaignKind.UNKNOWN:
            unsupported = "未识别到可自动执行的参与方式，请查看原动态"
        if unsupported:
            self._settle(enrollment_id, Stage.BLOCKED, now, unsupported)
            return EnrollReport(
                detail.dyn_id, campaign_id, enrollment_id, Stage.BLOCKED, reason=unsupported
            )

        actions = self._plan(detail, campaign, include_like=include_like, draft_text=not dry_run)
        if not actions:
            self._settle(enrollment_id, Stage.BLOCKED, now, "未识别到可自动执行的参与方式，请查看原动态")
            return EnrollReport(detail.dyn_id, campaign_id, enrollment_id, Stage.BLOCKED, reason="未识别到可自动执行的参与方式，请查看原动态")

        # 配额放在"已经确定要写"之后：预演不占用写额度，也不应该被额度挡住。
        if not dry_run and self._quota is not None:
            gate = self._quota.check(account_id)
            if not gate.allowed:
                self._settle(enrollment_id, Stage.BLOCKED, now, gate.explain())
                return EnrollReport(detail.dyn_id, campaign_id, enrollment_id, Stage.BLOCKED, reason=gate.explain())

        self._settle(enrollment_id, Stage.RUNNING, now, "")
        results: list[tuple[str, Step]] = []
        step_index = 0
        total_steps = len(actions) + sum(action.kind is ActionKind.SHARE for _,action in actions)
        for label, action in actions:
            if not campaign.is_open(self._clock.now()):
                self._settle(enrollment_id,Stage.BLOCKED,self._clock.now(),'执行期间已到截止时间，后续动作未发送')
                return EnrollReport(detail.dyn_id,campaign_id,enrollment_id,Stage.BLOCKED,tuple(results),reason='执行期间已到截止时间，后续动作未发送')
            step_index += 1
            self._step_event(account_id, dyn_id, label, step_index, total_steps)
            step = (
                self._journal.inspect(account_id=account_id, campaign_id=campaign_id, action=action)
                if dry_run
                else self._journal.attempt(
                    account_id=account_id,
                    campaign_id=campaign_id,
                    action=action,
                    content=action.payload.get("text", ""),
                )
            )
            results.append((label, step))
            if action.kind is ActionKind.SHARE:
                step_index += 1
                self._step_event(account_id, dyn_id, '核对本人转发', step_index, total_steps, step.message)
            if not dry_run and step.outcome in (Outcome.UNKNOWN, Outcome.SKIPPED_UNKNOWN, Outcome.REJECTED):
                # 结果未知就该停手，继续做后面的动作只会让状态更乱。
                break

        stage = _stage_of(results, dry_run=dry_run)
        reason = _reason_of(results)
        self._settle(enrollment_id, stage, self._clock.now(), reason)
        # Persist the item outcome before handing account-wide failures to the scheduler.
        for _, step in results:
            if step.fault in (Fault.AUTH, Fault.RISK, Fault.RATE_LIMIT):
                raise RemoteError(step.fault, reason)
        return EnrollReport(
            dyn_id=detail.dyn_id,
            campaign_id=campaign_id,
            enrollment_id=enrollment_id,
            stage=stage,
            steps=tuple(results),
            reason=reason,
        )

    def recheck(self, account_id: int, dyn_id: str) -> EnrollReport:
        """Re-evaluate a legacy blocked row once without composing or sending text."""
        return self.run(account_id, dyn_id, dry_run=True)

    def _plan(self, detail, campaign: Campaign, *, include_like: bool, draft_text: bool = True):
        published = str(int(detail.published_at.timestamp())) if detail.published_at else ""
        base = {"published_at": published}
        share, comment, like = required_interactions(detail.text, campaign.kind)
        ordinary = self._standard_actions and campaign.kind in (CampaignKind.SHARE, CampaignKind.COMMENT)
        if ordinary:
            share = comment = like = True
        draft = self._draft(campaign, detail) if draft_text and (share or comment) else ""
        plan: list[tuple[str, Action]] = []

        if self._standard_actions or like or (include_like and "点赞" in detail.text):
            plan.append(("点赞", Action(ActionKind.LIKE, detail.dyn_id, dict(base))))
        if (self._standard_actions or "关注" in detail.text) and detail.up_uid:
            plan.append(
                (
                    "关注",
                    Action(ActionKind.FOLLOW, detail.dyn_id, {**base, "up_uid": detail.up_uid}),
                )
            )
        if ordinary:
            plan.append(('收藏', Action(ActionKind.FAVORITE,detail.dyn_id,
                {**base,'target':'opus','optional':'0' if '收藏' in detail.text else '1'})))
        if share:
            plan.append(("转发", Action(ActionKind.SHARE, detail.dyn_id, {**base, "text": draft})))
        if comment:
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
        return plan

    def _step_event(self, owner, dyn_id, label, index, total, message=''):
        if self._events:
            self._events.publish('participation.step', dict(account_id=owner,dyn_id=dyn_id,
                message=f'{index}/{total} {label}' + (f' · {message}' if message else '')))

    def _draft(self, campaign: Campaign, detail=None) -> str:  # type: ignore[no-untyped-def]
        from ..domain.participate_text import required_comment_text
        literal = required_comment_text(getattr(detail, "text", ""))
        if literal:
            return literal
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
        if all(step.fault in (None, Fault.REMOTE_BUSINESS, Fault.PARSE)
               for _, step in results if step.outcome is Outcome.REJECTED):
            return Stage.BLOCKED
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
