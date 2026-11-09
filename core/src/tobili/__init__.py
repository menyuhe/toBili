"""toBili 核心包。

依赖方向：`api → usecases → domain ← adapters`。
`domain` 不知道网络与磁盘的存在；`adapters` 知道全部细节，但不做业务判断。
"""

__version__ = "0.2.18"
__all__ = ["__version__"]
