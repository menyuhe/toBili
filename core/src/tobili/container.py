"""组合根。

所有"具体实现"在这里被拼起来。它不属于任何一层，存在的意义是让上层（api、cli、
desktop）都从这里拿同一套装配好的对象，而不是各自 new 一遍。
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from datetime import UTC, timedelta

from .adapters.bili import BiliGateway, QrAuthenticator, Wire
from .adapters.llm.openai import OpenAICompatible
from .adapters.notify.dingtalk import DingTalk
from .adapters.sqlite import Database, Repositories
from .adapters.sqlite.eventsink import SqliteEventSink
from .adapters.vault import FileVault
from .config import Paths, resolve
from .domain.errors import MissingCredential
from .domain.session import LEGACY_SESSION, session_key
from .domain.tidy import TidyPolicy
from .ports.bili import Reader
from .ports.llm import Llm
from .ports.models import AccountProfile, QrTicket
from .runtime import EventBus, Journal, Lanes, SystemClock
from .runtime.gate import CleaningGate
from .runtime.quota import Quota
from .runtime.schedule import Schedule
from .usecases.batch_enroll import BatchEnroll
from .usecases.discover import DiscoverSources
from .usecases.enroll import Enroll
from .usecases.execute_tidy import ExecuteTidy
from .usecases.housekeeping import Housekeeping
from .usecases.inbox import Inbox
from .usecases.manage_feeds import ManageFeeds
from .usecases.notify import DINGTALK_SECRET, K_WEBHOOK, Notify
from .usecases.participate_text import (
    K_LLM_BASE,
    K_LLM_MODEL,
    LLM_KEY,
    ParticipateText,
)
from .usecases.pipeline import Pipeline
from .usecases.plan_tidy import PlanTidy
from .usecases.pool import ManagePool, RunPool
from .usecases.scan_feeds import ScanFeeds
from .usecases.settle import Settle
from .usecases.sign_in import QrSignIn

# 保留多少份执行前快照。20 份按"每次清理执行一份"算，够覆盖几个月的回溯；
# 每份就是当前库的大小（本机约 1~2MB），不会失控。
BACKUPS_KEPT = 20


class Container:
    def __init__(self, home: str | None = None) -> None:
        self.paths: Paths = resolve(home)
        self.vault = FileVault(self.paths.vault)
        from .adapters.avatar import AvatarCache

        self.avatars = AvatarCache(self.paths.home / "cache" / "avatars")
        self.db = Database(self.paths.database)
        self.db.migrate()
        self.repos = Repositories(self.db)
        from .usecases.participation_repair import reclassify_checkins, repair_legacy_likes
        reclassify_checkins(self)
        repair_legacy_likes(self)
        self.clock = SystemClock()
        # 序号从库里接上，重启后 SSE 的 Last-Event-ID 才不会把新事件全丢掉。
        self.bus = EventBus(
            start_seq=self.repos.events.last_seq(),
            sink=SqliteEventSink(self.repos.events),
        )
        self.lanes = Lanes(self.paths.lanes)
        self.pool_progress: dict[int, str] = {}
        self.source_progress: dict[int, dict] = {}

        self._guard = threading.Lock()
        self._model_gate = threading.Lock()
        # account_id -> (cookie, gateway)。切号不关别人的连接：并行作业还在用。
        self._gateways: dict[int, tuple[str, BiliGateway]] = {}
        self._tickets: dict[str, QrTicket] = {}
        self._clocks: dict[int, Schedule] = {}
        from .runtime.taskbook import TaskBook

        self.tasks = TaskBook(self)
        from importlib.util import find_spec

        self._write_scope = None
        self.check_writer = None
        if find_spec("tobili.private_pack") is not None:
            from importlib import import_module

            plugin = import_module("tobili.coordination")
            self._write_scope = plugin.write_scope
            self.check_writer = lambda owner: plugin.check_writer(self, owner)
            from .block_sync import change

            self.change_block = lambda uid, name, reason, active: change(
                self, uid, name, reason, active
            )
            from .revisions import pin_reward

            self.pin_reward = pin_reward
        self._migrate_session()

    # ---- 凭据 ----
    # 一个账号一份 cookie。保险库里的键带 uid（见 domain.session），
    # account.credential_ref 记的就是这个键 —— 所以删账号只要删一行加一个文件。
    def cookie(self, account_id: int | None = None) -> str:
        target = self._resolve_account(account_id)
        ref = self.repos.accounts.credential_ref(target)
        value = self.vault.read(ref) if ref else None
        if not value:
            raise MissingCredential("这个账号还没有凭据，请重新扫码登录")
        return value

    def has_session(self, account_id: int | None = None) -> bool:
        try:
            return bool(self.cookie(account_id))
        except Exception:
            return False

    def drop_session(self, account_id: int | None = None) -> None:
        """退出某个账号：删掉它的 cookie，并把它从"可用账号"里摘出去。

        数据的去留交给调用方决定 —— 这里只负责"这个号不能再用了"。
        """
        target = self._resolve_account(account_id)
        if target in self._clocks:
            self._clocks[target].stop()
        ref = self.repos.accounts.credential_ref(target)
        if ref:
            self.vault.erase(ref)
        self.repos.accounts.set_credential_ref(target, "")
        self.repos.accounts.disable(target)
        with self._guard:
            slot = self._gateways.pop(target, None)
            if slot is not None:
                slot[1].close()

    # ---- 网关 ----
    def gateway_for(self, account_id: int | None = None) -> BiliGateway:
        """某个账号的网关。

        按账号缓存，不做成单例：一轮作业会并行跑多个账号，共用一个实例就是
        拿 A 的 cookie 去写 B 的账号 —— 这是那种"看起来都成功、结果全在别人名下"
        的错误，事后极难追溯。
        """
        target = self._resolve_account(account_id)
        cookie = self.cookie(target)
        with self._guard:
            slot = self._gateways.get(target)
            if slot is None or slot[0] != cookie:
                if slot is not None:
                    slot[1].close()
                gateway = BiliGateway(Wire(cookie))
                from .adapters.sqlite.source_cache import SourceCache

                gateway._transport.source_cache = SourceCache(self.db, target)
                self._gateways[target] = (cookie, gateway)
                return gateway
            return slot[1]

    def gateway(self) -> BiliGateway:
        return self.gateway_for(None)

    def connect(self, cookie: str) -> Reader:
        return BiliGateway(Wire(cookie))

    def authenticator(self) -> QrAuthenticator:
        return QrAuthenticator()

    # ---- 用例 ----
    def sign_in(self) -> QrSignIn:
        return QrSignIn(
            auth=self.authenticator(),
            vault=self.vault,
            accounts=self.repos.accounts,
            connect=self.connect,
        )

    def scan_feeds(self, account_id: int | None = None) -> ScanFeeds:
        from .usecases.scan_model import ScanModel

        llm = self.llm() if self.repos.settings.get(0, "llm.scan", "0") == "1" else None
        return ScanFeeds(
            model=ScanModel(llm, self.db) if llm and llm.ready() else None,
            gateway=self.gateway_for(account_id),
            feeds=self.repos.feeds,
            campaigns=self.repos.campaigns,
            clock=self.clock,
            events=self.bus,
        )

    def manage_feeds(self) -> ManageFeeds:
        return ManageFeeds(self.repos.feeds)

    def llm(self) -> Llm:
        """按当前设置构造一个大模型客户端。每次调用都重读设置，改完立刻生效。"""
        account = 0
        from .runtime.model_dispatch import ModelDispatch

        client = OpenAICompatible(
            base_url=self.repos.settings.get(account, K_LLM_BASE, ""),
            api_key=self.vault.read(LLM_KEY) or "",
            model=self.repos.settings.get(account, K_LLM_MODEL, ""),
        )
        return ModelDispatch(client, self.repos.settings, self._model_gate, self.bus)

    def text(self, account_id: int | None = None) -> ParticipateText:
        return ParticipateText(
            gateway=self.gateway_for(account_id),
            settings=self.repos.settings,
            vault=self.vault,
            llm=self.llm(),
        )

    def discover(self, account_id: int | None = None) -> DiscoverSources:
        return DiscoverSources(gateway=self.gateway_for(account_id), feeds=self.manage_feeds())

    def batch_enroll(self, account_id: int | None = None) -> BatchEnroll:
        return BatchEnroll(
            campaigns=self.repos.campaigns,
            enroll=self._enroll(account_id),
            clock=self.clock,
            quota=self.quota(account_id),
        )

    def _enroll(self, account_id: int | None = None) -> Enroll:
        account_id = self._resolve_account(account_id)
        gateway = self.gateway_for(account_id)
        return Enroll(
            gateway=gateway,
            campaigns=self.repos.campaigns,
            enrollments=self.repos.enrollments,
            journal=self.journal(gateway, account_id),
            clock=self.clock,
            quota=self.quota(account_id),
            composer=self.text(account_id),
            events=self.bus,
            standard_actions=True,
        )

    def enroll(self) -> Enroll:
        return self._enroll()

    def notify(self) -> Notify:
        """通知通道。webhook 在设置里，加签密钥在保险库 —— 每次现装，
        改完配置立刻生效，不用重启。"""
        state = Notify(
            settings=self.repos.settings,
            vault=self.vault,
        ).state()
        channel = None
        if state.has_webhook:
            channel = DingTalk(
                self.repos.settings.get(0, K_WEBHOOK, ""),
                secret=self.vault.read(DINGTALK_SECRET) or "",
            )
        return Notify(settings=self.repos.settings, vault=self.vault, channel=channel)

    def housekeeping(self) -> Housekeeping:
        """清掉过期的历史。daemon 每轮顺手调一次。"""
        return Housekeeping(events=self.repos.events, inbox=self.repos.inbox, clock=self.clock)

    def inbox(self, account_id: int | None = None) -> Inbox:
        return Inbox(
            gateway=self.gateway_for(account_id),
            seen=self.repos.inbox,
            clock=self.clock,
        )

    # ---- 定时自动化 ----
    # 配置存全局设置（account_id=0），因为它管的是"这些账号这个进程"。
    K_SCHEDULE_ON = "schedule.enabled"
    K_SCHEDULE_MIN = "schedule.interval"
    K_SCHEDULE_APPLY = "schedule.apply"

    def schedule(self, owner: int) -> Schedule:
        with self._guard:
            if owner not in self._clocks:
                settings = self.repos.settings

                def save(minutes: int, apply: bool, running: bool) -> None:
                    settings.put(owner, self.K_SCHEDULE_MIN, str(minutes))
                    settings.put(owner, self.K_SCHEDULE_APPLY, str(int(apply)))
                    settings.put(owner, self.K_SCHEDULE_ON, str(int(running)))

                timer = Schedule(
                    runner=lambda apply: self.scheduled_round(owner, apply, self.scope(owner)),
                    clock=self.clock,
                    on_save=save,
                )
                timer._state.interval_min = int(settings.get(owner, self.K_SCHEDULE_MIN, "60"))
                timer._state.apply = settings.get(owner, self.K_SCHEDULE_APPLY, "0") == "1"
                self._clocks[owner] = timer
            return self._clocks[owner]

    def scope(self, owner: int) -> str:
        return self.repos.settings.get(owner, "schedule.selection", "queue")

    def resume_schedule(self) -> None:
        # 全局旧计划没有账号授权，升级后不自动迁移成真写计划。
        for owner in self.repos.accounts.ids():
            if (
                self.has_session(owner)
                and self.repos.settings.get(owner, self.K_SCHEDULE_ON, "0") == "1"
            ):
                timer = self.schedule(owner)
                state = timer.status()
                timer.resume(interval_min=state.interval_min, apply=state.apply)

    def scheduled_round(self, owner: int, apply: bool, selection: str) -> dict[str, object]:
        if not self.has_session(owner):
            raise MissingCredential("本账号没有可用凭据")
        ok = True
        if apply and self.check_writer:
            self.check_writer(owner)
        if selection == "queue":
            with self.lanes.exclusive(owner):
                pool = self.manage_pool().view(owner)
                if pool.paused or not pool.counts.get("pending"):
                    note = "计划单已暂停或没有待办"
                    written = scanned = 0
                else:
                    result = self.run_pool(owner).run(owner, apply=apply, limit=10)
                    ok = result.failed == 0
                    scanned, written, note = (
                        result.attempted,
                        result.joined if apply else 0,
                        result.note,
                    )
        else:
            result = self.pipeline().run(
                [owner], pages=1, per_feed=20, limit=10, floor=0.8, apply=apply
            )
            item = result.accounts[0]
            ok = not (item.problems or item.failed)
            scanned, written = item.scanned, item.settled if apply else 0
            note = (
                item.skipped
                or "；".join(item.problems)
                or f"候选 {item.candidates} 条，写入 {written} 条"
            )
        self.bus.publish("schedule.round", {"account_id": owner, "title": note})
        return {"ok": ok, "scanned": scanned, "written": written, "note": note}

    def manage_pool(self) -> ManagePool:
        return ManagePool(
            pool=self.repos.pool,
            campaigns=self.repos.campaigns,
            enrollments=self.repos.enrollments,
            clock=self.clock,
        )

    def run_pool(self, owner: int | None = None) -> RunPool:
        def progress(account_id, note):
            self.pool_progress[account_id] = note

        return RunPool(
            pool=self.repos.pool,
            # 传工厂而不是实例：读凭据会抛 MissingCredential，而"队列空着"这件事
            # 不需要凭据，不该被报成"未登录"。
            enroll=lambda: self._enroll(owner),
            clock=self.clock,
            quota=self.quota(owner),
            progress=progress,
            events=self.bus,
        )

    def pipeline(self) -> Pipeline:
        """一轮作业。

        传**工厂**而不是装配好的实例：一轮里要并行跑多个账号，每个账号得有自己的
        网关。传实例就等于让所有账号共用第一个账号的 cookie。
        """
        return Pipeline(
            scanner_for=self.scan_feeds,
            batch_for=self.batch_enroll,
            lanes=self.lanes,
        )

    def settle(self, account_id: int | None = None) -> Settle:
        return Settle(
            gateway=self.gateway_for(account_id),
            enrollments=self.repos.enrollments,
            verdicts=self.repos.verdicts,
            clock=self.clock,
            settings=self.repos.settings,
            events=self.bus,
        )

    def plan_tidy(self) -> PlanTidy:
        return PlanTidy(
            verdicts=self.repos.verdicts,
            tidy=self.repos.tidy,
            clock=self.clock,
            keep=self.repos.keep,
        )

    def cleaning_gate(self, account_id: int | None = None) -> CleaningGate:
        return CleaningGate(
            gateway=self.gateway_for(account_id),
            intents=self.repos.intents,
            settings=self.repos.settings,
            clock=self.clock,
        )

    def execute_tidy(self, account_id: int | None = None) -> ExecuteTidy:
        account_id = self._resolve_account(account_id)
        return ExecuteTidy(
            gateway=self.gateway_for(account_id),
            tidy=self.repos.tidy,
            journal=self.journal(self.gateway_for(account_id), account_id),
            gate=self.cleaning_gate(account_id),
            clock=self.clock,
            quota=self.quota(account_id),
            snapshot=self.snapshot,
            protected=self.protected,
        )

    def protected(self, owner: int, campaign_id: int) -> bool:
        campaign = self.repos.campaigns.get(campaign_id)
        verdict = self.repos.verdicts.detail(campaign_id, owner)
        if not campaign or not verdict or verdict["outcome"] != "lost":
            return True
        decided = verdict["decided_at"]
        policy = TidyPolicy()
        if not decided or not timedelta(
            hours=policy.grace_hours
        ) <= self.clock.now() - decided <= timedelta(days=policy.freeze_days):
            return True
        return any(
            str(k["value"]) == str(campaign["dyn_id" if k["kind"] == "campaign" else "up_uid"])
            for k in self.repos.keep.all_of(owner)
        )

    def snapshot(self) -> str:
        """执行清理前的库快照。保留最近 20 份，够回溯到"上一次也是这么删的"那一天。"""
        return str(self.db.snapshot(self.paths.backups, keep=BACKUPS_KEPT))

    def quota(self, owner: int | None = None) -> Quota:
        from .domain.throttle import Budget

        owner = owner if owner is not None else (self.account() or (0,))[0]
        gap = float(self.repos.settings.get(owner, "participation.interval_seconds", "15"))
        return Quota(
            intents=self.repos.intents,
            clock=self.clock,
            budget=Budget(per_hour=0, per_day=0, min_gap_seconds=gap),
        )

    def journal(self, gateway=None, owner=None) -> Journal:  # type: ignore[no-untyped-def]
        from .usecases.catalog_controls import guard_action

        bound = self._resolve_account(owner) if self._write_scope else owner

        from contextlib import contextmanager

        target = gateway or self.gateway_for(bound)

        @contextmanager
        def fence(account_id):
            if account_id != bound:
                raise ValueError("写入网关与操作账号不一致")
            with self._write_scope(self, account_id):
                profile = self.repos.accounts.by_id(account_id)
                if not profile or str(target.whoami()) != profile.uid:
                    from .domain.errors import WriteUnavailable

                    raise WriteUnavailable("登录凭据与授权账号不符，请重新登录")
                yield

        return Journal(
            gateway=target,
            intents=self.repos.intents,
            clock=self.clock,
            events=self.bus,
            write_scope=fence if self._write_scope else None,
            preflight=self.check_writer,
            action_guard=lambda cid, action: guard_action(self, cid, action),
        )

    # ---- 扫码票据 ----
    def remember(self, ticket: QrTicket) -> None:
        with self._guard:
            self._tickets[ticket.key] = ticket
            while len(self._tickets) > 8:
                self._tickets.pop(next(iter(self._tickets)))

    def ticket(self, key: str) -> QrTicket:
        with self._guard:
            return self._tickets.get(key) or QrTicket(key=key, url="")

    # ---- 当前账号 ----
    def account(self) -> tuple[int, AccountProfile] | None:
        return self.repos.accounts.current()

    def require_account(self) -> tuple[int, AccountProfile]:
        current = self.account()
        if current is None:
            raise MissingCredential("还没有登录，请先扫码")
        return current

    def _resolve_account(self, account_id: int | None) -> int:
        """没指定账号就取当前的。指定了就必须真的存在。"""
        if account_id is None:
            current = self.account()
            if current is None:
                raise MissingCredential("还没有登录，请先扫码")
            return current[0]
        if not self.repos.accounts.exists(account_id):
            raise MissingCredential(f"账号 {account_id} 不存在")
        return account_id

    def switch_account(self, account_id: int) -> tuple[int, AccountProfile]:
        """切到另一个账号。

        只改"当前是谁"，不动任何数据 —— 每个账号的订阅源、参与记录、预选池
        本来就按 account_id 分开存，切号看到的就是另一套。
        """
        profile = self.repos.accounts.by_id(account_id)
        if profile is None:
            raise MissingCredential(f"账号 {account_id} 不存在")
        if not self.repos.accounts.credential_ref(account_id):
            raise MissingCredential("这个账号的凭据已经删了，需要重新扫码")
        if not self.vault.exists(self.repos.accounts.credential_ref(account_id)):
            raise MissingCredential("找不到这个账号的凭据文件，需要重新扫码")
        self.repos.accounts.mark_current(account_id)
        return account_id, profile

    def accounts(self) -> list[dict[str, object]]:
        """所有可切换的账号。

        `ready` 是"能不能真的拿它干活"—— 有账号行却没有凭据（删过、换过机器）
        时要如实False，否则切过去每个操作都报错，人还以为程序坏了。
        """
        current = self.account()
        current_id = current[0] if current else 0
        items: list[dict[str, object]] = []
        for account_id in self.repos.accounts.ids():
            profile = self.repos.accounts.by_id(account_id)
            if profile is None:
                continue
            ref = self.repos.accounts.credential_ref(account_id)
            items.append(
                {
                    "id": account_id,
                    **_profile_dict(profile),
                    "avatar": (
                        f"/api/accounts/{account_id}/avatar?v="
                        + hashlib.sha256(profile.avatar.encode()).hexdigest()[:12]
                        if profile.avatar
                        else ""
                    ),
                    "current": account_id == current_id,
                    "ready": bool(ref) and self.vault.exists(ref),
                }
            )
        return items

    def remove_account(self, account_id: int) -> int | None:
        """摘掉一个账号，返回摘完之后该用哪个号（没有可用的就 None）。

        数据和凭据分开处理：凭据删掉、账号标停用，但**不删它名下的记录**。
        参与记录是开奖核验的依据，清理清单是"我删了什么"的唯一凭据 —— 这些
        删了就真没了，而人想重新用这个号，扫一次码就全回来。
        """
        self.drop_session(account_id)
        # 只挑"真的能用"的号：剩下的号里可能有凭据早就丢了的，
        # 切过去照样是一片红，不如停在未登录。
        usable = [int(item["id"]) for item in self.accounts() if item["ready"]]
        if usable:
            self.repos.accounts.mark_current(usable[0])
        return usable[0] if usable else None

    def _migrate_session(self) -> None:
        """把单账号时代的 cookie 挪到带 uid 的键下。

        不做这一步，升级后所有人都会看到扫码页 —— 凭据明明还在硬盘上，
        只是换了个名字。只迁一次：迁完旧键就删了，下次启动 `exists` 为假。
        """
        try:
            if not self.vault.exists(LEGACY_SESSION):
                return
            credential = self.vault.read(LEGACY_SESSION)
            current = self.repos.accounts.current()
            if not credential or current is None:
                # 没有账号行说明这份 cookie 是孤儿，留着不动，等用户扫码覆盖。
                return
            account_id, profile = current
            key = session_key(profile.uid)
            self.vault.write(key, credential)
            self.repos.accounts.set_credential_ref(account_id, key)
            self.vault.erase(LEGACY_SESSION)
        except Exception:
            # 迁移失败不该让程序起不来。旧键还在，下次启动会再试一次；
            # 代价只是这一次显示"未登录"，而那本来就该重新扫码。
            pass

    # ---- 概览 ----
    def overview(self) -> dict[str, object]:
        current = self.account()
        conn = self.db.connect()
        # 活动是公共的（同一个活动谁都能参与），但"我参与了几条"必须按账号数 ——
        # 否则切到一个空账号，概览上还写着别人的三十条。
        account_id = current[0] if current else 0
        total = conn.execute("SELECT COUNT(*) AS n FROM campaign c WHERE NOT EXISTS(SELECT 1 FROM catalog_removed x WHERE x.dyn_id=c.dyn_id)").fetchone()["n"]
        open_now = conn.execute(
            "SELECT COUNT(*) AS n FROM campaign c WHERE NOT EXISTS(SELECT 1 FROM catalog_removed x WHERE x.dyn_id=c.dyn_id) AND (deadline_at IS NULL OR deadline_at > ?)",
            (_utc_stamp(self.clock),),
        ).fetchone()["n"]
        enrolled = conn.execute(
            "SELECT COUNT(*) AS n FROM enrollment WHERE account_id = ?", (account_id,)
        ).fetchone()["n"]
        settled = conn.execute(
            "SELECT COUNT(*) AS n FROM enrollment WHERE account_id = ? AND stage = 'settled'",
            (account_id,),
        ).fetchone()["n"]
        unresolved = 0
        if current is not None:
            unresolved = len(self.repos.intents.unresolved(current[0]))

        accounts = self.accounts()
        return {
            "signed_in": current is not None,
            "account": _profile_dict(current[1]) if current else None,
            "accounts": accounts,
            "encrypted": bool(getattr(self.vault, "secure", False)),
            "campaigns": {"total": int(total), "open": int(open_now)},
            "enrollments": {"total": int(enrolled), "settled": int(settled)},
            "unresolved_actions": unresolved,
        }

    def close(self) -> None:
        self.tasks.close()
        # 先停秒针再关连接：反过来的话，正在跑的那一轮会在网关关闭后炸出
        # 一个 "connection closed" 的错误，看起来像定时器坏了。
        for timer in self._clocks.values():
            timer.stop(persist=False)
        with self._guard:
            for _, gateway in self._gateways.values():
                gateway.close()
            self._gateways.clear()
        self.db.close()


def _profile_dict(profile: AccountProfile) -> dict[str, object]:
    from .runtime.redact import mask_id

    # B 站头像地址是 http 的。浏览器从 http 页面加载没问题，但桌面壳或将来上 https
    # 时会被当成混合内容直接拦掉，表现是"头像一直是空的"且没有任何报错。
    avatar = profile.avatar.strip()
    if avatar.startswith("http://"):
        avatar = "https://" + avatar[len("http://") :]
    # 原图 170 KB，界面上只占 32px。B 站图片服务支持在地址后面挂处理参数，
    # 加一段 96px 裁圆方图的 webp 后是 1.7 KB —— 为了一个头像多传 170 KB 不值得。
    if avatar and "@" not in avatar:
        avatar = f"{avatar}@96w_96h_1c.webp"
    return {
        "uid": mask_id(profile.uid),
        "nickname": profile.nickname,
        "level": profile.level,
        "avatar": avatar,
    }


def _utc_stamp(clock: SystemClock) -> str:
    return clock.now().astimezone(UTC).isoformat(timespec="seconds")


Factory = Callable[[], Container]
