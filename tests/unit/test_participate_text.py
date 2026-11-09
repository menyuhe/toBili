"""参与文案。

转发语是这个工具唯一会被别人看见的输出。写死一句"转发抽奖"不只是难看：同一条
文案出现在几十条转发里，是最容易被判定为机器行为的特征。

这里验证两件事：什么算"能用的评论"，以及三条路失败时退得对不对。
"""

from __future__ import annotations

import random

from tobili.adapters.sqlite import Database, Repositories
from tobili.domain.participate_text import (
    MAX_LEN,
    TextMode,
    build_comment_pool,
    clean_candidate,
    fill_template,
)
from tobili.usecases.participate_text import (
    K_CUSTOM,
    K_FALLBACK,
    K_LLM_BASE,
    K_LLM_MODEL,
    K_MODE,
    LLM_KEY,
    ParticipateText,
    describe_settings,
    save_settings,
)


class _Vault:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def read(self, name: str) -> str | None:
        return self.items.get(name)

    def write(self, name: str, value: str) -> None:
        self.items[name] = value

    def erase(self, name: str) -> None:
        self.items.pop(name, None)

    def exists(self, name: str) -> bool:
        return name in self.items


class _Gateway:
    def __init__(self, messages: list[str] | None = None, *, boom: bool = False) -> None:
        self.messages = messages or []
        self.boom = boom
        self.asked: list[str] = []

    def comments(self, *, rid: str, comment_type: int = 17, **_kw) -> list[str]:  # type: ignore[no-untyped-def]
        self.asked.append(rid)
        if self.boom:
            raise RuntimeError("评论接口挂了")
        return self.messages


class _Llm:
    def __init__(self, reply: str = "", *, boom: bool = False) -> None:
        self.reply = reply
        self.boom = boom
        self.calls: list[list[dict[str, str]]] = []

    def ready(self) -> bool:
        return True

    def chat(self, messages, *, temperature: float = 0.8, max_tokens=None):  # type: ignore[no-untyped-def]
        from tobili.ports.llm import LlmError

        self.calls.append([dict(m) for m in messages])
        if self.boom:
            raise LlmError("模型欠费了")
        return self.reply

    def describe(self) -> dict:
        return {"ready": True, "model": "fake"}


def _repos(tmp_path) -> Repositories:  # type: ignore[no-untyped-def]
    db = Database(tmp_path / "t.db")
    db.migrate()
    return Repositories(db)


def _text(repos, gateway=None, llm=None) -> ParticipateText:  # type: ignore[no-untyped-def]
    return ParticipateText(
        gateway=gateway or _Gateway(),  # type: ignore[arg-type]
        settings=repos.settings,
        vault=_Vault(),  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        picker=random.Random(0),
    )


# ---- 什么算能用的评论 ----
def test_explicit_comment_literal_keeps_product_keyword():
    from tobili.domain.participate_text import required_comment_text
    assert required_comment_text('从评论"科睿X51"中随机抽取一位送【帆布袋】') == '科睿X51'
    assert required_comment_text('评论区留言：“夏天快乐” 即可') == '夏天快乐'
    assert required_comment_text('礼品【键盘】，在评论区聊聊喜欢的功能') == ''
    assert required_comment_text('评论“红色”或者评论“蓝色”') == ''


def test_enroll_literal_does_not_get_paraphrased_by_composer():
    from types import SimpleNamespace
    from tobili.usecases.enroll import Enroll
    class Composer:
        def draft(self, **kwargs):
            raise AssertionError('Explicit keyword should not call model')
    stub = SimpleNamespace(_composer=Composer())
    assert Enroll._draft(stub, SimpleNamespace(), SimpleNamespace(text='从评论"科睿X51"中抽取')) == '科睿X51'


def test_rejects_urls_mentions_and_template_words() -> None:
    assert clean_candidate("这个画风真的好看，期待后续") is not None
    assert clean_candidate("看这里 https://example.com 有惊喜") is None
    assert clean_candidate("谢谢@某某 提醒") is None
    assert clean_candidate("转发抽奖，求中") is None
    assert clean_candidate("开奖了恭喜") is None


def test_rejects_emoji_only_and_short_text() -> None:
    assert clean_candidate("[大笑][大笑][大笑]") is None
    assert clean_candidate("好") is None
    assert clean_candidate("ab") is None


def test_rejects_too_long() -> None:
    assert clean_candidate("这" * (MAX_LEN + 1)) is None


def test_pool_skips_the_first_few() -> None:
    """前几条通常是 UP 置顶和自己人，照抄它们最容易撞车。"""
    messages = [
        "置顶评论内容啊",
        "沙发位置内容啊",
        "地板评论内容啊",
        "第四条内容啊啊",
        "第五条内容啊啊",
    ]
    messages += ["这个角色我特别喜欢，画得好"]
    pool = build_comment_pool(messages)
    assert pool == ["这个角色我特别喜欢，画得好"]


def test_pool_dedupes() -> None:
    messages = ["占位占位占位占位"] * 5
    messages += ["这个作品我一直在追，太喜欢了", "这个作品我一直在追，太喜欢了"]
    pool = build_comment_pool(messages)
    assert pool == ["这个作品我一直在追，太喜欢了"]


def test_pool_stops_at_window_end() -> None:
    messages = ["占位占位占位占位"] * 5
    messages += [f"第{i}条正常评论内容" for i in range(80)]
    pool = build_comment_pool(messages)
    assert len(pool) <= 60  # 5..65 的窗口


# ---- 模板 ----
def test_fill_template_replaces_known_vars_only() -> None:
    out = fill_template(
        "来看{up}的{title}，奖品是{prize}", up_name="阿猫", title="抽奖", prize="大会员"
    )
    assert out == "来看阿猫的抽奖，奖品是大会员"
    assert fill_template("未知变量{unknown}保留", up_name="x") == "未知变量{unknown}保留"


# ---- 三条路 ----
def test_custom_mode_uses_template(repos) -> None:  # type: ignore[no-untyped-def]
    repos.settings.put(0, K_MODE, TextMode.CUSTOM.value)
    repos.settings.put(0, K_CUSTOM, "来支持{up}啦")
    draft = _text(repos).resolve(action="share", campaign_title="抽奖", up_name="阿猫")
    assert draft.text == "来支持阿猫啦"
    assert draft.source == "custom"


def test_random_comment_mode_picks_from_others(repos) -> None:  # type: ignore[no-untyped-def]
    repos.settings.put(0, K_MODE, TextMode.RANDOM_COMMENT.value)
    gateway = _Gateway(["占位占位占位占位"] * 5 + ["这个手办做工真的细，想要"])
    draft = _text(repos, gateway).resolve(
        action="share", campaign_title="抽奖", up_name="阿猫", dyn_id="d1"
    )
    assert draft.source == "random_comment"
    assert "手办" in draft.text
    assert gateway.asked == ["d1"]


def test_random_comment_falls_back_to_custom_when_empty(repos) -> None:  # type: ignore[no-untyped-def]
    """评论区关了也要能参与。一次转发没有文案是小事，漏掉活动是损失。"""
    repos.settings.put(0, K_MODE, TextMode.RANDOM_COMMENT.value)
    repos.settings.put(0, K_CUSTOM, "固定文案")
    draft = _text(repos, _Gateway([])).resolve(
        action="share", campaign_title="抽奖", up_name="阿猫", dyn_id="d1"
    )
    assert draft.text == "固定文案"
    assert draft.source == "custom"
    assert "评论区" in draft.note


def test_random_comment_survives_gateway_error(repos) -> None:  # type: ignore[no-untyped-def]
    repos.settings.put(0, K_MODE, TextMode.RANDOM_COMMENT.value)
    draft = _text(repos, _Gateway(boom=True)).resolve(
        action="share", campaign_title="抽奖", up_name="阿猫", dyn_id="d1"
    )
    assert draft.text  # 有东西可发就行


def test_llm_mode_uses_model(repos) -> None:  # type: ignore[no-untyped-def]
    repos.settings.put(0, K_MODE, TextMode.LLM.value)
    llm = _Llm("“这个角色我追了三季，终于等到手办了”")
    draft = _text(repos, llm=llm).resolve(
        action="share", campaign_title="抽奖", up_name="阿猫", body="送出三份手办"
    )
    assert draft.source == "llm"
    # 模型爱加引号，剥掉
    assert draft.text == "这个角色我追了三季，终于等到手办了"
    assert llm.calls and "送出三份手办" in llm.calls[0][1]["content"]


def test_llm_failure_falls_back_to_comments_then_custom(repos) -> None:  # type: ignore[no-untyped-def]
    repos.settings.put(0, K_MODE, TextMode.LLM.value)
    repos.settings.put(0, K_CUSTOM, "兜底文案")
    draft = _text(repos, _Gateway([]), _Llm(boom=True)).resolve(
        action="share", campaign_title="抽奖", up_name="阿猫", dyn_id="d1"
    )
    assert draft.text == "兜底文案"


def test_llm_failure_uses_comments_when_available(repos) -> None:  # type: ignore[no-untyped-def]
    """模型挂了但评论区能用时，先拿评论 —— 退两步比直接退回模板更像人写的。"""
    repos.settings.put(0, K_MODE, TextMode.LLM.value)
    gateway = _Gateway(["占位占位占位占位"] * 5 + ["这个画风我太喜欢了"])
    draft = _text(repos, gateway, _Llm(boom=True)).resolve(
        action="share", campaign_title="抽奖", up_name="阿猫", dyn_id="d1"
    )
    assert draft.source == "random_comment"


def test_unknown_mode_falls_back_to_custom(repos) -> None:  # type: ignore[no-untyped-def]
    """设置被改坏了不该让参与卡住。"""
    repos.settings.put(0, K_MODE, "不存在的模式")
    repos.settings.put(0, K_CUSTOM, "安全文案")
    draft = _text(repos).resolve(action="share", campaign_title="t", up_name="u")
    assert draft.text == "安全文案"


def test_fallback_text_is_used_when_custom_is_empty(repos) -> None:  # type: ignore[no-untyped-def]
    repos.settings.put(0, K_MODE, TextMode.CUSTOM.value)
    repos.settings.put(0, K_CUSTOM, "   ")
    repos.settings.put(0, K_FALLBACK, "备用一句")
    draft = _text(repos).resolve(action="share", campaign_title="t", up_name="u")
    assert draft.text == "备用一句"


# ---- 设置 ----
def test_save_settings_writes_only_given_fields(repos) -> None:  # type: ignore[no-untyped-def]
    vault = _Vault()
    save_settings(repos.settings, vault, custom="第一版", llm_base="https://a.com")
    save_settings(repos.settings, vault, fallback="备用")

    assert repos.settings.get(0, K_CUSTOM) == "第一版"
    assert repos.settings.get(0, K_FALLBACK) == "备用"
    assert repos.settings.get(0, K_LLM_BASE) == "https://a.com"


def test_api_key_is_never_echoed(repos) -> None:  # type: ignore[no-untyped-def]
    """密钥只说"设了没"。回显一次就会被截图、被贴进聊天。"""
    vault = _Vault()
    save_settings(repos.settings, vault, llm_key="sk-secret-value")
    body = describe_settings(repos.settings, vault, None)

    assert body["llm"]["key_set"] is True
    assert "sk-secret-value" not in str(body)


def test_empty_key_does_not_erase_existing(repos) -> None:  # type: ignore[no-untyped-def]
    """界面上那个密码框永远是空的；保存时留空表示不改，不是清空。"""
    vault = _Vault()
    save_settings(repos.settings, vault, llm_key="sk-original")
    save_settings(repos.settings, vault, llm_key="", llm_model="gpt-4o-mini")

    assert vault.read(LLM_KEY) == "sk-original"
    assert repos.settings.get(0, K_LLM_MODEL) == "gpt-4o-mini"
