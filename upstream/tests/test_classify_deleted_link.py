from __future__ import annotations

from src.pipeline.classify_fetch_context import ClassifyFetchContext

import httpx
import pytest
from unittest.mock import Mock, patch
from src.pipeline.classify_step import classify_new_link
from src.forward_parser import DynamicContentFetchError


@pytest.mark.parametrize("status", [404, 410])
def test_missing_reserve_page_is_skipped(status):
    client = Mock()
    response = httpx.Response(status, request=httpx.Request("GET", "https://www.bilibili.com/opus/1258624914709020678"))
    client.get_text.side_effect = httpx.HTTPStatusError("unavailable", request=response.request, response=response)
    with patch.object(ClassifyFetchContext, "is_deleted_link", return_value=False), patch.object(
        ClassifyFetchContext, "get_additional", return_value=None
    ), patch("src.pipeline.classify_step.fetch_lottery_notice", return_value=None):
        result = classify_new_link(client, "1258624914709020678")
    assert result.skipped and result.skip_reason == "链接失效"


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_temporary_http_failures_are_not_marked_deleted(status):
    response = httpx.Response(status, request=httpx.Request("GET", "https://www.bilibili.com/opus/123"))
    error = httpx.HTTPStatusError("retry later", request=response.request, response=response)
    with patch("src.pipeline.classify_step._classify_new_link", side_effect=error), pytest.raises(DynamicContentFetchError, match=f'HTTP {status}'):
        classify_new_link(Mock(), "123")


@pytest.mark.parametrize("url", ["https://api.bilibili.com/x/dynamic/detail", "https://llm.example/v1/chat/completions"])
def test_missing_api_endpoint_is_not_a_deleted_activity(url):
    response = httpx.Response(404, request=httpx.Request("GET", url))
    error = httpx.HTTPStatusError("not found", request=response.request, response=response)
    with patch("src.pipeline.classify_step._classify_new_link", side_effect=error), pytest.raises(httpx.HTTPStatusError):
        classify_new_link(Mock(), "123")


def test_dead_link_does_not_prevent_following_activity_persistence(isolated_home):
    from src.pipeline.classify_step import ClassifyOutcome
    from src.pipeline.refresh_all_pipeline import run_new_links_pipeline
    from src.activity_store import known_activity_ids
    dead, alive = "1258624914709020678", "1258624914709020679"
    response = httpx.Response(404, request=httpx.Request("GET", f"https://www.bilibili.com/opus/{dead}"))
    error = httpx.HTTPStatusError("not found", request=response.request, response=response)
    enriched = Mock(skipped=False)
    enriched.to_dict.return_value = {"dynamic_id": alive, "lottery_type": "互动抽奖", "draw_status": "active"}
    with patch("src.pipeline.refresh_all_pipeline.BilibiliClient"), patch(
        "src.pipeline.classify_step._classify_new_link",
        side_effect=[error, ClassifyOutcome(alive, "互动抽奖", False)],
    ), patch("src.pipeline.refresh_all_pipeline.enrich_activity", return_value=enriched), patch(
        "src.pipeline.refresh_all_pipeline.apply_initial_status", side_effect=lambda row: row
    ):
        result = run_new_links_pipeline([f"https://www.bilibili.com/opus/{did}" for did in (dead, alive)])
    assert result.persisted_count == 1
    assert result.skip_reasons == {"链接失效": 1}
    assert alive in known_activity_ids() and dead not in known_activity_ids()


def test_is_deleted_link_reuses_detail_probe(monkeypatch) -> None:
    calls = {"n": 0}

    class _Client:
        pass

    def fake_probe(client, dynamic_id, *, retries=2):
        calls["n"] += 1
        return None, 0, "0"

    monkeypatch.setattr(
        "src.pipeline.classify_fetch_context.probe_dynamic_detail_api",
        fake_probe,
    )
    monkeypatch.setattr(
        "src.pipeline.classify_fetch_context.is_detail_api_enabled",
        lambda: True,
    )

    ctx = ClassifyFetchContext(_Client(), "123")
    assert ctx.is_deleted_link() is True
    assert ctx.get_additional() is None
    assert calls["n"] == 1
