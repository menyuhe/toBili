"""命令行入口。

留一个 CLI 有两个用处：M1 阶段可以在没有界面的情况下验证整条链路；出问题时能
用最小依赖复现，不必先怀疑界面。
"""

from __future__ import annotations

import argparse
import sys
import time

from .container import Container
from .domain.errors import MissingCredential, RemoteError, SchemaTooNew
from .runtime.redact import mask_id, safe_exception
from .usecases.manage_feeds import FeedRejected
from .usecases.sign_in import SignInExpired, SignInRejected


def _ok(text: str) -> None:
    print(f"  {text}")


def cmd_login(_args: argparse.Namespace) -> int:
    box = Container()
    auth = box.authenticator()
    try:
        ticket = auth.begin()
    except RemoteError as exc:
        print(f"获取二维码失败：{safe_exception(exc)}")
        return 2

    print("\n用 B 站手机客户端扫码：\n")
    _show_qr(ticket.url)
    print(f"\n  {ticket.url}\n")
    print("等待扫码…（Ctrl+C 取消）")

    deadline = time.monotonic() + 180
    seen = ""
    sign_in = box.sign_in()
    try:
        while time.monotonic() < deadline:
            progress = auth.poll(ticket)
            if progress.state != seen:
                seen = progress.state
                if seen == "scanned":
                    print("  已扫码，请在手机上点确认")
            if progress.state == "confirmed" and progress.credential:
                result = sign_in.persist(progress.credential)
                print("\n登录成功：")
                _ok(f"账号 {mask_id(result.profile.uid)}（{result.profile.nickname}）")
                _ok(f"等级 Lv.{result.profile.level}")
                _ok(f"凭据加密：{'是' if result.encrypted else '否（当前平台不支持）'}")
                _ok(f"数据目录 {box.paths.home}")
                return 0
            if progress.state in {"expired", "rejected"}:
                print("二维码已失效，请重新运行 login")
                return 3
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n已取消")
        return 130
    except (SignInExpired, SignInRejected) as exc:
        print(f"登录失败：{exc}")
        return 3
    finally:
        box.close()

    print("等待超时，请重新运行 login")
    return 3


def cmd_whoami(_args: argparse.Namespace) -> int:
    box = Container()
    try:
        account = box.account()
        if account is None:
            print("还没有登录，先运行：tobili login")
            return 1
        _id, profile = account
        _ok(f"账号 {mask_id(profile.uid)}（{profile.nickname}）Lv.{profile.level}")
        _ok(f"凭据加密：{'是' if box.vault.secure else '否'}")
        return 0
    finally:
        box.close()


def cmd_scan(args: argparse.Namespace) -> int:
    box = Container()
    try:
        account = box.require_account()
        targets = box.repos.accounts.ids() if args.all else [account[0]]
        if not targets:
            print("没有可用账号")
            return 1

        worst = 0
        for account_id in targets:
            with box.lanes.exclusive(account_id):
                report = box.scan_feeds().run(account_id, per_feed=args.limit, pages=args.pages)
            _ok(
                f"账号 {account_id}：订阅源 {report.feeds} 个，读取 {report.scanned} 条，"
                f"识别 {report.matched} 次，去重后 {report.unique} 个活动"
            )
            if report.duplicates:
                _ok(f"其中 {report.duplicates} 次是别的源已经见过的（已去重）")
            for problem in report.problems:
                _ok(f"问题：{problem}")
            if not report.ok:
                worst = 1
        return worst
    except MissingCredential as exc:
        print(str(exc))
        return 1
    finally:
        box.close()


def cmd_feed(args: argparse.Namespace) -> int:
    box = Container()
    try:
        account = box.require_account()
        manage = box.manage_feeds()

        if args.action == "list":
            items = manage.list(account[0])
            if not items:
                _ok("还没有订阅源。用 tobili feed add <UID> 添加")
                return 0
            for item in items:
                state = "启用" if item.enabled else "停用"
                stamp_note = f" · 上次扫描 {item.last_scan_at}" if item.last_scan_at else ""
                _ok(f"[{item.id}] {state} {item.kind}:{item.target} {item.label}{stamp_note}")
            return 0

        if args.action == "add":
            target = getattr(args, "target", "") or ""
            feed_id = manage.add(account[0], kind=args.kind, target=target, label=args.label)
            where = f"{args.kind}:{target}" if target else args.kind
            _ok(f"已添加订阅源 [{feed_id}] {where}")
            return 0

        manage.remove(account[0], args.feed_id)
        _ok(f"已停用订阅源 {args.feed_id}")
        return 0
    except FeedRejected as exc:
        print(f"无法处理：{exc}")
        return 1
    except MissingCredential as exc:
        print(str(exc))
        return 1
    finally:
        box.close()


def cmd_settle(args: argparse.Namespace) -> int:
    box = Container()
    try:
        account = box.require_account()
        with box.lanes.exclusive(account[0]):
            report = box.settle().run(
                account[0],
                profile=account[1],
                limit=args.limit,
                dry_run=not args.apply,
            )
        mode = "已写入" if args.apply else "预演（加 --apply 才落库）"
        _ok(f"核验 {report.checked} 条 · 有结论 {report.decided} 条 · {mode}")
        for item in report.items:
            _ok(f"{item.up_name} {item.dyn_id}：{item.outcome.value} — {item.evidence}")
        for problem in report.problems:
            _ok(f"问题：{problem}")
        return 0 if report.ok else 1
    except MissingCredential as exc:
        print(str(exc))
        return 1
    except RemoteError as exc:
        print(f"失败：{safe_exception(exc)}")
        return 2
    finally:
        box.close()


def cmd_tidy(args: argparse.Namespace) -> int:
    """清理的全部子命令。

    默认动作是 `plan`：`tobili tidy` 和 `tobili tidy plan` 等价，都是为了让人先看到
    清单。执行必须显式写 `tidy run <id> --apply`，少一个词就不会动手。
    """
    action = getattr(args, "action", None) or "plan"
    handler = {
        "plan": _tidy_plan,
        "show": _tidy_show,
        "confirm": _tidy_confirm,
        "run": _tidy_run,
        "undo": _tidy_undo,
        "pause": _tidy_pause,
        "resume": _tidy_resume,
        "ack": _tidy_ack,
        "status": _tidy_status,
    }.get(action)
    if handler is None:  # pragma: no cover - argparse 已限制取值
        print(f"未知的清理动作：{action}")
        return 2
    return handler(args)


def _tidy_plan(args: argparse.Namespace) -> int:
    box = Container()
    try:
        account = box.require_account()
        preview = box.plan_tidy().run(account[0], limit=getattr(args, "limit", None))
        _ok(f"清理预演：可清 {preview.clearable} 条 / 共 {len(preview.lines)} 条")
        for line in preview.lines:
            mark = "可清" if line.allowed else "保留"
            _ok(f"[{mark}] {line.up_name or line.dyn_id} — {line.reason}")
        if preview.persisted:
            _ok(f"计划已存为 #{preview.plan_id}；本轮只预演，没有执行任何删除")
            _ok(f"确认无误后：tobili tidy confirm {preview.plan_id}")
        elif preview.lines:
            _ok("没有符合条件的记录，未生成计划")
        return 0
    except MissingCredential as exc:
        print(str(exc))
        return 1
    finally:
        box.close()


def _tidy_show(args: argparse.Namespace) -> int:
    box = Container()
    try:
        account = box.require_account()
        plan = box.repos.tidy.plan_of(args.plan_id)
        if plan is None:
            print(f"计划 #{args.plan_id} 不存在")
            return 1
        if int(plan["account_id"]) != account[0]:
            print("这个计划不属于当前账号")
            return 1
        _ok(f"计划 #{args.plan_id} · {plan['state']} · 创建于 {plan['created_at']}")
        if plan["note"]:
            _ok(f"说明：{plan['note']}")
        for item in box.repos.tidy.items_of(args.plan_id):
            # 预演阶段 `target` 还是空的 —— 要删的那条转发得等执行时才去我的空间里
            # 翻出来。所以这里退一步显示活动本身的动态 ID：至少能点开看看是哪一条。
            target = item["target"] or f"活动 {item['dyn_id']}"
            reason = item["reason"] or ""
            _ok(
                f"[{item['state']}] {item['action']} {item['up_name'] or item['dyn_id']}"
                f" · 目标 {target} {reason}"
            )
        return 0
    except MissingCredential as exc:
        print(str(exc))
        return 1
    finally:
        box.close()


def _tidy_confirm(args: argparse.Namespace) -> int:
    from .usecases.execute_tidy import TidyRejected

    box = Container()
    try:
        box.require_account()
        state = box.execute_tidy().confirm(args.plan_id)
        _ok(f"计划 #{args.plan_id} 已确认（{state.value}）")
        _ok(f"下一步：tobili tidy run {args.plan_id}            # 预演，不写")
        _ok(f"        tobili tidy run {args.plan_id} --apply    # 真的执行")
        return 0
    except (TidyRejected, MissingCredential) as exc:
        print(str(exc))
        return 1
    finally:
        box.close()


def _tidy_run(args: argparse.Namespace) -> int:
    from .usecases.execute_tidy import TidyBlocked, TidyRejected

    box = Container()
    try:
        account = box.require_account()
        with box.lanes.exclusive(account[0]):
            report = box.execute_tidy().run(
                args.plan_id, apply=args.apply, limit=getattr(args, "limit", None)
            )
        mode = "执行" if args.apply else "预演"
        _ok(f"计划 #{args.plan_id} · {mode} · 共 {len(report.lines)} 条")
        for line in report.lines:
            _ok(f"[{line.state}] {line.action} {line.up_name or line.dyn_id} — {line.message}")
        _ok(
            f"完成 {report.done} · 失败 {report.failed} · 跳过 {report.skipped}"
            + (f" · 待执行 {report.planned}" if not args.apply else "")
        )
        if report.note:
            _ok(report.note)
        if report.snapshot:
            _ok(f"执行前已备份：{report.snapshot}")
        return 0 if report.failed == 0 else 1
    except (TidyRejected, TidyBlocked, MissingCredential) as exc:
        print(str(exc))
        return 2
    finally:
        box.close()


def _tidy_undo(args: argparse.Namespace) -> int:
    box = Container()
    try:
        box.require_account()
        result = box.execute_tidy().undo(args.item_id)
        _ok(result.message)
        return 0 if result.ok else 1
    except MissingCredential as exc:
        print(str(exc))
        return 1
    finally:
        box.close()


def _tidy_pause(args: argparse.Namespace) -> int:
    box = Container()
    try:
        account = box.require_account()
        box.cleaning_gate().pause(account[0], args.reason or "")
        _ok("清理已暂停。执行 tidy resume 恢复。")
        return 0
    except MissingCredential as exc:
        print(str(exc))
        return 1
    finally:
        box.close()


def _tidy_resume(args: argparse.Namespace) -> int:
    box = Container()
    try:
        account = box.require_account()
        gate = box.cleaning_gate()
        gate.resume(account[0])
        decision = gate.check(account[0])
        _ok("已恢复。" if decision.open else f"已撤销暂停，但闸门仍然关闭：{decision.explain()}")
        return 0
    except MissingCredential as exc:
        print(str(exc))
        return 1
    finally:
        box.close()


def _tidy_ack(args: argparse.Namespace) -> int:
    box = Container()
    try:
        account = box.require_account()
        chat, system = box.cleaning_gate().ack(account[0])
        _ok(f"已记录：私信 {chat} 条、系统通知 {system} 条。这些消息不再拦清理。")
        return 0
    except MissingCredential as exc:
        print(str(exc))
        return 1
    finally:
        box.close()


def _tidy_status(args: argparse.Namespace) -> int:
    box = Container()
    try:
        account = box.require_account()
        snapshot = box.cleaning_gate().inspect(account[0])
        signals = snapshot.signals
        _ok(f"闸门：{'开放' if snapshot.open else '关闭'} — {snapshot.decision.reason}")
        if snapshot.decision.hint:
            _ok(f"处理方式：{snapshot.decision.hint}")
        _ok(f"暂停开关：{'开' if signals.paused else '关'} · 近期风控 {signals.risk_hits} 次")
        _ok(
            f"未读私信 {signals.unread_chat}（已确认 {signals.acked_chat}）· "
            f"未读系统通知 {signals.unread_system}（已确认 {signals.acked_system}）"
        )
        latest = box.repos.tidy.latest_plan(account[0])
        if latest:
            _ok(f"最近计划 #{latest['id']} · {latest['state']} · {latest['created_at']}")
        return 0
    except MissingCredential as exc:
        print(str(exc))
        return 1
    finally:
        box.close()


def cmd_quota(args: argparse.Namespace) -> int:
    box = Container()
    try:
        account = box.require_account()
        quota = box.quota()
        gate = quota.check(account[0])
        budget = quota.budget
        _ok(
            f"近一小时 {gate.last_hour}/{budget.per_hour} · 近一天 {gate.last_day}/{budget.per_day}"
        )
        _ok(f"最小间隔 {budget.min_gap_seconds} 秒 · {gate.explain()}")
        return 0
    except MissingCredential as exc:
        print(str(exc))
        return 1
    finally:
        box.close()


def cmd_enroll(args: argparse.Namespace) -> int:
    box = Container()
    try:
        account = box.require_account()
        usecase = box.enroll()
        with box.lanes.exclusive(account[0]):
            report = usecase.run(account[0], args.dyn_id, dry_run=not args.apply)
        mode = "执行" if args.apply else "预演（加 --apply 才会真的写）"
        _ok(f"活动 {report.dyn_id} · {mode}")
        for name, step in report.steps:
            _ok(f"{name}: {step.outcome.value} — {step.message}")
        _ok(f"最终状态：{report.stage.value}")
        return 0
    except MissingCredential as exc:
        print(str(exc))
        return 1
    except RemoteError as exc:
        print(f"失败：{safe_exception(exc)}")
        return 2
    finally:
        box.close()


def cmd_discover(args: argparse.Namespace) -> int:
    box = Container()
    try:
        account = box.require_account()
        report = box.discover().run(account[0], args.keyword, limit=args.limit, apply=args.add)
        if not report.picked:
            print(f"没有搜到和「{report.keyword}」相关的 UP 主")
            for problem in report.problems:
                _ok(f"问题：{problem}")
            return 1

        _ok(f"关键词「{report.keyword}」找到 {len(report.picked)} 个候选：")
        for item in report.picked:
            reasons = "；".join(item.reasons) or "无明显特征"
            _ok(f"{item.user.name}（UID {item.user.uid}，粉丝 {item.user.fans}）{reasons}")
        for problem in report.problems:
            _ok(f"问题：{problem}")

        if report.added:
            _ok(f"已加入订阅源 {len(report.added)} 个：{', '.join(map(str, report.added))}")
        else:
            _ok("以上只是候选。确认要订阅就加 --add，或手动 feed add <UID>")
        return 0
    except MissingCredential as exc:
        print(str(exc))
        return 1
    except RemoteError as exc:
        print(f"失败：{safe_exception(exc)}")
        return 2
    finally:
        box.close()


def cmd_run(args: argparse.Namespace) -> int:
    box = Container()
    try:
        account = box.require_account()
        targets = box.repos.accounts.ids() if args.all else [account[0]]
        report = box.pipeline().run(
            targets,
            pages=args.pages,
            per_feed=args.per_feed,
            limit=args.limit,
            floor=args.floor,
            apply=args.apply,
        )
        mode = "执行" if args.apply else "预演（加 --apply 才会真的写）"
        _ok(f"{mode} · 账号 {len(report.accounts)} 个 · 共读取 {report.scanned} 条")
        for outcome in report.accounts:
            if outcome.skipped:
                _ok(f"账号 {outcome.account_id}：跳过 —— {outcome.skipped}")
                continue
            _ok(
                f"账号 {outcome.account_id}：识别 {outcome.matched} 次 / 去重 {outcome.unique} 个，"
                f"候选 {outcome.candidates} 条，完成 {outcome.settled}，"
                f"阻塞 {outcome.blocked}，失败 {outcome.failed}"
            )
            if not args.apply and outcome.planned:
                _ok(f"预演了 {outcome.planned} 条，没有写任何东西")
            if outcome.stop_reason:
                _ok(f"提前停止：{outcome.stop_reason}（还剩 {outcome.incomplete} 条没做）")
            for problem in outcome.problems:
                _ok(f"问题：{problem}")
        return 0 if all(o.ok for o in report.accounts) else 1
    except MissingCredential as exc:
        print(str(exc))
        return 1
    finally:
        box.close()


def _notify_round(box: Container, report, pushed: set[str]) -> None:  # type: ignore[no-untyped-def]
    """一轮结束后该推的通知。

    两条原则：出问题一定推；没做事就不推。还有一条容易漏 —— **同一条中奖消息
    只推一次**，否则每轮都往群里发一遍，人第一件事就是把机器人禁掉。
    """
    notify = box.notify()
    if not notify.enabled():
        return
    failed = sum(outcome.failed for outcome in report.accounts)
    problems = [problem for outcome in report.accounts for problem in outcome.problems]
    notify.about_round(failed=failed, problems=problems, settled=report.settled)

    try:
        view = box.inbox().fetch(box.account()[0], limit=20)  # type: ignore[index]
    except Exception:
        return
    fresh = [item for item in view.items if item.prize and item.key not in pushed]
    if not fresh:
        return
    if notify.about_inbox(fresh):
        pushed.update(item.key for item in fresh)
        print(f"  已通过钉钉推送 {len(fresh)} 条可能的开奖消息", flush=True)


def cmd_daemon(args: argparse.Namespace) -> int:
    import threading

    from .runtime.scheduler import Scheduler

    box = Container()
    stop = threading.Event()
    try:
        box.require_account()
        # 只跑有凭据的账号。缺凭据的号每轮都会报同一个错，除了刷屏没别的作用。
        targets = [int(item["id"]) for item in box.accounts() if item["ready"]]
        if not targets:
            print("没有凭据可用的账号，请先扫码登录。", flush=True)
            return 1
        pipeline = box.pipeline()
        # 已经推过的消息 key。放内存而不是落库：进程重启后同一条会再推一次，
        # 但那一次之后就安静了 —— 为此加一张表不值当。
        pushed: set[str] = set()

        def task() -> dict[str, object]:
            # 先清历史再跑：表只增不减，daemon 挂一个月就是十万行 run_event。
            # 放在轮次开头而不是结尾，是因为结尾那步可能因为异常被跳过 ——
            # 而"跑了一轮却没清理"这种漏，攒起来正好是最难发现的那种。
            swept = box.housekeeping().run()
            if swept.total:
                print(f"  [{time.strftime('%H:%M:%S')}] 清理旧记录 {swept.total} 条", flush=True)
            report = pipeline.run(
                targets,
                pages=args.pages,
                per_feed=args.per_feed,
                limit=args.limit,
                floor=args.floor,
                apply=args.apply,
            )
            for outcome in report.accounts:
                if outcome.skipped:
                    note = outcome.skipped
                elif args.apply:
                    note = f"完成 {outcome.settled} / 候选 {outcome.candidates}"
                else:
                    note = f"预演 {outcome.planned} / 候选 {outcome.candidates}（未写入）"
                print(
                    f"  [{time.strftime('%H:%M:%S')}] 账号 {outcome.account_id}：{note}", flush=True
                )
                for problem in outcome.problems:
                    print(f"    问题：{problem}", flush=True)

            _notify_round(box, report, pushed)
            return {"accounts": len(report.accounts), "settled": report.settled}

        scheduler = Scheduler(task, interval=args.every, bus=box.bus, stop=stop)
        mode = "执行" if args.apply else "预演，不会真的写"
        print(f"每 {args.every:.0f} 秒跑一轮（{mode}）。Ctrl+C 停止。", flush=True)
        rounds = scheduler.run()
        print(f"\n已停止，共跑 {rounds} 轮。")
        return 0
    except KeyboardInterrupt:
        stop.set()
        print("\n已停止")
        return 130
    except MissingCredential as exc:
        print(str(exc))
        return 1
    finally:
        box.close()


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "tobili.api.app:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


def cmd_desktop(args: argparse.Namespace) -> int:
    """桌面壳。参数原样转给 `tobili.desktop`，两个入口共用一份解析逻辑。"""
    from .desktop import main as desktop_main

    forwarded: list[str] = []
    if args.port:
        forwarded += ["--port", str(args.port)]
    if args.tray:
        forwarded.append("--tray")
    if args.autostart:
        forwarded += ["--autostart", args.autostart]
    return desktop_main(forwarded)


def _show_qr(url: str) -> None:
    try:
        import qrcode
    except ImportError:
        print("（未安装 qrcode，请把下面的链接发到手机浏览器打开）")
        return
    try:
        code = qrcode.QRCode(border=1)
        code.add_data(url)
        code.make(fit=True)
        code.print_ascii(invert=True)
    except Exception:
        print("（二维码渲染失败，请复制下面的链接）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tobili", description="B 站抽奖活动管理工具")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="扫码登录").set_defaults(func=cmd_login)
    sub.add_parser("whoami", help="查看当前账号").set_defaults(func=cmd_whoami)

    scan = sub.add_parser("scan", help="扫描订阅源")
    scan.add_argument("--pages", type=int, default=1, help="每个源最多翻几页")
    scan.add_argument("--limit", type=int, default=20, help="每页最多取几条")
    scan.add_argument("--all", action="store_true", help="扫描所有已登录账号")
    scan.set_defaults(func=cmd_scan)

    feed = sub.add_parser("feed", help="订阅源管理")
    feed_actions = feed.add_subparsers(dest="action", required=True)
    feed_actions.add_parser("list", help="列出订阅源").set_defaults(func=cmd_feed)
    feed_add = feed_actions.add_parser("add", help="添加订阅源")
    feed_add.add_argument("target", nargs="?", default="", help="UP 的 UID（following 不用填）")
    feed_add.add_argument(
        "--kind", default="space", help="space=某个 UP 的空间动态，following=我的关注流"
    )
    feed_add.add_argument("--label", default="", help="备注，缺省用 UID")
    feed_add.set_defaults(func=cmd_feed)
    feed_rm = feed_actions.add_parser("rm", help="停用订阅源")
    feed_rm.add_argument("feed_id", type=int)
    feed_rm.set_defaults(func=cmd_feed)

    discover = sub.add_parser("discover", help="按关键词找值得订阅的 UP 主")
    discover.add_argument("keyword", help="关键词，例如 转发抽奖")
    discover.add_argument("--limit", type=int, default=10, help="最多列几个候选")
    discover.add_argument("--add", action="store_true", help="直接把候选加入订阅源")
    discover.set_defaults(func=cmd_discover)

    run = sub.add_parser("run", help="一轮作业：扫描 + 批量参与（默认只预演）")
    run.add_argument("--apply", action="store_true", help="真正执行写操作")
    run.add_argument("--limit", type=int, default=10, help="本轮最多参与几条")
    run.add_argument("--floor", type=float, default=0.8, help="自动参与的置信度门槛")
    run.add_argument("--pages", type=int, default=1, help="每个源最多翻几页")
    run.add_argument("--per-feed", type=int, default=20, help="每页最多取几条")
    run.add_argument("--all", action="store_true", help="对所有已登录账号执行")
    run.set_defaults(func=cmd_run)

    daemon = sub.add_parser("daemon", help="定时跑作业（默认只预演）")
    daemon.add_argument("--every", type=float, default=3600, help="每隔多少秒跑一轮")
    daemon.add_argument("--apply", action="store_true", help="真正执行写操作")
    daemon.add_argument("--limit", type=int, default=10, help="每轮最多参与几条")
    daemon.add_argument("--floor", type=float, default=0.8, help="自动参与的置信度门槛")
    daemon.add_argument("--pages", type=int, default=1, help="每个源最多翻几页")
    daemon.add_argument("--per-feed", type=int, default=20, help="每页最多取几条")
    daemon.set_defaults(func=cmd_daemon)

    sub.add_parser("quota", help="查看写操作配额").set_defaults(func=cmd_quota)

    settle = sub.add_parser("settle", help="核验开奖结果（默认只预演）")
    settle.add_argument("--limit", type=int, default=20, help="本轮最多核验几条")
    settle.add_argument("--apply", action="store_true", help="把结论写进数据库")
    settle.set_defaults(func=cmd_settle)

    tidy = sub.add_parser("tidy", help="清理：预演 / 确认 / 执行 / 回滚")
    tidy_actions = tidy.add_subparsers(dest="action")
    tidy_plan = tidy_actions.add_parser("plan", help="生成清理计划（只预演）")
    tidy_plan.add_argument("--limit", type=int, default=None, help="本轮最多列入几条")
    tidy_actions.add_parser("status", help="看闸门与最近计划")
    tidy_show = tidy_actions.add_parser("show", help="查看某个计划的明细")
    tidy_show.add_argument("plan_id", type=int)
    tidy_confirm = tidy_actions.add_parser("confirm", help="确认一个已预演的计划")
    tidy_confirm.add_argument("plan_id", type=int)
    tidy_run = tidy_actions.add_parser("run", help="执行计划（默认只预演）")
    tidy_run.add_argument("plan_id", type=int)
    tidy_run.add_argument("--apply", action="store_true", help="真正执行删除/取关")
    tidy_run.add_argument("--limit", type=int, default=None, help="本轮最多做几条")
    tidy_undo = tidy_actions.add_parser("undo", help="回滚一条取关")
    tidy_undo.add_argument("item_id", type=int)
    tidy_pause = tidy_actions.add_parser("pause", help="立即暂停清理")
    tidy_pause.add_argument("--reason", default="", help="暂停原因，会显示在状态里")
    tidy_actions.add_parser("resume", help="撤销暂停")
    tidy_actions.add_parser("ack", help="确认已看过未读消息，允许继续")
    tidy.set_defaults(func=cmd_tidy)

    enroll = sub.add_parser("enroll", help="参与一条活动（默认只预演）")
    enroll.add_argument("dyn_id", help="动态 ID")
    enroll.add_argument("--apply", action="store_true", help="真正执行写操作")
    enroll.set_defaults(func=cmd_enroll)

    serve = sub.add_parser("serve", help="启动本地接口")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8848)
    serve.set_defaults(func=cmd_serve)

    desktop = sub.add_parser("desktop", help="桌面壳：托盘 + 浏览器界面（单进程）")
    desktop.add_argument("--port", type=int, default=None, help="监听端口，默认 8811")
    desktop.add_argument("--tray", action="store_true", help="只驻留托盘，不打开浏览器")
    desktop.add_argument("--autostart", choices=["on", "off", "status"], default=None)
    desktop.set_defaults(func=cmd_desktop)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return int(args.func(args))
    except SchemaTooNew as exc:
        # 不打 traceback：这不是程序坏了，是库比程序新，用户需要的是"换新版"这一句话。
        print(f"\n打不开数据目录：{exc}\n")
        return 4


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
