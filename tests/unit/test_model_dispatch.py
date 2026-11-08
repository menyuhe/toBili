import threading
from types import SimpleNamespace

import httpx
import pytest

from tobili.adapters.llm.openai import OpenAICompatible
from tobili.ports.llm import LlmError
from tobili.runtime.events import EventBus
from tobili.runtime.model_dispatch import ModelDispatch


def test_small_deepseek_jobs_disable_thinking_only_on_official_host(monkeypatch):
    bodies = []

    def post(*args, **kw):
        bodies.append(kw["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setattr(httpx, "post", post)
    for host in ["api.deepseek.com", "other.example"]:
        assert (
            OpenAICompatible(
                base_url="https://" + host, api_key="fixture", model="deepseek-flash"
            ).test()
            == "OK"
        )
    assert bodies[0]["thinking"] == {"type": "disabled"}
    assert "thinking" not in bodies[1]


def test_model_backoff_survives_recreated_jobs_and_explicit_test_recovers(repos):
    calls = []

    def chat(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise LlmError("service unavailable")
        return "OK"

    client = SimpleNamespace(ready=lambda: True, describe=lambda: {"model": "fixture"}, chat=chat)
    bus = EventBus()
    gate = threading.Lock()

    def job():
        return ModelDispatch(client, repos.settings, gate, bus)

    with pytest.raises(LlmError):
        job().chat([])
    with pytest.raises(LlmError, match="冷却"):
        job().chat([])
    assert len(calls) == 1
    assert job().test() == "OK" and len(calls) == 2
    assert [e.payload["ok"] for e in bus.since(0)] == [False, True]
