"""依赖注入。

只有一个容器，挂在 `app.state` 上。不做复杂的 Provider 注册 —— 这套系统的对象
图很浅，手工装配比框架更清楚。
"""

from __future__ import annotations

from fastapi import Request

from ..container import Container


def get_container(request: Request) -> Container:
    return request.app.state.container
