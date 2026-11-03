from unittest.mock import MagicMock, patch

import pytest
import yaml
from pathlib import Path

from src.sources.article_source import NEW_ARTICLE_SOURCES
from src.sources.common import commit_source_checkpoint
from src.state_store import get_last_container
from web.actions import DS_HANDLERS, DS_HANDLER_BY_ID
from web.activity_service import get_summary


@pytest.mark.parametrize("source", NEW_ARTICLE_SOURCES)
def test_new_source_fetch_cache_force_and_twenty_limit(isolated_home, monkeypatch, source):
    monkeypatch.setattr("src.sources.article_source.DATA_DIR", isolated_home / "data")
    client = MagicMock()
    client.get_latest_article.return_value = {"id": 53342386, "title": "今日合集", "publish_time": 1791460806}
    ids = [str(1256000000000000000 + i) for i in range(25)]
    container = "1256000000000000100"
    client.get_article_detail.return_value = {"opus": {"opus_id": container, "content": {"paragraphs": [
        {"text": {"nodes": [{"node_type": 4, "link": {"link": f"https://www.bilibili.com/opus/{i}"}} for i in [container, *ids, ids[-1]]]}}
    ]}}}
    with patch("src.sources.article_source.BilibiliClient") as factory:
        factory.return_value.__enter__.return_value = client
        result = source.check_update()
        client.get_latest_article.assert_called_once_with(source.mid)
        assert result.updated
        assert len(result.activity_links) == 20
        assert result.activity_links[0].endswith(ids[-1])
        assert not any(link.endswith(container) for link in result.activity_links)
        assert get_last_container(source.source_id) is None
        source.save_result(result)
        commit_source_checkpoint(result)
        cached = source.check_update()
        assert not cached.updated
        assert cached.activity_links == result.activity_links
        assert client.get_article_detail.call_count == 1
        assert source.check_update(force=True).updated
        assert client.get_article_detail.call_count == 2


def test_active_backup_configuration_and_summary_match(isolated_home):
    config = yaml.safe_load((Path(__file__).resolve().parents[1] / "config/sources.yaml").read_text(encoding="utf-8"))
    active = [key for key, value in config.items() if not value.get("backup")]
    assert active == [key for key, _, _ in DS_HANDLERS]
    assert len(active) == 7
    from src.source_registry import catalog
    assert set(config) == {r["id"] for r in catalog()}
    summary = get_summary()
    assert [s["id"] for s in summary["sources"]] == list(config)
    assert [s["id"] for s in summary["sources"] if s["backup"]] == ["DS-7", "DS-11"]
    assert all("published_at_text" in s for s in summary["sources"])
