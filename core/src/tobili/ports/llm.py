"""大模型端口。

刻意只保留一个方法：`chat(messages) -> str`。不做 function call、不做流式、
不做多模态 —— 这个项目里 LLM 只干两件事：提取活动资料、生成参与文案。为这两件事
搭一套通用 LLM 框架，维护成本会超过它省下的判断力。

协议是 OpenAI 兼容的（`/chat/completions`），因为国内能买到的模型几乎都提供
这个形状。换供应商只改 base_url 和模型名。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol


class LlmError(RuntimeError):
    """调用失败。消息里已经抹掉密钥，可以直接显示给人看。"""


class Llm(Protocol):
    def ready(self) -> bool:
        """配置齐了没有。没配好时调用方不该尝试，也不该把它当故障。"""
        ...

    def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float = 0.8,
        max_tokens: int | None = None,
    ) -> str:
        """一轮对话，返回纯文本。失败抛 `LlmError`。"""
        ...

    def describe(self) -> Mapping[str, object]:
        """当前配置（**不含密钥**）。界面上要显示"我连的是哪个模型"。"""
        ...
