"""凭据保险库：能加密、能降级、不认非法名。"""

from __future__ import annotations

import pytest

from tobili.adapters.vault import OPEN, SEALED, FileVault, VaultError


def _fake_seal(payload: bytes) -> bytes:
    return bytes(b ^ 0x5A for b in payload)


def _fake_unseal(payload: bytes) -> bytes:
    return bytes(b ^ 0x5A for b in payload)


@pytest.fixture
def vault(tmp_path) -> FileVault:  # type: ignore[no-untyped-def]
    return FileVault(tmp_path / "vault", seal=_fake_seal, unseal=_fake_unseal)


def test_roundtrip(vault: FileVault) -> None:
    vault.write("session", "SESSDATA=abc; bili_jct=xyz")
    assert vault.read("session") == "SESSDATA=abc; bili_jct=xyz"


def test_plaintext_never_touches_disk(vault: FileVault) -> None:
    vault.write("session", "SESSDATA=supersecret")
    raw = (vault.root / "session.bin").read_bytes()
    assert raw.startswith(SEALED)
    assert b"supersecret" not in raw


def test_missing_returns_none(vault: FileVault) -> None:
    assert vault.read("nobody") is None


def test_rejects_path_traversal(vault: FileVault) -> None:
    for name in ("../escape", "a/b", "", "..", "x\\y"):
        with pytest.raises(ValueError):
            vault.write(name, "value")


def test_erase(vault: FileVault) -> None:
    vault.write("tmp", "v")
    vault.erase("tmp")
    assert not vault.exists("tmp")


def test_fallback_when_no_cipher(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """平台没有加密能力时要能工作，但必须把 secure 标成 False。"""
    vault = FileVault(tmp_path / "plain", plain=True)
    vault.write("session", "cookie")
    assert vault.secure is False
    assert (vault.root / "session.bin").read_bytes().startswith(OPEN)
    assert vault.read("session") == "cookie"


def test_corrupted_sealed_file_raises(tmp_path) -> None:  # type: ignore[no-untyped-def]
    vault = FileVault(tmp_path / "v", seal=_fake_seal, unseal=None)
    vault.write("session", "cookie")
    broken = FileVault(tmp_path / "v", seal=_fake_seal, unseal=None)
    with pytest.raises(VaultError):
        broken.read("session")


def test_needs_upgrade_detects_legacy_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    plain = FileVault(tmp_path / "v", plain=True)
    plain.write("session", "cookie")
    upgraded = FileVault(tmp_path / "v", seal=_fake_seal, unseal=_fake_unseal)
    assert upgraded.needs_upgrade("session") is True
    upgraded.write("session", upgraded.read("session") or "")
    assert upgraded.needs_upgrade("session") is False


def test_names_lists_entries(vault: FileVault) -> None:
    vault.write("a", "1")
    vault.write("b", "2")
    assert vault.names() == ["a", "b"]


@pytest.mark.windows
def test_tighten_keeps_owner_access(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """收紧权限不能把主人也关在门外。

    这是实际踩过的坑：断继承和授权分两条命令时，中间 DACL 为空，第二条失败就
    得到一个谁都读不了的文件（连 System 也读不了）。所以这里断言"收紧之后自己
    还能打开"，而不是只看 icacls 的返回码。
    """
    from tobili.adapters.vault import acl

    target = tmp_path / "credential.bin"
    target.write_bytes(b"payload")
    assert acl.tighten(target) is True
    assert acl.readable(target) is True
    clean, leftovers = acl.audit(target)
    assert clean, f"仍有宽泛主体：{leftovers}"


@pytest.mark.windows
def test_vault_write_leaves_readable_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    vault = FileVault(tmp_path / "vault", seal=_fake_seal, unseal=_fake_unseal)
    vault.write("session", "SESSDATA=abc")
    assert vault.tight is True
    assert vault.read("session") == "SESSDATA=abc"
