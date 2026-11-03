from threading import Event

import pytest

from src import participation_pool as pool
from src.task_control import TaskCancelled, task_cancellation

IDS = ['1111111111111111111', '2222222222222222222', '3333333333333333333']


@pytest.fixture
def selected(isolated_home):
    candidates = {did: {'activity_title': f'奖品{i}', 'lottery_type': '互动抽奖', 'can_participate': True}
                  for i, did in enumerate(IDS)}
    pool.edit('123', 'add', ids=IDS, candidates=candidates)
    return candidates


def execute(participate, **kwargs):
    return pool.run('123', participate=participate, eligible=kwargs.get('eligible', lambda did: None),
                    progress=kwargs.get('progress', lambda **event: None))


def test_order_dedup_remove_and_account_isolation(selected):
    pool.edit('123', 'add', ids=[IDS[0]], candidates=selected)
    pool.edit('123', 'move', dynamic_id=IDS[2], direction=-1)
    assert [row['dynamic_id'] for row in pool.snapshot('123')['items']] == [IDS[0], IDS[2], IDS[1]]
    pool.edit('123', 'remove', dynamic_id=IDS[2])
    assert len(pool.snapshot('123')['items']) == 2
    assert pool.snapshot('456')['items'] == []
    assert pool._path('123').exists()


def test_invalid_add_is_atomic(selected):
    with pytest.raises(ValueError):
        pool.edit('123', 'add', ids=['9999999999999999999'], candidates={})
    assert len(pool.snapshot('123')['items']) == 3
    with pytest.raises(ValueError):
        pool.snapshot('../123')


def test_serial_order_and_pause_after_current_then_resume(selected):
    calls = []
    def participate(did, report):
        calls.append(did)
        if len(calls) == 1:
            pool.edit('123', 'pause')
            with pytest.raises(ValueError, match='暂停后'):
                pool.edit('123', 'remove', dynamic_id=IDS[1])
        return {'ok': True, 'log': '点赞成功\n转发成功'}
    first = execute(participate)
    assert calls == [IDS[0]]
    assert first['result']['pending'] == 2 and first['result']['paused']
    assert not pool.snapshot('123')['running']
    execute(participate)
    assert calls == IDS
    assert pool.snapshot('123')['items'] == []
    assert pool.snapshot('123')['last_run']['joined'] == 2
    pool.edit('123', 'clear_done')
    assert pool.snapshot('123')['items'] == []


def test_failed_item_stops_pool_and_can_requeue(selected):
    calls = []
    def participate(did, report):
        calls.append(did)
        raise RuntimeError('网络失败')
    result = execute(participate)
    assert calls == IDS[:1] and not result['ok']
    assert '网络失败' in result['message'] and '待参与 2 条' in result['message']
    assert [row['state'] for row in pool.snapshot('123')['items']] == ['failed', 'pending', 'pending']
    pool.edit('123', 'retry')
    calls.clear()
    execute(lambda did, report: calls.append(did) or {'ok': True})
    assert calls == IDS


def test_only_durable_success_confirms_cloud_writes_before_next_login_check(selected):
    from src.cloud_guard import guarded_writes, check_cloud_write
    confirmed = []
    def confirm():
        assert IDS[0] not in [r['dynamic_id'] for r in pool.snapshot('123')['items']]
        confirmed.append(IDS[0])
    def eligible(did):
        if did == IDS[1]:
            raise RuntimeError('登录状态暂时无法核验')
    def participate(did, report):
        check_cloud_write()
        return {'ok': True}
    with guarded_writes(lambda: None, confirm):
        result = execute(participate, eligible=eligible)
    assert confirmed == [IDS[0]]
    assert result['result']['joined'] == 1 and result['result']['failed'] == 1
    assert [r['dynamic_id'] for r in pool.snapshot('123')['items']] == IDS[1:]


def test_skip_ended_and_log_saved_steps(selected):
    calls, logs = [], []
    def participate(did, report):
        calls.append(did)
        report(step=1, total=6, message='点赞', log_append='点赞完成')
        return {'ok': True, 'log': '参与详情'}
    result = execute(participate, eligible=lambda did: '已结束，已跳过' if did == IDS[1] else None,
                     progress=lambda **event: logs.append(event))
    assert calls == [IDS[0], IDS[2]] and result['result']['skipped_count'] == 1
    assert any(event.get('log_append') == '点赞完成' for event in logs)
    assert pool.snapshot('123')['items'] == []
    assert pool.snapshot('123')['last_run']['skipped'] == 1


def test_cancel_keeps_current_and_remaining_pending(selected):
    cancel = Event()
    def participate(did, report):
        cancel.set()
        raise ValueError('任务已取消')
    with task_cancellation(cancel), pytest.raises(TaskCancelled):
        execute(participate)
    saved = pool.snapshot('123')
    assert saved['paused'] and not saved['running'] and saved['pending'] == 3
    assert '中断' in saved['items'][0]['message']


def test_restart_recovers_running_without_replaying_completed(selected):
    saved = pool.snapshot('123')
    saved['items'][0]['state'] = 'success'
    saved['items'][1]['state'] = 'running'
    pool._save('123', saved)
    recovered = pool.snapshot('123')
    assert [row['state'] for row in recovered['items']] == ['pending', 'pending']
    assert recovered['paused']


def test_action_uses_existing_single_participation(selected, monkeypatch):
    from web import actions
    from web import pool_routes
    calls = []
    monkeypatch.setattr(pool_routes, 'account_uid', lambda: '123')
    monkeypatch.setattr(pool_routes, 'candidates', lambda: selected)
    original = actions._run_action
    def dispatch(action, params=None, **kwargs):
        if action == 'participate':
            calls.append(params['dynamic_id'])
            return {'ok': True}
        return original(action, params, **kwargs)
    monkeypatch.setattr(actions, '_run_action', dispatch)
    result = actions.run_action('participate_pool', {'uid': '123'})
    assert result['ok'] and calls == IDS


def test_account_change_pauses_before_any_participation(selected, monkeypatch):
    from web.actions import run_action
    from web import pool_routes
    monkeypatch.setattr(pool_routes, 'account_uid', lambda: '456')
    result = run_action('participate_pool', {'uid': '123'})
    assert not result['ok'] and result['result']['joined'] == 0
    saved = pool.snapshot('123')
    assert saved['paused'] and saved['pending'] == 2
    assert '账号已改变' in saved['items'][0]['message']


def test_definitive_ineligibility_skips_then_continues(selected):
    from src.participate_preflight import ActivityNotParticipatable
    calls = []
    def participate(did, report):
        calls.append(did)
        if did == IDS[0]:
            raise ActivityNotParticipatable('活动已结束')
        return {'ok': True}
    result = execute(participate)
    assert calls == IDS
    assert result['ok'] and result['result']['skipped_count'] == 1
    assert result['result']['joined'] == 2 and not result['result']['paused']
    assert pool.snapshot('123')['items'] == []


def test_pool_runs_real_preflight_with_unknown_deadlines(isolated_home, monkeypatch):
    from unittest.mock import MagicMock
    from src.activity_store import replace_all_activities
    from web import actions, pool_routes
    replace_all_activities([{'dynamic_id': did, 'lottery_type': '互动抽奖' if i == 1 else '转发抽奖',
        'activity_status': '未参加', 'draw_status': 'active', 'lottery_time': None, 'conditions': {}}
        for i, did in enumerate(IDS)])
    monkeypatch.setattr(pool_routes, 'account_uid', lambda: '123')
    pool.edit('123', 'add', ids=IDS, candidates=pool_routes.candidates())
    monkeypatch.setattr(actions, 'BilibiliClient', MagicMock())
    monkeypatch.setattr('src.participate_preflight.fetch_dynamic_detail', lambda *args: {'modules': {}})
    monkeypatch.setattr('src.participate_preflight.is_upower_dynamic', lambda item: False)
    monkeypatch.setattr('src.participate_preflight.fetch_notice_for_interact', lambda *args: ({'status': 1}, 0, IDS[1]))
    monkeypatch.setattr('src.participate_preflight.load_participations', lambda: {})
    calls = []
    def simulated(did, on_step, **kwargs):
        calls.append(did)
        on_step(1, 1, '模拟参与完成', 'repost')
        return {'ok': True, 'message': '参与成功'}
    monkeypatch.setattr(actions, '_participate_dynamic_payload', simulated)
    result = actions.run_action('participate_pool', {'uid': '123'})
    assert result['ok'] and result['result']['joined'] == 2 and result['result']['skipped_count'] == 1
    assert calls == [IDS[0], IDS[2]]
    assert pool.snapshot('123')['items'] == []


def test_success_exits_immediately_and_failure_stays(selected):
    seen = []
    def participate(did, report):
        if did == IDS[1]:
            seen.extend(row['dynamic_id'] for row in pool.snapshot('123')['items'])
            raise RuntimeError('网络失败')
        return {'ok': True}
    result = execute(participate)
    assert seen == IDS[1:]
    assert result['result']['joined'] == 1
    assert [r['state'] for r in pool.snapshot('123')['items']] == ['failed', 'pending']
    assert pool.snapshot('123')['last_run']['joined'] == 1


def test_reconcile_keeps_remaining_order_and_does_not_modify_running_target(selected):
    pool.reconcile('123', completed_ids={IDS[0]}, removed_ids={IDS[2]})
    assert [r['dynamic_id'] for r in pool.snapshot('123')['items']] == [IDS[1]]
    def participate(did, report):
        pool.reconcile('123', completed_ids={did})
        assert pool.snapshot('123')['items'][0]['state'] == 'running'
        return {'ok': True}
    execute(participate)
    assert pool.snapshot('123')['items'] == []


def test_route_syncs_current_account_participation_only(selected, monkeypatch):
    from src.db.models import ParticipationRow
    from src.db.session import session_scope
    from web import pool_routes
    with session_scope() as session:
        session.add(ParticipationRow(uid='123', dynamic_id=IDS[0], user_status='已参加', updated_at=1))
        session.add(ParticipationRow(uid='456', dynamic_id=IDS[1], user_status='已参加', updated_at=1))
    monkeypatch.setattr(pool_routes, 'account_uid', lambda: '123')
    assert [r['dynamic_id'] for r in pool_routes.get_pool()['items']] == IDS[1:]


def test_single_and_triple_completion_hook_removes_only_successful_id(selected, monkeypatch):
    from web.actions import _sync_completed_pool_item
    monkeypatch.setattr('src.db.uids.participation_uid', lambda: '123')
    _sync_completed_pool_item(IDS[1])
    assert [r['dynamic_id'] for r in pool.snapshot('123')['items']] == [IDS[0], IDS[2]]
