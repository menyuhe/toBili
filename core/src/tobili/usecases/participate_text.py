"""参与文案的编排：选哪条路、失败了往哪退。

三条路的可靠性差得很远，所以顺序不是按"哪个好"排，而是按"哪个一定成"排：

    先按用户选的模式试 → 不行退到 fallback → 还不行给一句最土的默认值

`random_comment` 和 `llm` 都可能失败（评论区关了、模型欠费、网络抖动），但**参与
本身不该因此失败**。一次转发没有文案是小事，漏掉一个活动是实打实的损失。

每次解析都返回 `Draft` 而不是裸字符串：`source` 要能被事件流和界面看见。用户有权
知道"这条是我自己写的"还是"模型随手编的"。
"""

from __future__ import annotations

import random

from ..domain.participate_text import (
    DEFAULT_CUSTOM,
    DEFAULT_FALLBACK,
    Draft,
    TextMode,
    build_comment_pool,
    fill_template,
    llm_messages,
    trim,
)
from ..ports.bili import Gateway
from ..ports.collab import SecretStore
from ..ports.llm import Llm, LlmError
from ..ports.repo import SettingRepo

LLM_KEY = "llm_api_key"

# 设置键。放常量而不是散在字符串里，是因为它们会被 API、界面、CLI 三处读，
# 拼错一个的表现是"设置了但不生效"，查起来很费劲。
K_MODE = "text.mode"
K_CUSTOM = "text.custom"
K_FALLBACK = "text.fallback"
K_LLM_BASE = "llm.base_url"
K_LLM_MODEL = "llm.model"
K_LLM_ON = "llm.enabled"


class ParticipateText:
    def __init__(
        self,
        *,
        gateway: Gateway,
        settings: SettingRepo,
        vault: SecretStore,
        llm: Llm | None = None,
        picker: random.Random | None = None,
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._vault = vault
        self._llm = llm
        # 可注入是为了测试能稳定复现"挑中了哪一条"。
        self._random = picker or random.Random()

    # ---- 供用例调用 ----
    def draft(
        self,
        *,
        action: str,
        campaign_title: str,
        up_name: str,
        dyn_id: str = "",
        body: str = "",
    ) -> str:
        return self.resolve(
            action=action,
            campaign_title=campaign_title,
            up_name=up_name,
            dyn_id=dyn_id,
            body=body,
        ).text

    def resolve(
        self,
        *,
        action: str,
        campaign_title: str,
        up_name: str,
        dyn_id: str = "",
        body: str = "",
        rid: str = "",
        comment_type: int = 17,
    ) -> Draft:
        account = 0  # 设置按全局存，不跟账号走：换号不该丢了自己写的文案
        mode = self._mode(account)

        if mode is TextMode.CUSTOM:
            return self._custom(account, up_name=up_name, title=campaign_title)
        if mode is TextMode.RANDOM_COMMENT:
            draft = self._from_comments(rid or dyn_id, comment_type=comment_type)
            if draft is not None:
                return draft
            return self._custom(
                account, up_name=up_name, title=campaign_title, note="评论区没挑到能用的"
            )
        if mode is TextMode.LLM:
            draft = self._from_llm(
                up_name=up_name, title=campaign_title, body=body or campaign_title
            )
            if draft is not None:
                return draft
            # 模型不可用时先试评论，再退回模板 —— 退两步比一步到位更可能拿到"像人写的"。
            draft = self._from_comments(rid or dyn_id, comment_type=comment_type)
            if draft is not None:
                return Draft(draft.text, draft.source, note="模型不可用，改用评论区")
            return self._custom(account, up_name=up_name, title=campaign_title, note="模型不可用")
        return self._fallback(account)

    # ---- 内部 ----
    def _mode(self, account: int) -> TextMode:
        raw = self._settings.get(account, K_MODE, TextMode.CUSTOM.value)
        try:
            return TextMode(raw)
        except ValueError:
            # 设置坏了不该让参与卡住，按最稳的那条路走。
            return TextMode.CUSTOM

    def _custom(
        self,
        account: int,
        *,
        up_name: str,
        title: str,
        note: str = "",
    ) -> Draft:
        template = self._settings.get(account, K_CUSTOM, DEFAULT_CUSTOM)
        text = trim(fill_template(template, up_name=up_name, title=title))
        if not text:
            # 用户把自定义文案清空了。走备用句，而不是塞回内置默认值 ——
            # 他清空的意思就是"别用那句"。
            return self._fallback(account)
        return Draft(text, "custom", note=note)

    def _fallback(self, account: int) -> Draft:
        text = trim(self._settings.get(account, K_FALLBACK, DEFAULT_FALLBACK))
        return Draft(text or DEFAULT_FALLBACK, "fallback")

    def _from_comments(self, rid: str, *, comment_type: int) -> Draft | None:
        if not rid:
            return None
        try:
            messages = self._gateway.comments(rid=rid, comment_type=comment_type)
        except Exception:
            return None
        pool = build_comment_pool(messages)
        if not pool:
            return None
        picked = self._random.choice(pool)
        return Draft(trim(picked), "random_comment", note=f"从 {len(pool)} 条评论里挑的")

    def _from_llm(self, *, up_name: str, title: str, body: str) -> Draft | None:
        if self._llm is None or not self._llm.ready():
            return None
        try:
            text = self._llm.chat(
                llm_messages(up_name=up_name, title=title, body=body),
                temperature=0.9,
                max_tokens=120,
            )
        except LlmError:
            return None
        except Exception:
            # 适配器之外的意外（比如 DNS 层面的东西）。退到下一页比抛出去好。
            return None
        cleaned = _clean_llm_reply(text)
        if not cleaned:
            return None
        return Draft(trim(cleaned), "llm", note="模型生成")


def _clean_llm_reply(text: str) -> str:
    """模型偶尔会加引号、加"评论："、加两行。

    这些不是模型的错，是 prompt 的边界情况；在这里剥掉比在 prompt 里写十条禁令可靠。
    """
    value = (text or "").strip()
    for prefix in ("评论：", "评论:", "转发语：", "转发语:", "回复："):
        if value.startswith(prefix):
            value = value[len(prefix) :].strip()
    value = value.strip("\"'“”「」『』 ")
    value = value.splitlines()[0].strip() if value else ""
    return value


def describe_settings(settings: SettingRepo, vault: SecretStore, llm: Llm | None) -> dict:
    """界面上要显示的东西。密钥只说"设了没"，绝不回显。"""
    account = 0
    import json

    health = json.loads(settings.get(0, "llm.health", "{}"))
    return {
        "mode": settings.get(account, K_MODE, TextMode.CUSTOM.value),
        "custom": settings.get(account, K_CUSTOM, DEFAULT_CUSTOM),
        "fallback": settings.get(account, K_FALLBACK, DEFAULT_FALLBACK),
        "llm": {
            "scan_enabled": settings.get(account, "llm.scan", "0") == "1",
            "enabled": settings.get(account, K_LLM_ON, "0") == "1",
            "base_url": settings.get(account, K_LLM_BASE, ""),
            "model": settings.get(account, K_LLM_MODEL, ""),
            "key_set": vault.exists(LLM_KEY),
            "ready": bool(llm and llm.ready()),
            "health": {k: health[k] for k in ("ok", "error", "at", "retry_at") if k in health},
        },
    }


def save_settings(
    settings: SettingRepo,
    vault: SecretStore,
    *,
    mode: str | None = None,
    custom: str | None = None,
    fallback: str | None = None,
    llm_base: str | None = None,
    llm_model: str | None = None,
    llm_key: str | None = None,
    llm_enabled: bool | None = None,
    scan_enabled: bool | None = None,
) -> None:
    """只写传进来的字段。None 表示"别动它"——空字符串是有效的值（清空）。"""
    account = 0
    if mode is not None:
        TextMode(mode)  # 非法值直接抛，别把坏配置写进库
        settings.put(account, K_MODE, mode)
    if custom is not None:
        settings.put(account, K_CUSTOM, custom)
    if fallback is not None:
        settings.put(account, K_FALLBACK, fallback)
    if llm_base is not None:
        settings.put(account, K_LLM_BASE, llm_base.strip())
    if llm_model is not None:
        settings.put(account, K_LLM_MODEL, llm_model.strip())
    if scan_enabled is not None:
        settings.put(account, "llm.scan", "1" if scan_enabled else "0")
    if llm_enabled is not None:
        settings.put(account, K_LLM_ON, "1" if llm_enabled else "0")
    # 密钥单独存：只在传了非空值时覆盖。留空表示"不改"，否则界面上那个永远
    # 显示为空密码框，一保存就把真密钥清掉了。
    if llm_key:
        vault.write(LLM_KEY, llm_key.strip())


def dump_preview(draft: Draft) -> dict:
    return {
        "text": draft.text,
        "source": draft.source,
        "note": draft.note,
        "fallback": draft.fallback,
    }


__all__ = [
    "K_CUSTOM",
    "K_FALLBACK",
    "K_LLM_BASE",
    "K_LLM_MODEL",
    "K_LLM_ON",
    "K_MODE",
    "LLM_KEY",
    "ParticipateText",
    "describe_settings",
    "dump_preview",
    "save_settings",
]
