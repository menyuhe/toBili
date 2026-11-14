"""开奖核验。

对每一条"已过截止时间、还没结算"的报名，去 UP 的空间动态里找开奖公告，交给
`domain.result.decide` 判断，结果写进 verdict 并把报名推进到已结算。

这条链路**只读不写**：核验本身不产生任何对外操作。最坏情况是判错了需要人工纠正，
不会造成不可恢复的损失 —— 真正的破坏力在下游的清理环节，那里必须人工确认。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ..domain.enrollment import Stage
from ..domain.errors import Fault, MissingCredential, RemoteError
from ..domain.result import Announcement, Decision, decide, official_decision
from ..domain.tidy import Outcome
from ..ports.bili import Gateway
from ..ports.collab import Clock
from ..ports.models import AccountProfile
from ..ports.repo import EnrollmentRepo, VerdictRepo
from ..runtime.redact import safe_exception


@dataclass(frozen=True, slots=True)
class Settled:
    enrollment_id: int
    dyn_id: str
    up_name: str
    outcome: Outcome
    evidence: str


@dataclass(frozen=True, slots=True)
class SettleReport:
    checked: int = 0
    decided: int = 0
    items: tuple[Settled, ...] = field(default=())
    problems: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass(frozen=True, slots=True)
class OverrideReport:
    campaign_id: int
    outcome: Outcome
    previous: Outcome | None
    note: str
    was_manual: bool


class SettleRejected(ValueError):
    """改判请求本身不成立。和"改判失败"区分开：这里是调用方给的东西不对。"""


class Settle:
    def __init__(
        self,
        *,
        gateway: Gateway,
        enrollments: EnrollmentRepo,
        verdicts: VerdictRepo,
        clock: Clock,
        grace_hours: int = 12,
        recent: int = 20,
        settings=None,
        events=None,
    ) -> None:
        self._gateway = gateway
        self._enrollments = enrollments
        self._verdicts = verdicts
        self._clock = clock
        self._grace_hours = grace_hours
        self._recent = recent
        self._settings = settings
        self._events = events

    def run(
        self,
        account_id: int,
        *,
        profile: AccountProfile,
        limit: int = 20,
        dry_run: bool = True,
    ) -> SettleReport:
        report = self._run(account_id, profile=profile, limit=limit, dry_run=dry_run)
        if self._events:
            self._events.publish(
                "settle",
                {
                    "account_id": account_id,
                    "checked": report.checked,
                    "decided": report.decided,
                    "problems": list(report.problems),
                    "preview": dry_run,
                    "title": f"开奖核验 · 检查 {report.checked} 条，确认 {report.decided} 条"
                    + (" · " + report.problems[0] if report.problems else ""),
                },
            )
        return report

    def _run(self, account_id, *, profile, limit, dry_run):
        now = self._clock.now()
        if self._settings:
            until = float(self._settings.get(account_id, "results.cooldown", "0"))
            if until > now.timestamp():
                return SettleReport(
                    problems=(
                        f"开奖接口冷却中，{int(until - now.timestamp()) // 60 + 1} 分钟后重试",
                    )
                )
        rows = list(self._enrollments.settleable(account_id, before=now))[: max(1, min(30, limit))]

        checked = decided = 0
        items: list[Settled] = []
        problems: list[str] = []
        spaces = {}

        for row in rows:
            up_uid = str(row.get("up_uid") or "")
            label = str(row.get("up_name") or up_uid)
            official = getattr(self._gateway, "lottery_notice", None)
            try:
                business_id = str(row.get("draw_business_id") or "")
                business_type = int(row.get("draw_business_type") or 0)
                if official and not business_id:
                    if row.get("kind") == "reserve":
                        business_id = self._gateway.detail(str(row["dyn_id"])).reserve_id
                        business_type = 10
                    elif row.get("kind") == "interactive":
                        business_id, business_type = str(row["dyn_id"]), 1
                notice = (
                    official(business_id, business_type)
                    if official and business_id and business_type in (1, 10)
                    else None
                )
                if notice is not None:
                    decision = official_decision(notice, profile.uid)
                else:
                    if not up_uid:
                        problems.append(f"{row.get('dyn_id')}：缺少 UP 信息")
                        # 没有 UP 就永远定位不到公告，再查一百次也是同一句结论。
                        self._record_check(
                            account_id, row, now, dry_run, error="缺少 UP 信息", terminal=True
                        )
                        continue
                    if up_uid not in spaces:
                        spaces[up_uid] = self._gateway.scan(
                            kind="space", target=up_uid, cursor="", limit=self._recent
                        )
                    page = spaces[up_uid]
                    announcements = tuple(
                        Announcement(
                            dyn_id=item.dyn_id,
                            text=item.text,
                            published_at=item.published_at,
                            media=len(item.attachments),
                        )
                        for item in page.items
                        if not official
                        or str(row["dyn_id"]) in item.text
                        or item.dyn_id == row["dyn_id"]
                    )
                    decision = decide(
                        announcements,
                        uid=profile.uid,
                        nickname=profile.nickname,
                        now=now,
                        grace_hours=self._grace_hours,
                    )
                    if official and decision.outcome is Outcome.LOST:
                        decision = Decision(Outcome.PENDING, "文字公告不能确认完整名单，待人工核对")
            except Exception as exc:
                error = f"{label}：{safe_exception(exc)}"
                problems.append(error)
                self._record_check(account_id, row, now, dry_run, error=error)
                shared_failure = isinstance(exc, MissingCredential) or (
                    isinstance(exc, RemoteError)
                    and (
                        exc.fault
                        in {Fault.AUTH, Fault.RISK, Fault.RATE_LIMIT, Fault.NETWORK, Fault.TIMEOUT}
                        or exc.code in {412, 429}
                    )
                )
                if shared_failure:
                    if self._settings:
                        self._settings.put(
                            account_id, "results.cooldown", str(now.timestamp() + 600)
                        )
                    problems[-1] += "；本轮已停止，10 分钟后重试"
                    break
                continue
            checked += 1
            # 公告里写着还没到开奖时间（预约类最常见）的时候，别按常规节奏反复问：
            # 直接排到开奖之后再看，中间这段时间问了也只会得到同一句"名单未发布"。
            ready_at = None
            if notice is not None and decision.outcome is Outcome.PENDING:
                draw_at = notice.get("lottery_time")
                if isinstance(draw_at, (int, float)) and draw_at > now.timestamp() + 300:
                    ready_at = datetime.fromtimestamp(draw_at, UTC) + timedelta(minutes=30)
            self._record_check(account_id, row, now, dry_run, not_before=ready_at)
            if decision.outcome is Outcome.PENDING:
                if official and not dry_run:
                    self._verdicts.record(
                        campaign_id=int(row["campaign_id"]),
                        account_id=account_id,
                        outcome=decision.outcome,
                        decided_at=now,
                        evidence=decision.evidence,
                    )
                items.append(
                    Settled(
                        enrollment_id=int(row["enrollment_id"]),
                        dyn_id=str(row["dyn_id"]),
                        up_name=label,
                        outcome=decision.outcome,
                        evidence=decision.evidence,
                    )
                )
                continue

            decided += 1
            kept_manual = False
            if not dry_run:
                written = self._verdicts.record(
                    campaign_id=int(row["campaign_id"]),  # type: ignore[arg-type]
                    account_id=account_id,
                    outcome=decision.outcome,
                    decided_at=now,
                    evidence=decision.evidence,
                )
                # 写不进去只有一个原因：这条已经被人改判过。不能当成功往下走 ——
                # 界面会显示"已核验为未中奖"，而库里其实是"中奖"。
                kept_manual = not written
                enrollment_id = int(row["enrollment_id"])  # type: ignore[arg-type]
                if enrollment_id and not kept_manual:
                    self._enrollments.move(
                        enrollment_id,
                        Stage.SETTLED,
                        now=now,
                        reason=f"开奖：{decision.outcome.value}",
                    )
            items.append(
                Settled(
                    enrollment_id=int(row["enrollment_id"]),  # type: ignore[arg-type]
                    dyn_id=str(row.get("dyn_id") or ""),
                    up_name=label,
                    outcome=decision.outcome,
                    evidence=(
                        "已有人工改判，本次自动结论未生效" if kept_manual else decision.evidence
                    ),
                )
            )

        return SettleReport(
            checked=checked,
            decided=decided,
            items=tuple(items),
            problems=tuple(problems),
        )

    def _record_check(self, owner, row, now, dry_run, error="", *, terminal=False, not_before=None):
        record = getattr(self._enrollments, "record_check", None)
        if record and not dry_run:
            record(
                owner,
                int(row["campaign_id"]),
                now=now,
                error=error,
                terminal=terminal,
                not_before=not_before,
            )

    def override(
        self,
        account_id: int,
        *,
        campaign_id: int,
        outcome: Outcome,
        note: str = "",
    ) -> OverrideReport:
        """人工改判。

        自动核验只读正文，判错是必然的：名单只有图片、昵称和别人重名、公告写在
        评论区。而判错的后果是下游真的去删一条其实中奖的转发 —— 这是整个系统里
        唯一会造成不可恢复损失的路径，所以人必须能改，而且改了要留住。

        写成 `source='manual'` 之后，后续自动核验的 upsert 会被 `WHERE` 挡掉，
        不会把人的结论改回去。
        """
        if outcome is Outcome.PENDING:
            raise SettleRejected(
                "不能改判成「待定」—— 待定的意思是还没判，不是一种结论。"
                "如果想让自动核验重新判，改回它原本的结论即可。"
            )
        previous = self._verdicts.detail(campaign_id, account_id)
        now = self._clock.now()
        written = self._verdicts.record(
            campaign_id=campaign_id,
            account_id=account_id,
            outcome=outcome,
            decided_at=now,
            evidence=f"人工改判：{note}" if note else "人工改判",
            source="manual",
            note=note,
        )
        if not written:  # 理论上人工写入永远成功；失败说明库有问题，不能当成功
            raise SettleRejected("改判没有写进库里，请重试")

        # 报名推进到已结算。否则这条活动停在 planned，界面上看不出它已经有结论。
        for enrollment in self._enrollments.by_campaign(campaign_id):
            if enrollment.account_id == account_id:
                self._enrollments.move(
                    enrollment.id,
                    Stage.SETTLED,
                    now=now,
                    reason=f"人工改判：{outcome.value}",
                )
                break

        return OverrideReport(
            campaign_id=campaign_id,
            outcome=outcome,
            previous=(
                Outcome(str(previous["outcome"])) if previous else None  # type: ignore[arg-type]
            ),
            note=note,
            was_manual=bool(previous and previous["source"] == "manual"),
        )
