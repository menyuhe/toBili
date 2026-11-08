"""扫码登录用例。

把"轮询到出结果"这件事从适配器里搬到用例，是为了让适配器只负责单次请求——
单次请求可以单独测试，轮询逻辑可以用假实现快速验证，不需要等两分钟。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..domain.session import session_key
from ..ports.bili import Authenticator, Reader
from ..ports.collab import SecretStore
from ..ports.models import AccountProfile, QrProgress, QrTicket
from ..ports.repo import AccountRepo

# 会话键名现在由 `domain.session` 统一给（带 uid），这里不再定义 —— 两个名字
# 描述同一个东西时，改了一个忘了另一个，迁移就会静默失效。


class SignInRejected(RuntimeError):
    pass


class SignInExpired(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SignInResult:
    account_id: int
    profile: AccountProfile
    encrypted: bool


class QrSignIn:
    def __init__(
        self,
        *,
        auth: Authenticator,
        vault: SecretStore,
        accounts: AccountRepo,
        connect: Callable[[str], Reader],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._auth = auth
        self._vault = vault
        self._accounts = accounts
        self._connect = connect
        self._sleep = sleep

    def begin(self) -> QrTicket:
        return self._auth.begin()

    def wait(
        self,
        ticket: QrTicket,
        *,
        on_state: Callable[[QrProgress], None] | None = None,
        interval: float = 2.0,
        limit: float = 180.0,
    ) -> SignInResult:
        deadline = time.monotonic() + limit
        last = QrProgress(state="waiting")
        while time.monotonic() < deadline:
            last = self._auth.poll(ticket)
            if on_state is not None:
                on_state(last)
            if last.state == "rejected":
                raise SignInRejected("扫码被拒绝，或二维码已作废")
            if last.state == "expired":
                raise SignInExpired("二维码已过期，请重新获取")
            if last.state == "confirmed" and last.credential:
                return self.persist(last.credential)
            self._sleep(interval)
        raise SignInExpired("等待扫码超时，请重新获取二维码")

    def persist(self, credential: str) -> SignInResult:
        """把凭据落库并读出账号档案。接口层的轮询回调也走这里，避免两套写入路径。

        每个账号一份 cookie，键带 uid（见 `domain.session`）。所以"再扫一次码"
        天然就是"加一个账号"，不需要单独的添加流程。
        """
        reader = self._connect(credential)
        profile = reader.profile()
        if not profile.uid:
            raise SignInRejected("登录成功但读不到账号信息，请稍后重试")

        key = session_key(profile.uid)
        self._vault.write(key, credential)
        account_id = self._accounts.upsert(profile, key)
        # 新扫的号直接切成当前号 —— 人扫码就是为了用它，再让他去下拉里点一下是多余的。
        self._accounts.mark_current(account_id)
        return SignInResult(
            account_id=account_id,
            profile=profile,
            encrypted=bool(getattr(self._vault, "secure", False)),
        )
