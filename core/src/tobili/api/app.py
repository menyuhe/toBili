"""应用装配。

两个形态共用同一份装配：开发时 Vite 跑在 5173、跨域调这里；打包后前端构建产物
就在 `ui/dist`，由这里同源托管，CORS 不参与。

静态托管放在**最后**注册。FastAPI 按注册顺序匹配，`/{path:path}` 这种兜底一旦
排到前面，`/api/...` 就全被它吃掉了。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..container import Container
from .. import __version__
from .routes import (
    accounts,
    auth,
    campaigns,
    events,
    feeds,
    history,
    inbox,
    overview,
    pool,
    results,
    run,
    schedule,
    settings,
    tasks,
    tidy,
)

__all__ = ["create_app"]

# ui/dist 的位置：源码仓库里是 ../../ui/dist，打包后由 desktop 壳指过来。
_BUNDLE = Path(__file__).resolve().parents[4] / "ui" / "dist"

# 这个服务只给本机用，所以 Host 与 Origin 必须落在环回地址上。
#
# 两道都要，因为它们防的不是一件事：
# - **Host**：DNS 重绑定。攻击者把自己的域名解析到 127.0.0.1，浏览器就以为
#   同源了，能直接读收件箱里的中奖私信。绑死 Host 等于让那条路走不通。
# - **Origin**：别的网页顺手发一个请求过来。写接口靠 `application/json` 的预检
#   挡着，读接口没有这层，所以要自己看。
#
# `testserver` 是 FastAPI TestClient 的默认 Host，它只可能出现在测试里 ——
# 外面没人能把 Host 写成这个名字还连到这个进程。
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"})
_ALLOWED_ORIGINS = frozenset(
    {
        "http://127.0.0.1",
        "http://localhost",
        "http://[::1]",
    }
)


def _hostname(value: str) -> str:
    """去掉端口。IPv6 的 `[::1]:8848` 要按括号处理，不能简单地 split(':')。"""
    text = value.strip().lower()
    if text.startswith("["):
        return text.split("]", 1)[0] + "]"
    return text.rsplit(":", 1)[0] if ":" in text else text


def _origin_allowed(origin: str) -> bool:
    text = origin.strip().lower().rstrip("/")
    return any(text == allowed or text.startswith(allowed + ":") for allowed in _ALLOWED_ORIGINS)


def create_app(home: str | None = None, *, bundle: str | Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = Container(home)
        # 上次开着定时器就接着跑。放在这里而不是 import 时，是为了让"--help"
        # 和一次性命令不会顺手起一个后台写循环。
        background = getattr(app.state, "start_background", lambda box: None)(app.state.container)
        app.state.container.resume_schedule()
        app.state.container.tasks.start()
        from ..runtime.source_refresh import SourceRefresh

        source_refresh = SourceRefresh(app.state.container)
        from ..runtime.prize_worker import PrizeWorker

        prize_worker = PrizeWorker(app.state.container)
        try:
            yield
        finally:
            prize_worker.close()
            source_refresh.close()
            if background:
                background.close()
            app.state.container.close()

    app = FastAPI(
        title="toBili",
        version=__version__,
        description="B 站抽奖活动管理工具",
        lifespan=lifespan,
    )
    # 只为本地开发放行，打包后的桌面壳同源加载，不经过 CORS。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def only_loopback(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # 中间件在最外层，所以连静态资源一起挡：这个进程里没有任何东西是给外人看的。
        if _hostname(request.headers.get("host", "")) not in _ALLOWED_HOSTS:
            return JSONResponse({"detail": "只接受本机访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and not _origin_allowed(origin):
            return JSONResponse({"detail": "只接受本机页面发起的请求"}, status_code=403)
        return await call_next(request)

    app.include_router(overview.router)
    app.include_router(auth.router)
    # 素材地址必须在 digest 之前也可以、之后也可以 —— 两套前缀不重叠。
    # 放前面只是让 /openapi.json 里读起来是"素材、内容、操作"的顺序。
    app.include_router(accounts.router)
    app.include_router(campaigns.router)
    from .routes import catalog_controls, public_catalog

    app.include_router(catalog_controls.router)
    app.include_router(public_catalog.router)
    app.include_router(events.router)
    app.include_router(feeds.router)
    app.include_router(history.router)
    app.include_router(inbox.router)
    app.include_router(pool.router)
    app.include_router(results.router)
    app.include_router(run.router)
    app.include_router(schedule.router)
    app.include_router(settings.router)
    app.include_router(tidy.router)
    app.include_router(tasks.router)
    from .routes import account_browser, standby

    app.include_router(standby.router)
    app.include_router(account_browser.router)
    from importlib import import_module
    from importlib.util import find_spec

    if find_spec("tobili.private_pack") is not None:
        import_module("tobili.private_pack").mount(app)
    _mount_ui(app, Path(bundle) if bundle else _BUNDLE)
    return app


def _mount_ui(app: FastAPI, dist: Path) -> None:
    """有构建产物就托管，没有就算了 —— 开发时前端由 Vite 提供，API 不该因此起不来。"""
    index = dist / "index.html"
    if not index.is_file():
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        # 兜底路由**不能吃 /api**。吃掉的后果不是 404，而是 200 + 一整页 HTML：
        # 调用方（包括我自己）会以为"路由配错了"，实际是"这个进程根本没有这个接口" ——
        # 版本旧了、拼错一个字母，都会表现成这样。所以这里必须明确回 404。
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="没有这个接口")
        # 真实文件（图标、字体、manifest）直接给；其余一律回 index.html，
        # 让前端路由接住 —— 否则刷新 /tidy 会 404。
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
