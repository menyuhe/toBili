"""通知：何时发、发到哪、发不出去怎么办。

三个决定：

1. **默认关闭。** 装好就在群里刷消息的软件，第一件事是被人踢出群。
2. **webhook 存设置、secret 存保险库。** 前者是地址，后者是密钥。规则简单：
   任何能让人往群里发消息的东西，都不该以明文躺在 sqlite 里。
3. **发不出去只记一笔，不重试、不抛。** 通知是旁路，不是主流程的一环。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.inbox import notify_line
from ..ports.collab import Notifier, SecretStore
from ..ports.repo import SettingRepo

GLOBAL = 0

K_ENABLED = "notify.dingtalk.enabled"
K_WEBHOOK = "notify.dingtalk.webhook"
DINGTALK_SECRET = "notify-dingtalk-secret"


@dataclass(frozen=True, slots=True)
class NotifyState:
    enabled: bool
    webhook: str
    """已经遮过的地址。**原样不回传** —— 界面上只需要认出"配过没有"。"""
    has_webhook: bool
    has_secret: bool


def mask_webhook(url: str) -> str:
    """钉钉的 webhook 里带着 access_token，等于半个密码。

    留前后一小段，够人确认"我配的是这个群"，不够别人拿去用。
    """
    value = (url or "").strip()
    if not value:
        return ""
    if len(value) <= 42:
        return value[:12] + "…"
    return f"{value[:34]}…{value[-6:]}"


class Notify:
    def __init__(
        self,
        *,
        settings: SettingRepo,
        vault: SecretStore,
        channel: Notifier | None = None,
    ) -> None:
        self._settings = settings
        self._vault = vault
        self._channel = channel

    # ---- 配置 ----
    def state(self) -> NotifyState:
        webhook = self._settings.get(GLOBAL, K_WEBHOOK, "")
        return NotifyState(
            enabled=self._settings.get(GLOBAL, K_ENABLED, "") == "1",
            webhook=mask_webhook(webhook),
            has_webhook=bool(webhook.strip()),
            has_secret=bool((self._vault.read(DINGTALK_SECRET) or "").strip()),
        )

    def save(self, *, webhook: str | None, secret: str | None, enabled: bool) -> NotifyState:
        """`webhook`/`secret` 传 None 表示"别动它"。

        界面上这两个框存完就清空了（读不回来），所以"保存一次没改的配置"
        必须是无害的 —— 否则随手点一下保存就把密钥抹了。
        """
        if webhook is not None:
            self._settings.put(GLOBAL, K_WEBHOOK, webhook.strip())
        self._settings.put(GLOBAL, K_ENABLED, "1" if enabled else "0")
        if secret is not None and secret.strip():
            self._vault.write(DINGTALK_SECRET, secret.strip())
        return self.state()

    def clear(self) -> NotifyState:
        self._settings.put(GLOBAL, K_WEBHOOK, "")
        self._settings.put(GLOBAL, K_ENABLED, "0")
        self._vault.erase(DINGTALK_SECRET)
        return self.state()

    # ---- 发送 ----
    def test(self) -> tuple[bool, str]:
        """发一条测试。失败原因原样返回，界面上要能照着改。"""
        if self._channel is None or not self.state().has_webhook:
            return False, "还没有配置 webhook"
        ok = self._channel.send("toBili 测试通知", "如果你看到这条，说明通知配好了。", level="ok")
        return (True, "已发出") if ok else (False, "发不出去，检查 webhook 或加签密钥")

    def enabled(self) -> bool:
        return self._channel is not None and self.state().enabled and self.state().has_webhook

    def send(self, title: str, body: str, *, level: str = "info") -> bool:
        if not self.enabled():
            return False
        if self._channel is None:
            return False
        return self._channel.send(title, body, level=level)

    # ---- 该不该发 ----
    def about_inbox(self, items) -> bool:  # type: ignore[no-untyped-def]
        """收件箱里出现"可能在说中奖"的消息时发一条。

        没有中奖消息就不发 —— "有 12 条未读"这种通知，只会让人把通知关掉。
        """
        line = notify_line(list(items))
        if not line:
            return False
        return self.send("可能有中奖消息", line, level="warn")

    def about_round(self, *, failed: int, problems: list[str], settled: int) -> bool:
        """定时轮结束。只在"出了问题"或"真的做了事"时发，全部无事发生就不发。"""
        if failed or problems:
            body = "\n".join([f"失败 {failed} 条", *problems[:5]])
            return self.send("这一轮有异常", body, level="danger")
        if settled:
            return self.send("这一轮跑完了", f"成功 {settled} 条", level="ok")
        return False
