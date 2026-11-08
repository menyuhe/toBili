"""OpenAI 兼容的对话接口。

国内能买到的模型（DeepSeek、通义、Kimi、智谱、以及各种中转）几乎都提供
`/chat/completions`。所以这里不做供应商适配矩阵，只处理一个形状：

    POST {base_url}/chat/completions
    {model, messages, temperature, max_tokens}
    → choices[0].message.content

三件必须做对的事：

1. **base_url 归一化**。人会填 `https://api.deepseek.com`、填 `.../v1`、也很可能
   直接贴一个完整的 `.../v1/chat/completions`。三种都得能用，否则第一次配置就失败，
   而失败信息通常还看不出是地址拼错了。
2. **错误脱敏**。异常消息会被原样显示在界面上，密钥不能出现在里面。
3. **失败要短**。转发一条动态不值得等 90 秒。超时按 30 秒，重试只对 429/5xx 做，
   最多两次 —— 限流时把主流程拖住比文案写得差严重得多。
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from ...ports.llm import LlmError

_TIMEOUT = (10, 30)
_RETRIES = 2
_RETRY_STATUS = {408, 429, 500, 502, 503, 504}


def normalize_base(url: str) -> str:
    """把人给的地址补成 `.../v1/chat/completions`。

    宁可多容错几种写法：这里猜错一次的代价是"配了但用不了"，而且报错只会说
    "连接失败"，用户根本看不出是地址少了 `/v1`。
    """
    value = (url or "").strip().rstrip("/")
    if not value:
        return ""
    if value.endswith("/chat/completions"):
        return value
    if value.endswith("/v1") or value.endswith("/v1beta"):
        return value + "/chat/completions"
    return value + "/v1/chat/completions"


class OpenAICompatible:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: tuple[float, float] = _TIMEOUT,
    ) -> None:
        self._endpoint = normalize_base(base_url)
        self._key = (api_key or "").strip()
        self._model = (model or "").strip()
        self._timeout = timeout

    def ready(self) -> bool:
        return bool(self._endpoint and self._key and self._model)

    def describe(self) -> Mapping[str, object]:
        # 只回模型的"名字"，不回密钥，也不回完整地址里的 query。
        return {
            "ready": self.ready(),
            "model": self._model,
            "endpoint": _host_of(self._endpoint),
            "key_set": bool(self._key),
        }

    def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float = 0.8,
        max_tokens: int | None = None,
    ) -> str:
        if not self.ready():
            raise LlmError("还没有配置模型地址、密钥或模型名")
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [dict(m) for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        # Small extraction/text jobs need an answer within their output budget.
        # Only the official DeepSeek endpoint gets its provider-specific switch.
        from urllib.parse import urlsplit

        if urlsplit(self._endpoint).hostname == "api.deepseek.com":
            payload["thinking"] = {"type": "disabled"}

        last: Exception | None = None
        for attempt in range(_RETRIES + 1):
            try:
                response = httpx.post(
                    self._endpoint,
                    json=payload,
                    timeout=self._timeout,
                    headers={
                        "Authorization": f"Bearer {self._key}",
                        "Content-Type": "application/json",
                    },
                )
            except httpx.HTTPError as exc:
                last = exc
                if attempt >= _RETRIES:
                    break
                time.sleep(0.8 * (attempt + 1))
                continue

            if response.status_code in _RETRY_STATUS and attempt < _RETRIES:
                last = LlmError(_explain(response.status_code, response.text))
                time.sleep(0.8 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise LlmError(_explain(response.status_code, response.text))
            return _content_of(response)

        raise LlmError(_explain_network(last))

    def test(self) -> str:
        """连通性测试。只让模型回一句话，成本可忽略。"""
        return self.chat(
            [{"role": "user", "content": "回复 OK 两个字，不要别的。"}],
            temperature=0,
            max_tokens=64,
        )


def _host_of(endpoint: str) -> str:
    from urllib.parse import urlparse

    try:
        return urlparse(endpoint).netloc or "（无效地址）"
    except ValueError:
        return "（无效地址）"


def _content_of(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError as exc:
        raise LlmError("模型返回的不是 JSON，可能地址指到了网页而不是接口") from exc
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        message = body.get("error") or body.get("message") or "返回里没有 choices"
        raise LlmError(str(message)[:200])
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first, dict) else None
    text = ""
    if isinstance(message, dict):
        text = str(message.get("content") or "").strip()
        if not text and message.get("reasoning_content"):
            # DeepSeek V4 这类模型把推理过程放在这里，正文可能是空的。
            raise LlmError("模型只返回了推理内容，没有正文；请换一个非推理模型或加大 max_tokens")
    if not text:
        raise LlmError("模型返回了空内容")
    return text


def _explain(status: int, raw: str) -> str:
    """把 HTTP 状态翻成人话。不回显整个响应体 —— 里面可能带账号信息。"""
    detail = ""
    try:
        body = json.loads(raw)
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                detail = str(error.get("message") or "")[:160]
            else:
                detail = str(body.get("message") or body.get("detail") or "")[:160]
    except (TypeError, ValueError):
        detail = (raw or "").strip()[:160]
    suffix = f"：{detail}" if detail else ""
    if status == 401:
        return f"密钥无效或已过期{suffix}"
    if status == 403:
        return f"密钥没有这个模型的权限{suffix}"
    if status == 404:
        return f"接口地址或模型名不存在{suffix}"
    if status == 429:
        return f"触发限流，稍后再试{suffix}"
    if status in {500, 502, 503, 504}:
        return f"模型服务暂时不可用（HTTP {status}）{suffix}"
    return f"调用失败（HTTP {status}）{suffix}"


def _explain_network(exc: Exception | None) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "连模型超时了，检查网络或换个地址"
    if isinstance(exc, httpx.ConnectError):
        return "连不上模型服务，检查地址与网络"
    if exc is None:
        return "调用失败"
    # 兜底：异常消息里可能带完整 URL，只留类型和很短的一截。
    return f"调用失败（{type(exc).__name__}）"
