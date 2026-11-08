"""Serialize local model work and persist provider backoff across task rounds."""

import hashlib
import json
import time

from ..ports.llm import LlmError
from .redact import scrub_text


class ModelDispatch:
    def __init__(self, client, settings, gate, events):
        self.client, self.settings, self.gate, self.events = client, settings, gate, events
        self.signature = hashlib.sha256(
            json.dumps(dict(client.describe()), sort_keys=True).encode()
        ).hexdigest()

    def ready(self):
        return self.client.ready()

    def describe(self):
        return self.client.describe()

    def chat(self, messages, *, temperature=0.8, max_tokens=None):
        if not self.gate.acquire(timeout=2):
            raise LlmError("模型正在处理其他任务，本次使用本地规则")
        try:
            health = json.loads(self.settings.get(0, "llm.health", "{}"))
            if (
                health.get("signature") == self.signature
                and health.get("retry_at", 0) > time.time()
            ):
                raise LlmError("模型调用冷却中：" + health.get("error", "稍后重试"))
            try:
                result = self.client.chat(messages, temperature=temperature, max_tokens=max_tokens)
            except Exception as exc:
                message = scrub_text(str(exc))
                self._record(False, message, time.time() + 600)
                raise LlmError(message) from exc
            self._record(True, "", 0)
            return result
        finally:
            self.gate.release()

    def _record(self, ok, error, retry_at):
        health = dict(
            signature=self.signature, ok=ok, error=error, retry_at=retry_at, at=time.time()
        )
        self.settings.put(0, "llm.health", json.dumps(health))
        self.events.publish(
            "llm",
            {
                "ok": ok,
                "model": self.describe().get("model", ""),
                "title": "模型调用完成" if ok else "模型调用失败 · " + error,
            },
        )

    def test(self):
        # An explicit connection test can recover from a persisted cooldown.
        self.settings.put(0, "llm.health", "{}")
        return self.chat(
            [{"role": "user", "content": "回复 OK 两个字，不要别的。"}],
            temperature=0,
            max_tokens=64,
        )
