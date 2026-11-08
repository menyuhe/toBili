"""macOS vault encryption; the encryption key stays in the login Keychain."""

import hashlib
import threading


class KeychainCodec:
    def __init__(self, root):
        self.account = hashlib.sha256(str(root.resolve()).encode()).hexdigest()
        self._cipher = None
        self._guard = threading.Lock()

    def cipher(self):
        from cryptography.fernet import Fernet

        with self._guard:
            if self._cipher is None:
                self._cipher = Fernet(self._key())
            return self._cipher

    def _key(self):
        import Security as sec
        from cryptography.fernet import Fernet

        query = {
            sec.kSecClass: sec.kSecClassGenericPassword,
            sec.kSecAttrService: "cn.4wdo.tobili.vault",
            sec.kSecAttrAccount: self.account,
        }
        lookup = {**query, sec.kSecReturnData: True, sec.kSecMatchLimit: sec.kSecMatchLimitOne}
        status, value = sec.SecItemCopyMatching(lookup, None)
        if status == sec.errSecItemNotFound:
            key = Fernet.generate_key()
            status, _ = sec.SecItemAdd({**query, sec.kSecValueData: key}, None)
            if status == sec.errSecSuccess:
                return key
            if status == sec.errSecDuplicateItem:
                status, value = sec.SecItemCopyMatching(lookup, None)
        if status != sec.errSecSuccess:
            raise OSError(f"钥匙串不可用（{status}），请解锁登录钥匙串后重试")
        return bytes(value)

    def seal(self, value):
        return self.cipher().encrypt(value)

    def unseal(self, value):
        from cryptography.fernet import InvalidToken

        try:
            return self.cipher().decrypt(value)
        except InvalidToken as exc:
            raise OSError("凭据不属于当前钥匙串或已损坏，请重新登录") from exc
