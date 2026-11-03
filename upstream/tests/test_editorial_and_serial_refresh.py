import threading
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from web import article_routes as article


def test_article_draw_range_includes_entire_china_days_and_templates():
    from datetime import datetime
    stamps = ['2026-10-21T00:00:00+08:00', '2026-10-22T23:59:59+08:00']
    rows = {str(i): dict(dynamic_id=str(i), lottery_type='互动抽奖',
                        lottery_time=int(datetime.fromisoformat(t).timestamp())) for i, t in enumerate(stamps, 100)}
    request = article.Generate(ids=list(rows), draw_start='2026-10-21', draw_end='2026-10-22',
                               template=article.Template(title='{开奖区间} · {count}', intro='{draw_range}'))
    result = article.compose(request, rows)
    assert result['title'] == '2026-10-21 至 2026-10-22 · 2'
    assert result['body'].startswith('2026-10-21 至 2026-10-22')
    request.draw_end = '2026-10-21'
    with pytest.raises(ValueError, match='区间外'):
        article.compose(request, rows)
    request.draw_start = request.draw_end = ''
    assert article.compose(request, rows)['title'] == result['title']
    rows['100']['lottery_time'] = None
    assert '1 个时间待定' in article.compose(request, rows)['title']
    request.draw_start = '2026-10-21'
    with pytest.raises(ValueError, match='时间待定'):
        article.compose(request, rows)


def test_article_range_validation_and_legacy_draft():
    from pydantic import ValidationError
    assert article.Draft.model_validate({'title': '旧草稿'}).draw_start == ''
    for values in [dict(draw_start='2026-02-30'), dict(draw_start='2026-10-22', draw_end='2026-10-21')]:
        with pytest.raises(ValidationError):
            article.Draft(**values)


def test_article_groups_facts_keeps_links_and_unknown_time():
    rows = {str(100 + i): dict(dynamic_id=str(100 + i), lottery_type=kind,
            lottery_time=None, draw_status='active', prizes=[dict(description=f'奖品{i}', winner_count=2)],
            cookie='DO_NOT_EXPORT', conditions={'api_key': 'DO_NOT_EXPORT'})
            for i, kind in enumerate(['转发抽奖', '互动抽奖', '预约抽奖'])}
    result = article.compose(article.Generate(ids=['100', '101', '102']), rows)
    assert result['groups'] == {'转发抽奖': 1, '互动抽奖': 1, '预约抽奖': 1}
    assert result['body'].index('【互动抽奖') < result['body'].index('【转发抽奖') < result['body'].index('【预约抽奖')
    for did in rows:
        assert f'https://t.bilibili.com/{did}' in result['body']
    assert 'DO_NOT_EXPORT' not in str(result)
    assert len(result['warnings']) == 3 and '未公布' in result['body']
    assert '{count}' not in result['title'] and '3 个活动' in result['title']


def test_missing_selected_activity_does_not_silently_drop_content():
    with pytest.raises(ValueError, match='原草稿已保留'):
        article.compose(article.Generate(ids=['100']), {})


def test_article_draft_roundtrip_account_isolation_and_validation(isolated_home, monkeypatch):
    from web.app import app
    uid = ['123']
    monkeypatch.setattr(article, 'account_uid', lambda: uid[0])
    client = TestClient(app)
    draft = client.get('/api/article/draft').json()
    draft.update(ids=['100'], title='自定义标题', body='手动修订的正文', draw_start='2026-10-21', draw_end='2026-10-23')
    draft['template']['intro'] = '本期 {count} 条'
    assert client.put('/api/article/draft', json=draft).status_code == 200
    assert client.get('/api/article/draft').json() == draft
    uid[0] = '456'
    assert client.get('/api/article/draft').json()['body'] == ''
    assert client.put('/api/article/draft', json=draft).status_code == 409
    assert client.post('/api/article/generate', json={'uid':'456','ids':['100'] * 101}).status_code == 400
    uid[0] = '123'
    assert client.get('/api/article/draft').json()['body'] == '手动修订的正文'


def test_refresh_all_checks_sources_serially_in_catalog_order(isolated_home, monkeypatch):
    from web import actions
    caller = threading.get_ident()
    catalog = [{'id': sid, 'backup': False} for sid in ['DS-third', 'DS-first', 'DS-second']]
    monkeypatch.setattr('src.source_registry.handlers', lambda defaults: [(r, None, None) for r in catalog])
    visited = []
    def check(index, sid, *args):
        assert threading.get_ident() == caller  # No source worker fan-out.
        visited.append(sid)
        return index, {'source_id': sid, 'updated': False, 'link_count': 0, 'saved': False, 'status_text':'无更新'}, sid, None
    monkeypatch.setattr(actions, '_run_ds_check', check)
    result = actions.run_action('refresh_all')
    assert visited == [r['id'] for r in catalog]
    assert result['ok'] and result['result']['pipeline_skipped']


def test_refresh_all_cancel_does_not_start_next_source(isolated_home, monkeypatch):
    from web import actions
    from src.task_control import TaskCancelled
    monkeypatch.setattr('src.source_registry.handlers', lambda defaults: [({'id':str(i),'backup':False}, None, None) for i in range(3)])
    check = Mock(side_effect=TaskCancelled('cancelled'))
    monkeypatch.setattr(actions, '_run_ds_check', check)
    with pytest.raises(TaskCancelled):
        actions.run_action('refresh_all')
    assert check.call_count == 1
