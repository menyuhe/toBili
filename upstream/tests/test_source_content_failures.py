from unittest.mock import Mock, patch

import httpx
import pytest

from src.bilibili_client import BilibiliClient
from src.dead_links import DynamicDeletedError, DynamicPageUnavailableError
from src.forward_parser import DynamicContentFetchError, fetch_dynamic_content_with_retry
from src.pipeline.classify_step import ClassifyOutcome, classify_new_link
from src.pipeline.refresh_all_pipeline import run_refresh_all_pipeline
from src.source_content_retry import include_pending, save_pending
from src.sources.common import CheckResult


def source(links, updated=True, container='1'):
    return CheckResult('DS3', updated, 'https://www.bilibili.com/read/cv1', container,
                       'test', 0, None, links, 0)


def test_soft_404_is_not_successful_html():
    client = object.__new__(BilibiliClient)
    client._http_get = Mock(return_value=httpx.Response(200, text='error page',
        request=httpx.Request('GET', 'https://www.bilibili.com/404')))
    with pytest.raises(DynamicPageUnavailableError, match='404'):
        client.get_text('https://www.bilibili.com/opus/1264195023432318983')
    assert client._http_get.call_count == 1


def test_soft_404_remains_retryable_in_classifier():
    client = Mock()
    client.get_text.side_effect = DynamicPageUnavailableError('跳转 B站 404，保留待重试')
    with patch('src.pipeline.classify_fetch_context.ClassifyFetchContext.is_deleted_link', return_value=False), patch(
        'src.pipeline.classify_fetch_context.ClassifyFetchContext.get_additional', return_value=None
    ), patch('src.pipeline.classify_step.fetch_lottery_notice', return_value=None), patch(
        'src.forward_parser.time.sleep'
    ), patch('src.lottery_api._fetch_dynamic_api_item', return_value=None), patch('src.lottery_api._fetch_opus_detail_item', return_value=None):
        with pytest.raises(DynamicContentFetchError, match='404'):
            classify_new_link(client, '1264195023432318983')


def test_soft_404_bubbles_through_content_retries():
    with patch('src.forward_parser.fetch_dynamic_content', side_effect=DynamicDeletedError('1264195023432318983')) as fetch:
        with pytest.raises(DynamicDeletedError):
            fetch_dynamic_content_with_retry(Mock(), '1264195023432318983')
        assert fetch.call_count == 1


def test_content_error_retains_endpoint_reason():
    with patch('src.forward_parser.fetch_dynamic_content', side_effect=DynamicContentFetchError('dynamic/detail HTTP 412')), patch('src.forward_parser.time.sleep'):
        with pytest.raises(DynamicContentFetchError, match='1264195023432318983.*HTTP 412'):
            fetch_dynamic_content_with_retry(Mock(), '1264195023432318983')


def test_batch_continues_and_retries_across_changed_container(isolated_home):
    first = source(['https://www.bilibili.com/opus/1264195023432318983', 'https://www.bilibili.com/opus/1264195023432318984'])
    with patch('src.pipeline.refresh_all_pipeline.BilibiliClient'), patch(
        'src.pipeline.refresh_all_pipeline.classify_new_link',
        side_effect=[DynamicContentFetchError('HTTP 412'), ClassifyOutcome('1264195023432318983', None, True, '链接失效')],
    ) as classify:
        result = run_refresh_all_pipeline([first])
    assert classify.call_count == 2
    assert result.deferred_ids == ['1264195023432318984']
    assert 'HTTP 412' in result.diagnostics[0]
    changed = include_pending(source(['https://www.bilibili.com/opus/1264195023432318985'], container='2'))
    assert 'https://www.bilibili.com/opus/1264195023432318984' in changed.activity_links
    unchanged = include_pending(source([], updated=False))
    assert unchanged.updated and unchanged.activity_links == ['https://www.bilibili.com/opus/1264195023432318984']
    save_pending([unchanged], [])
    assert not include_pending(source([], updated=False)).updated


def test_pending_not_dropped_by_source_limit(isolated_home):
    save_pending([source(['https://www.bilibili.com/opus/1264195023432318001'])], ['1264195023432318001'])
    changed = source([f'https://www.bilibili.com/opus/{n}' for n in range(1264195023432318100, 1264195023432318500)])
    assert 'https://www.bilibili.com/opus/1264195023432318001' in include_pending(changed).activity_links


def test_api_error_code_not_lost():
    from src.lottery_api import probe_dynamic_detail_api
    client = Mock()
    client.get_json.side_effect = RuntimeError('API error -404: not found')
    with patch('src.lottery_api.is_detail_api_enabled', return_value=True):
        assert probe_dynamic_detail_api(client, '1264195023432318983') == (None, -404, 'not found')
