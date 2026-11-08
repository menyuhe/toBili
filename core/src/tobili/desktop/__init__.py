"""桌面壳入口。

双击 exe 就到这里。做成一个薄壳的原因：所有判断都在下面几层，这里只负责
"把窗口给我支起来"，出问题时能用 `tobili serve` 原样复现。
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
import threading
from contextlib import suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import window
from .autostart import is_enabled, set_enabled
from .shell import DEFAULT_PORT, Shell, open_ui, say
from .tray import run as run_tray

_TITLE = "toBili"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tobili-desktop", description="toBili 桌面壳")
    parser.add_argument(
        "--port", type=int, default=None, help=f"监听端口，优先 {DEFAULT_PORT}，占用时自动选择"
    )
    parser.add_argument("--home", default=None, help="数据目录，缺省在 exe 旁边的 data/")
    parser.add_argument("--account-browser", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--target", default="", help=argparse.SUPPRESS)
    parser.add_argument("--browser-ticket", default="", help=argparse.SUPPRESS)
    parser.add_argument(
        "--browser",
        action="store_true",
        help="用系统浏览器打开，不起内嵌窗口（WebView2 缺失时的退路）",
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        help="只驻留托盘，不开窗口（开机自启用的就是它）",
    )
    parser.add_argument(
        "--autostart",
        choices=["on", "off", "status"],
        help="设置开机自启后退出，不开窗口",
    )
    return parser


class _DesktopStream(io.TextIOBase):
    def write(self, message: str) -> int:
        if message.strip():
            logging.getLogger("desktop.console").info("%s", message.rstrip())
        return len(message)


def _log_to(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        handlers=[
            RotatingFileHandler(
                path, maxBytes=4 * 1024 * 1024, backupCount=3, encoding="utf-8", delay=True
            )
        ],
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if sys.stdout is None:
        sys.stdout = _DesktopStream()
    if sys.stderr is None:
        sys.stderr = _DesktopStream()


def _complain(message: str) -> None:
    """出错了要有地方看。

    打包成 `--windowed` 之后没有控制台，异常如果不弹出来，用户看到的就是
    "双击了，什么也没发生"。这一条是为那种情况准备的。
    """
    say(message)
    logging.error(message)
    if sys.platform != "win32":
        return
    with suppress(Exception):
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, _TITLE, 0x10)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.account_browser is not None:
        from .account_browser import run

        return run(args.home, args.account_browser, args.target, args.browser_ticket)
    shell = Shell(home=args.home, port=args.port)
    _log_to(shell.paths.logs / "desktop.log")

    if args.autostart:
        if args.autostart == "status":
            say("开机自启：开" if is_enabled() else "开机自启：关")
            return 0
        try:
            set_enabled(args.autostart == "on")
        except OSError as exc:
            _complain(f"改不了开机自启：{exc}")
            return 1
        say("已开启开机自启" if args.autostart == "on" else "已关闭开机自启")
        return 0

    try:
        return _serve(
            shell, mode="tray" if args.tray else ("browser" if args.browser else "window")
        )
    except Exception as exc:
        logging.exception("桌面壳启动失败")
        _complain(f"toBili 没能启动：{exc}\n\n日志：{shell.paths.logs / 'desktop.log'}")
        return 1


def _serve(shell: Shell, *, mode: str) -> int:
    if not shell.claim():
        # 第二次双击：不起第二个服务，直接把已经开着的那个调出来。
        url = shell.existing_url()
        say(f"已经在运行：{url}")
        if mode == "window" and window.available():
            # 只开窗口，不传 shell —— 服务不在这个进程手里，退出时不能去停别人。
            return window.run(url, home=shell.paths.home)
        open_ui(url)
        return 0

    try:
        url = shell.start()
    except Exception:
        shell.stop()
        raise

    say(f"服务已启动：{url}")
    logging.info("listen %s home=%s", url, shell.paths.home)

    if mode == "window":
        if window.available():
            try:
                return window.run(url, shell=shell)
            except Exception:
                logging.exception("内嵌窗口失败，退回浏览器")
                open_ui(url)
                return _tray(shell)
        say("没有可用的内嵌窗口（缺 pywebview / WebView2），改用系统浏览器")
        open_ui(url)
        return _tray(shell)

    if mode == "browser":
        open_ui(url)
    return _tray(shell)


def _tray(shell: Shell) -> int:
    """窗口关掉之后仍然留在托盘里，直到用户选「退出」。"""
    stop = threading.Event()
    try:
        run_tray(shell, stop=stop)
    except KeyboardInterrupt:
        pass
    finally:
        shell.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
