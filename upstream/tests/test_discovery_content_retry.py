from unittest.mock import MagicMock, Mock, patch

import pytest

from src import lottery_discovery as discovery
from src.activity_store import known_activity_ids
from src.forward_parser import DynamicContentFetchError, fetch_dynamic_content_with_retry
from src.pipeline.classify_step import ClassifyOutcome
from src.pipeline.refresh_all_pipeline import run_new_links_pipeline
from src.task_control import TaskCancelled


def test_exhausted_content_fetch_retains_typed_error():
    with patch('src.forward_parser.fetch_dynamic_content', side_effect=DynamicContentFetchError('empty')), patch(
        'src.forward_parser.time.sleep'
    ), pytest.raises(DynamicContentFetchError, match='无法获取动态正文: 123') as error:
        fetch_dynamic_content_with_retry(Mock(), '123')
    assert error.value.dynamic_id == '123'


def test_unavailable_content_keeps_other_activities_and_retries_next_run(isolated_home, monkeypatch):
    bad, good = '1245655238775406594', '1245655238775406593'
    monkeypatch.setattr(discovery, 'TOPICS', ((1, '测试话题'), (2, '另一个话题')))
    monkeypatch.setattr(discovery, 'BilibiliClient', MagicMock())
    cards = [{'dynamic_card_item': {'id_str': did, 'modules': {'module_author': {'pub_ts': __import__('time').time()}}}} for did in (bad, good)]
    monkeypatch.setattr(discovery, '_page', lambda *args: (cards, '', False))
    activity = Mock(skipped=False)
    activity.to_dict.return_value = {'dynamic_id': good, 'lottery_type': '互动抽奖', 'draw_status': 'active'}
    classify = Mock(side_effect=[DynamicContentFetchError('empty'), ClassifyOutcome(good, '互动抽奖', False)])
    events = []
    with patch('src.pipeline.refresh_all_pipeline.BilibiliClient'), patch(
        'src.pipeline.refresh_all_pipeline.classify_new_link', classify
    ), patch('src.pipeline.refresh_all_pipeline.enrich_activity', return_value=activity), patch(
        'src.pipeline.refresh_all_pipeline.apply_initial_status', side_effect=lambda row: row
    ):
        result = discovery.sync_discovery(on_progress=events.append)
        assert result['state'] == 'success' and result['history_complete']
        assert result['persisted_count'] == 1 and result['pending_count'] == 1
        assert good in known_activity_ids() and bad not in known_activity_ids()
        saved = discovery._load()
        assert saved['pending_ids'] == [bad]
        assert bad not in saved['processed_ids'] and good in saved['processed_ids']
        assert len(classify.call_args_list) == 2  # No repeat within or across topics this run.
        assert any(bad in event and '待重试' in event for event in events)
        # A fresh load still finds the pending ID although the feed page is already seen.
        classify.reset_mock(side_effect=True)
        classify.return_value = ClassifyOutcome(bad, '互动抽奖', False)
        activity.to_dict.return_value = {'dynamic_id': bad, 'lottery_type': '互动抽奖', 'draw_status': 'active'}
        second = discovery.sync_discovery()
        assert second['persisted_count'] == 1 and second['pending_count'] == 0
        assert [call.args[1] for call in classify.call_args_list] == [bad]
        assert discovery._load()['pending_ids'] == []
        assert {bad, good} <= known_activity_ids()


@pytest.mark.parametrize('failure', [RuntimeError('LLM unavailable'), TaskCancelled('cancelled')])
def test_unrelated_errors_and_cancellation_still_propagate(isolated_home, failure):
    with patch('src.pipeline.refresh_all_pipeline.BilibiliClient'), patch(
        'src.pipeline.refresh_all_pipeline.classify_new_link', side_effect=failure
    ), pytest.raises(type(failure), match=str(failure)):
        run_new_links_pipeline(['https://www.bilibili.com/opus/1245655238775406594'], defer_unavailable_content=True)


def test_other_pipeline_callers_keep_strict_behavior(isolated_home):
    with patch('src.pipeline.refresh_all_pipeline.BilibiliClient'), patch(
        'src.pipeline.refresh_all_pipeline.classify_new_link', side_effect=DynamicContentFetchError('empty')
    ), pytest.raises(DynamicContentFetchError):
        run_new_links_pipeline(['https://www.bilibili.com/opus/1245655238775406594'])


def test_retry_budget_rotates_without_losing_ids(isolated_home, monkeypatch):
    ids = [str(1245655238775406500 + i) for i in range(7)]
    discovery._save({'topics': {}, 'pending_ids': ids, 'publication_times': {did: int(__import__('time').time()) for did in ids}})
    monkeypatch.setattr(discovery, 'TOPICS', ((1, '测试'),))
    monkeypatch.setattr(discovery, 'BilibiliClient', MagicMock())
    monkeypatch.setattr(discovery, '_page', lambda *args: ([], '', False))
    with patch('src.pipeline.refresh_all_pipeline.BilibiliClient'), patch(
        'src.pipeline.refresh_all_pipeline.classify_new_link', side_effect=DynamicContentFetchError('empty')
    ) as classify:
        result = discovery.sync_discovery()
        assert result['pending_count'] == 7 and classify.call_count == 5
        assert discovery._load()['pending_ids'] == ids[5:] + ids[:5]
        assert discovery._load()['processed_ids'] == []


def test_content_unavailable_during_enrichment_is_deferred(isolated_home):
    with patch('src.pipeline.refresh_all_pipeline.BilibiliClient'), patch(
        'src.pipeline.refresh_all_pipeline.classify_new_link', return_value=ClassifyOutcome('1245655238775406594', '互动抽奖', False)
    ), patch('src.pipeline.refresh_all_pipeline.enrich_activity', side_effect=DynamicContentFetchError('empty')):
        result = run_new_links_pipeline(['https://www.bilibili.com/opus/1245655238775406594'], defer_unavailable_content=True)
    assert result.ok and result.deferred_ids == ['1245655238775406594'] and result.persisted_count == 0
