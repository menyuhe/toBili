"""底层文件保险库。

格式很土但足够清楚：`4 字节魔数 + 载荷`。四个魔数位区分「系统密钥加密」与
「平台无加密能力时降级」两种状态，读取时两种都认，写入时优先加密。

Windows 使用 DPAPI；macOS 使用登录钥匙串保管加密密钥，访问失败时停止读写。
没有配置系统加密能力的其他平台使用明文格式，并将 `secure` 标为 False。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

from . import acl, dpapi

SEALED: Final[bytes] = b"TBV1"
OPEN: Final[bytes] = b"TBV0"
_BINARY: Final[int] = getattr(os, "O_BINARY", 0)
_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


class VaultError(RuntimeError):
    """凭据损坏或无法解密。这种情况不应静默降级，要让人知道要重新登录。"""


def _default_codec(
    root: Path,
) -> tuple[Callable[[bytes], bytes] | None, Callable[[bytes], bytes] | None]:
    if dpapi.available():
        return dpapi.seal, dpapi.unseal
    if sys.platform == "darwin":
        from .keychain import KeychainCodec

        codec = KeychainCodec(root)
        return codec.seal, codec.unseal
    return None, None


class FileVault:
    def __init__(
        self,
        root: Path | str,
        *,
        seal: Callable[[bytes], bytes] | None = None,
        unseal: Callable[[bytes], bytes] | None = None,
        plain: bool = False,
    ) -> None:
        """`seal/unseal` 都不传时自动选平台实现；`plain=True` 显式关闭加密。

        显式关闭是给测试和"确实拿不到系统密钥"的降级场景用的 —— 它必须能被
        主动选择，而不是被悄悄使用。
        """
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        if plain:
            seal, unseal = None, None
        elif seal is None and unseal is None:
            seal, unseal = _default_codec(self._root)
        self._seal = seal
        self._unseal = unseal
        self._tight = True

    @property
    def root(self) -> Path:
        return self._root

    @property
    def secure(self) -> bool:
        """是否真的在加密。界面应当把这个值显示出来，而不是假设它一定是真。"""
        return self._seal is not None and self._unseal is not None

    @property
    def tight(self) -> bool:
        """文件权限是否真的只留了当前用户。加密之外的第二层，可能单独失效。"""
        return self._tight

    def _locate(self, name: str) -> Path:
        if not name or not set(name) <= _ALLOWED or name in {".", ".."}:
            raise ValueError(f"非法的凭据名：{name!r}")
        return self._root / f"{name}.bin"

    def exists(self, name: str) -> bool:
        return self._locate(name).is_file()

    def write(self, name: str, value: str) -> None:
        payload = value.encode("utf-8")
        body = OPEN + payload if self._seal is None else SEALED + self._seal(payload)

        slot = self._locate(name)
        staging = slot.with_name(slot.name + ".tmp")
        try:
            # 以 0600 直接创建，而不是"先建再收紧"——后者在两步之间存在一个
            # 任何同机用户都能读到密文的窗口。
            handle = os.fdopen(
                os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _BINARY, 0o600),
                "wb",
            )
            with handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staging, slot)
        except BaseException:
            staging.unlink(missing_ok=True)
            raise
        self._tight = acl.tighten(slot)
        if not acl.readable(slot):
            # 写进去了却读不出来，是最坏的一种"成功"。当场炸掉，别等下次启动
            # 才发现凭据没了 —— 那时已经无从得知是哪一步弄坏的。
            raise VaultError(f"{name} 写入后无法读取，文件权限收紧失败")

    def read(self, name: str) -> str | None:
        target = self._locate(name)
        try:
            raw = target.read_bytes()
        except FileNotFoundError:
            return None

        if raw.startswith(SEALED):
            if self._unseal is None:
                raise VaultError(f"{name} 是加密的，但当前平台无法解密")
            try:
                return self._unseal(raw[len(SEALED) :]).decode("utf-8")
            except OSError as exc:
                raise VaultError(f"{name} 解密失败，可能需要重新登录：{exc}") from exc
        if raw.startswith(OPEN):
            return raw[len(OPEN) :].decode("utf-8")
        # 无魔数：早期明文文件。能读就说明该升级了。
        return raw.decode("utf-8", errors="replace")

    def needs_upgrade(self, name: str) -> bool:
        """是否需要把明文/降级文件重写成密文。"""
        if not self.secure:
            return False
        target = self._locate(name)
        try:
            return not target.read_bytes().startswith(SEALED)
        except FileNotFoundError:
            return False

    def erase(self, name: str) -> None:
        self._locate(name).unlink(missing_ok=True)

    def names(self) -> list[str]:
        return sorted(p.stem for p in self._root.glob("*.bin") if p.is_file())
