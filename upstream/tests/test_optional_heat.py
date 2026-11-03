from unittest.mock import patch

import pytest

from src.activity_store import load_activities
from src.fetch_activity_info import is_enriched_complete, list_missing_enrich_tasks
from src.lottery_api import extract_activity_heat
from src.pipeline.classify_step import ClassifyOutcome
from src.pipeline.refresh_all_pipeline import run_new_links_pipeline
from web.activity_service import list_activities


@pytest.mark.parametrize('modules', [{}, {'module_stat': {}},
    {'module_stat': {'forward': {'count': None}}},
    [{'module_stat': {'forward': {'count': 'invalid'}}}]])
def test_missing_heat_is_not_confirmed_zero(modules):
    assert extract_activity_heat({'modules': modules}, lottery_type='互动抽奖') == (None, False)


def test_explicit_zero_and_reserve_heat_remain_valid():
    assert extract_activity_heat({'modules': {'module_stat': {'forward': {'count': 0}}}},
                                 lottery_type='互动抽奖') == (0, False)
    assert extract_activity_heat({'modules': {'module_dynamic': {'additional': {'reserve': {'reserve_total': 0}}}}},
                                 lottery_type='预约抽奖') == (0, True)


def test_missing_heat_does_not_abort_persistence_or_repeat_enrichment(isolated_home):
    missing, available = '1070085866290937896', '1070085866290937897'
    notice = {'lottery_id': 1, 'first_prize': 1, 'first_prize_cmt': '奖品', 'lottery_time': 9999999999}
    outcomes = [ClassifyOutcome(did, '互动抽奖', False, lottery_notice=notice,
        notice_business_type=1, notice_business_id=did) for did in (missing, available)]
    detail_calls = []

    def fetch_detail(client, did):
        detail_calls.append(did)
        return None if did == missing else {'modules': {'module_stat': {'forward': {'count': 42}}}}

    events = []
    with patch('src.pipeline.refresh_all_pipeline.BilibiliClient'), patch(
        'src.pipeline.refresh_all_pipeline.classify_new_link', side_effect=outcomes
    ), patch('src.pipeline.enrich_fetch_context.fetch_dynamic_detail', side_effect=fetch_detail):
        result = run_new_links_pipeline([f'https://www.bilibili.com/opus/{did}' for did in (missing, available)],
                                       on_progress=lambda *args: events.append(args[-1]))
    assert result.ok and result.persisted_count == 2
    assert detail_calls.count(missing) == 1
    stored = {row['dynamic_id']: row for row in load_activities()}
    assert is_enriched_complete(stored[missing])
    assert not stored[missing]['repost_fetched'] and not stored[missing]['repost_zero_confirmed']
    assert stored[available]['repost_fetched'] and stored[available]['repost_count'] == 42
    assert list_missing_enrich_tasks() == []
    assert any(missing in event and '热度暂缺' in event for event in events)
    for direction in ('asc', 'desc'):
        items = list_activities(sort='heat', order=direction)['items']
        assert [item['dynamic_id'] for item in items] == [available, missing]
        assert items[-1]['heat_missing']
