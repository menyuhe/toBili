"""接口层：只做 DTO 映射与错误转换。"""

from .app import create_app

__all__ = ["create_app"]
