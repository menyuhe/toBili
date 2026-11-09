"""Account-scoped task choices and durable due times; no backlog replay."""

import json
import threading
import time

from ..domain.errors import Fault, RemoteError
from .lane import BusyAccount
from .redact import safe_exception
from .work_priority import participation_turn, requested

TASKS = {
    "topics": ("话题更新", ("topic",), 60),
    "collections": ("UP 合集更新", ("collection",), 60),
    "watch": ("UP 动态更新", ("space", "following"), 180),
    "results": ("开奖核验", (), 60),
    "messages": ("中奖消息", (), 30),
    "participate": ("参与活动", (), 60),
    "cleanup": ("清理检查", (), 180),
}
KEY = "tasks.config"


def source_slot(box, owner, key):
    local = float(box.repos.settings.get(owner, "sources.next_slot", "0"))
    if key in ("topics", "collections"):
        return max(local, float(box.repos.settings.get(0, "sources.shared.next_slot", "0")))
    return local


def feed_due_at(box, owner, feed):
    local = float(box.repos.settings.get(owner, f"tasks.feed_due.{feed['id']}", "0"))
    if feed["kind"] in ("topic", "collection"):
        return max(float(feed.get("next_scan_at", 0)), local)
    return local


class TaskBook:
    def __init__(self, box):
        self.box = box
        self.stop = threading.Event()
        self.guard = threading.RLock()
        self.worker = None
        self.runners = {}
        self.active = {}

    def config(self, owner):
        raw = self.box.repos.settings.get(owner, KEY, "")
        if raw:
            return {"hourly_limit": 20, **json.loads(raw)}
        settings = self.box.repos.settings
        feeds = {f["kind"] for f in self.box.repos.feeds.list_for(owner) if f["enabled"]}
        refresh = settings.get(owner, "sources.refresh", "0") == "1"
        return {
            "running": False,
            "apply": False,
            "selection": self.box.scope(owner),
            "limit": 10,
            "hourly_limit": 20,
            "tasks": [
                {
                    "key": key,
                    "enabled": key == "participate"
                    or (refresh and bool(feeds.intersection(kinds))),
                    "minutes": int(settings.get(owner, "sources.interval", "60"))
                    if kinds and refresh
                    else minutes,
                }
                for key, (_, kinds, minutes) in TASKS.items()
            ],
        }

    def view(self, owner):
        config = self.config(owner)
        settings = self.box.repos.settings
        upcoming = []
        now = time.time()
        if config["running"]:
            for task in config["tasks"]:
                if task["enabled"] and task["key"] != self.active.get(owner, {}).get("key"):
                    at = float(settings.get(owner, f"tasks.due.{task['key']}", "0"))
                    at = max(at, float(settings.get(owner, "tasks.cooldown", "0")))
                    if TASKS[task["key"]][1]:
                        at = max(at, source_slot(self.box, owner, task["key"]))
                    upcoming.append(dict(key=task["key"], label=TASKS[task["key"]][0], at=at))
        scheduled_next = min(upcoming, key=lambda item: item["at"]) if upcoming else None
        upcoming = [{**item, "at": max(now, item["at"])} for item in upcoming]
        if settings.get(owner, "sources.refresh", "0") == "1":
            for feed in self.box.repos.feeds.list_for(owner):
                if feed["enabled"]:
                    at = float(settings.get(owner, f"sources.due.{feed['id']}", "0"))
                    upcoming.append(
                        dict(
                            key="source",
                            label="订阅 · " + (feed["label"] or feed["target"]),
                            at=max(now, at, float(settings.get(owner, "sources.next_slot", "0"))),
                        )
                    )
        return {
            **config,
            "next": min(upcoming, key=lambda item: item["at"]) if upcoming else None,
            "next_scheduled": scheduled_next,
            "account_id": owner,
            "legacy_running": self.box.schedule(owner).status().running,
            "sources_running": settings.get(owner, "sources.refresh", "0") == "1",
            "active": (
                {"key": "participate", "note": self.box.pool_progress[owner]}
                if getattr(self.box, "pool_progress", {}).get(owner)
                else dict(self.active.get(owner, {}))
            ),
            "hourly_used": len(self.attempts(owner)),
            "states": {
                key: {
                    "next_at": settings.get(owner, f"tasks.due.{key}", ""),
                    "history": json.loads(settings.get(owner, f"tasks.history.{key}", "[]")),
                }
                for key in TASKS
            },
        }

    def save(self, owner, config):
        with self.guard:
            previous = self.config(owner)
            self.box.schedule(owner).stop()
            now = time.time()
            with self.box.db.write() as db:

                def put(key, value):
                    db.execute(
                        "INSERT INTO setting(account_id,key,value) VALUES(?,?,?) ON CONFLICT(account_id,key) DO UPDATE SET value=excluded.value",
                        (owner, key, value),
                    )

                put(KEY, json.dumps(config))
                if config["running"] and any(
                    t["enabled"] and TASKS[t["key"]][1] for t in config["tasks"]
                ):
                    put("sources.refresh", "0")
                for task in config["tasks"]:
                    old = next((t for t in previous["tasks"] if t["key"] == task["key"]), None)
                    if not previous["running"] or old != task:
                        put(f"tasks.due.{task['key']}", str(now + task["minutes"] * 60))

    def attempts(self, owner):
        return [
            at
            for at in json.loads(self.box.repos.settings.get(owner, "tasks.attempts", "[]"))
            if at > time.time() - 3600
        ]

    def compatible(self, owner, key, config):
        current = self.config(owner)

        def task(plan):
            return next((t for t in plan.get("tasks", []) if t["key"] == key), None)

        return (
            current["running"]
            and task(current) == task(config)
            and (
                key != "participate"
                or all(
                    current.get(k) == config.get(k)
                    for k in ("apply", "selection", "limit", "hourly_limit")
                )
            )
        )

    def start(self):
        self.worker = threading.Thread(target=self.loop, name="account-tasks", daemon=True)
        self.worker.start()

    def loop(self):
        while not self.stop.wait(10):
            for owner in self.box.repos.accounts.ids():
                if self.stop.is_set():
                    return
                with self.guard:
                    self.runners = {k: v for k, v in self.runners.items() if v.is_alive()}
                    if owner in self.runners or len(self.runners) >= 4:
                        continue
                    thread = threading.Thread(target=self._tick_safe, args=(owner,), daemon=True)
                    self.runners[owner] = thread
                    thread.start()

    def _tick_safe(self, owner):
        try:
            self.tick(owner)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Task scheduler tick failed")

    def tick(self, owner):
        if not self.box.has_session(owner) or requested(self.box, owner):
            return
        if float(self.box.repos.settings.get(owner, "tasks.cooldown", "0")) > time.time():
            return
        with self.guard:
            config = self.config(owner)
            if not config["running"]:
                return
            active = [t for t in config["tasks"] if t["enabled"]]
            if not active:
                return

            def due(t):
                at = float(self.box.repos.settings.get(owner, f"tasks.due.{t['key']}", "0"))
                return max(at, source_slot(self.box, owner, t["key"])) if TASKS[t["key"]][1] else at

            ready = [t for t in active if due(t) <= time.time()]
            if not ready:
                return
            task = min(ready, key=lambda t: (t["key"] != "participate", due(t)))
            # Save before IO so an interrupted round cannot repeat immediately on restart.
            self.box.repos.settings.put(
                owner, f"tasks.due.{task['key']}", str(time.time() + task["minutes"] * 60)
            )
        result = self.run(owner, task["key"], config, apply=config["apply"], scheduled=True)
        with self.guard:
            if (
                self.compatible(owner, task["key"], config)
                and result.get("note") != "账号正在执行其他任务，稍后再试"
            ):
                self.box.repos.settings.put(
                    owner,
                    f"tasks.due.{task['key']}",
                    str(time.time() + result.get("next_seconds", task["minutes"] * 60)),
                )

    def run(self, owner, key, config, *, apply=False, scheduled=False):
        box = self.box
        result = {
            "at": box.clock.now().isoformat(),
            "ok": True,
            "scanned": 0,
            "written": 0,
            "note": "",
        }
        started = False
        admitted = 0
        cooldown = float(box.repos.settings.get(owner, "tasks.cooldown", "0"))
        if cooldown > time.time():
            return {
                **result,
                "note": "平台请求冷却中，到时自动继续",
                "next_seconds": cooldown - time.time(),
            }

        def checkpoint(index):
            nonlocal admitted
            if self.stop.is_set() or (scheduled and not self.compatible(owner, key, config)):
                return "任务已停止或配置改变，本轮结束"
            self.active[owner] = {
                "key": key,
                "since": result["at"],
                "completed": admitted,
                "note": f"正在处理第 {admitted + 1} 条",
            }
            if apply:
                attempts = self.attempts(owner)
                if len(attempts) >= config.get("hourly_limit", 20):
                    return "已到每小时参与上限，等待额度恢复"
                # Reserve before IO; failed and interrupted attempts count too.
                box.repos.settings.put(
                    owner, "tasks.attempts", json.dumps([*attempts, time.time()])
                )
            admitted += 1
            return ""

        try:
            with (
                participation_turn(box, owner)
                if key == "participate" and not scheduled
                else box.lanes.exclusive(owner)
            ):
                if scheduled and requested(box, owner):
                    raise BusyAccount("手动参与优先")
                if scheduled and not self.compatible(owner, key, config):
                    return {**result, "note": "任务配置已改变，本轮取消"}
                self.active[owner] = {
                    "key": key,
                    "since": result["at"],
                    "completed": 0,
                    "note": "正在准备",
                }
                started = True
                kinds = TASKS[key][1]
                if kinds:
                    reports = []
                    feed_due = []
                    interval = (
                        next((t["minutes"] for t in config.get("tasks", []) if t["key"] == key), 60)
                        * 60
                    )
                    feeds = box.repos.feeds.list_for(owner)
                    if scheduled:
                        feeds = sorted(
                            feeds,
                            key=lambda feed: feed_due_at(box, owner, feed),
                        )
                    for feed in feeds:
                        if scheduled and requested(box, owner):
                            feed_due.append(time.time() + 300)
                            break
                        if self.stop.is_set() or (
                            scheduled and not self.compatible(owner, key, config)
                        ):
                            raise RuntimeError("任务已中止，进度已保存")
                        if feed["enabled"] and feed["kind"] in kinds:
                            due_key = f"tasks.feed_due.{feed['id']}"
                            due = feed_due_at(box, owner, feed)
                            if scheduled and due > time.time():
                                feed_due.append(due)
                                continue
                            if scheduled and reports:
                                feed_due.append(time.time() + 300)
                                continue
                            if scheduled:
                                box.repos.settings.put(
                                    owner, "sources.next_slot", str(time.time() + 300)
                                )
                                box.repos.settings.put(owner, due_key, str(time.time() + 300))
                            self.active[owner] = {
                                "key": key,
                                "since": result["at"],
                                "completed": len(reports),
                                "note": f"正在更新 {feed['label'] or feed['target']}",
                            }
                            report = box.scan_feeds(owner).run(
                                owner,
                                feed_id=feed["id"],
                                pages=1,
                                per_feed=5 if scheduled else 20,
                                **(
                                    {"automatic": True}
                                    if scheduled and feed["kind"] in ("topic", "collection")
                                    else {}
                                ),
                            )
                            reports.append(report)
                            if scheduled:
                                delay = max(600, interval) if report.problems else interval
                                due = time.time() + delay
                                if feed["kind"] in ("topic", "collection"):
                                    fresh = next(
                                        (
                                            f
                                            for f in box.repos.feeds.list_for(owner)
                                            if f["id"] == feed["id"]
                                        ),
                                        feed,
                                    )
                                    due = max(
                                        time.time() + 60,
                                        float(fresh.get("next_scan_at", 0)) or due,
                                        source_slot(box, owner, key),
                                    )
                                box.repos.settings.put(owner, due_key, str(due))
                                feed_due.append(due)
                    problems = [p for r in reports for p in r.problems]
                    result.update(
                        scanned=sum(r.scanned for r in reports),
                        ok=not problems,
                        note="；".join(problems)
                        or (
                            "共享订阅已更新，本轮复用"
                            if reports and all(getattr(r, "reused", 0) for r in reports)
                            else f"更新 {len(reports)} 个来源，识别 {sum(r.matched for r in reports)} 条活动"
                        ),
                    )
                    if scheduled and feed_due:
                        result["next_seconds"] = max(60, min(feed_due) - time.time())
                elif key == "participate":
                    if apply and box.check_writer:
                        box.check_writer(owner)
                    if apply and scheduled:
                        from ..usecases.participation_repair import reconcile_follows

                        repairs = reconcile_follows(
                            box,
                            owner,
                            cancelled=lambda: (
                                self.stop.is_set() or not self.compatible(owner, key, config)
                            ),
                        )
                        result.update(
                            follow_checked=repairs["checked"], follow_repaired=repairs["repaired"]
                        )
                    # Explicitly selected items take priority even in catalogue mode.
                    if apply and config["selection"] == "catalog":
                        from ..usecases.participation_repair import recheck_catalog

                        resumed = recheck_catalog(
                            box,
                            owner,
                            cancelled=lambda: (
                                self.stop.is_set()
                                or (scheduled and not self.compatible(owner, key, config))
                            ),
                        )
                        result["catalog_checked"] = resumed["checked"]
                        result["catalog_ready"] = resumed["ready"]
                    # Run through RunPool so progress, pause and completion stay in sync.
                    from ..usecases.pool import history_pending

                    queued = any(
                        item.waiting
                        or (item.state.value == "review" and history_pending(item.message))
                        for item in box.repos.pool.states(owner)
                    )
                    paused = box.repos.pool.paused(owner)[0]
                    queue_report = None
                    if config["selection"] == "queue" or queued or paused:
                        r = box.run_pool(owner).run(
                            owner,
                            apply=apply,
                            limit=config["limit"],
                            before_each=checkpoint,
                            cancelled=lambda: (
                                self.stop.is_set()
                                or (scheduled and not self.compatible(owner, key, config))
                            ),
                        )
                        result.update(
                            ok=not r.failed,
                            scanned=r.attempted,
                            written=r.joined if apply else 0,
                            note=r.note,
                        )
                        queue_report = r
                    remaining = max(0, config["limit"] - admitted)
                    # Catalogue mode fills unused slots after the explicit queue.
                    # Read-only legacy checks do not occupy participation slots;
                    # errors, pause and cancellation still stop the whole round.
                    can_fill = queue_report is None or (
                        not queue_report.failed
                        and not getattr(queue_report, "stop_reason", "")
                        and not box.repos.pool.paused(owner)[0]
                        and not any(item.waiting for item in box.repos.pool.states(owner))
                    )
                    if config["selection"] == "catalog" and can_fill and remaining:
                        r = box.batch_enroll(owner).run(
                            owner,
                            limit=remaining,
                            floor=0.8,
                            dry_run=not apply,
                            before_each=checkpoint,
                            stop_on_failure=apply,
                            cancelled=lambda: (
                                self.stop.is_set()
                                or (scheduled and not self.compatible(owner, key, config))
                            ),
                        )
                        note = r.stop_reason
                        note = (
                            note
                            or "；".join(r.problems)
                            or (
                                f"奖品库候选 {r.candidates} 条，完成 {r.settled} 条"
                                if r.candidates
                                else box.repos.campaigns.no_candidate_reason(
                                    owner, now=box.clock.now(), floor=0.8
                                )
                            )
                        )
                        if r.blocked:
                            reasons = list(
                                dict.fromkeys(
                                    line.note
                                    for line in r.lines
                                    if line.stage.value == "blocked" and line.note
                                )
                            )
                            note += f"；待处理 {r.blocked} 条"
                            if reasons:
                                note += "：" + "；".join(reasons[:3])[:360]
                        result.update(
                            ok=result["ok"] and r.ok,
                            scanned=result["scanned"] + r.candidates,
                            written=result["written"] + (r.settled if apply else 0),
                            note="；".join(x for x in (result["note"], note) if x),
                        )
                    if result.get("follow_checked"):
                        result["note"] += (
                            f"；关注补查 {result['follow_checked']} 条，补关注 {result.get('follow_repaired', 0)} 条"
                        )
                    if result.get("catalog_checked"):
                        result["note"] += (
                            f"；奖品库复核 {result['catalog_checked']} 条，可接续 {result['catalog_ready']} 条"
                        )
                elif key == "results":
                    r = box.settle(owner).run(
                        owner, profile=box.repos.accounts.by_id(owner), limit=20, dry_run=False
                    )
                    result.update(
                        ok=r.ok,
                        scanned=r.checked,
                        note="；".join(r.problems)
                        or f"核验 {r.checked} 条，确定 {r.decided} 条结果",
                    )
                elif key == "messages":
                    r = box.inbox(owner).fetch(owner, pages=1, limit=60)
                    result.update(
                        ok=not r.problems,
                        scanned=len(r.items),
                        note="；".join(r.problems) or f"读取 {len(r.items)} 条消息，请到消息页查看",
                    )
                elif key == "cleanup":
                    r = box.plan_tidy().run(owner, limit=50, persist=False)
                    result.update(scanned=len(r.lines), note=f"可清理 {r.clearable} 条")
        except BusyAccount:
            result.update(ok=False, note="账号正在执行其他任务，稍后再试")
            box.repos.settings.put(owner, f"tasks.due.{key}", str(time.time() + 60))
        except RemoteError as exc:
            if exc.fault in (Fault.RISK, Fault.RATE_LIMIT, Fault.NETWORK, Fault.TIMEOUT):
                box.repos.settings.put(owner, "tasks.cooldown", str(time.time() + 900))
                result.update(
                    ok=False,
                    cooldown=True,
                    next_seconds=900,
                    note=f"{safe_exception(exc)}；冷却 15 分钟后按原排期继续",
                )
            else:
                result.update(ok=False, note=safe_exception(exc))
        except Exception as exc:
            result.update(ok=False, note=safe_exception(exc))
        finally:
            if started:
                self.active.pop(owner, None)
                if key == "participate":
                    getattr(box, "pool_progress", {}).pop(owner, None)
        with self.guard:
            if (
                started
                and scheduled
                and apply
                and key == "participate"
                and not result["ok"]
                and not result.get("cooldown")
            ):
                current = self.config(owner)
                if self.compatible(owner, key, config):
                    for task in current["tasks"]:
                        if task["key"] == key:
                            task["enabled"] = False
                    current["running"] = any(t["enabled"] for t in current["tasks"])
                    box.repos.settings.put(owner, KEY, json.dumps(current))
                    result["note"] += "；真实参与已停用，处理问题后重新勾选并保存"
            history_key = f"tasks.history.{key}"
            history = json.loads(box.repos.settings.get(owner, history_key, "[]"))
            box.repos.settings.put(owner, history_key, json.dumps([result, *history][:20]))
        box.bus.publish(
            "schedule.round", {"account_id": owner, "title": f"{TASKS[key][0]} · {result['note']}"}
        )
        return result

    def close(self):
        self.stop.set()
        if self.worker:
            self.worker.join(timeout=8)
        deadline = time.monotonic() + 8
        for worker in list(self.runners.values()):
            worker.join(timeout=max(0, deadline - time.monotonic()))

    def pause(self, owner):
        with self.guard:
            config = self.config(owner)
            config["running"] = False
            self.box.repos.settings.put(owner, KEY, json.dumps(config))
            self.box.schedule(owner).stop()
