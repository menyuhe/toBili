from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.activity_store import replace_all_activities
from src.participate_preflight import ensure_activity_participatable


@pytest.mark.parametrize('lottery_type', ['转发抽奖', '互动抽奖', '预约抽奖'])
def test_own_article_skipped_before_any_mutation(isolated_home, monkeypatch, lottery_type):
    from src.participate_preflight import ActivityNotParticipatable
    did = '1261710496536461321'
    replace_all_activities([{'dynamic_id': did, 'lottery_type': lottery_type,
        'activity_status': '未参加', 'draw_status': 'active', 'conditions': {}}])
    monkeypatch.setattr('src.bilibili_auth.get_login_uid', lambda: '9005111')
    monkeypatch.setattr('src.participate_preflight.fetch_dynamic_detail', lambda *args:
        {'type': 'DYNAMIC_TYPE_ARTICLE', 'modules': {'module_author': {'mid': 9005111}}})
    client = MagicMock()
    with pytest.raises(ActivityNotParticipatable, match='本人发布'):
        ensure_activity_participatable(client, did)
    client.post_form.assert_not_called()
    client.post_json.assert_not_called()


def test_follow_self_never_sends_request(monkeypatch):
    from src.lottery_actions import follow_user
    monkeypatch.setattr('src.lottery_actions.get_login_uid', lambda: 9005111)
    client = MagicMock()
    with pytest.raises(RuntimeError, match='不能关注自己'):
        follow_user(client, uid=9005111, csrf='test', referer='https://www.bilibili.com')
    client.post_form.assert_not_called()


def test_ensure_activity_participatable_rejects_joined(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = isolated_home
    dynamic_id = "1000000000000000001"
    replace_all_activities(
        [
            {
                "dynamic_id": dynamic_id,
                "lottery_type": "互动抽奖",
                "activity_status": "未参加",
                "draw_status": "active",
                "lottery_time": 9_999_999_999,
                "conditions": {},
            }
        ]
    )

    notice = {
        "participated": True,
        "lottery_time": 9_999_999_999,
        "status": 0,
    }
    client = MagicMock()
    monkeypatch.setattr(
        "src.participate_preflight.fetch_dynamic_detail",
        lambda _client, _did: {"modules": {}},
    )
    monkeypatch.setattr("src.participate_preflight.is_upower_dynamic", lambda _item: False)
    monkeypatch.setattr(
        "src.participate_preflight.fetch_notice_for_interact",
        lambda _client, _did: (notice, 0, dynamic_id),
    )
    monkeypatch.setattr("src.participate_preflight.load_participations", lambda: {})

    with pytest.raises(RuntimeError, match="已参加"):
        ensure_activity_participatable(client, dynamic_id, lottery_type_hint="互动抽奖")


@pytest.mark.parametrize('lottery_type', ['转发抽奖', '互动抽奖', '预约抽奖'])
def test_unknown_deadline_is_participatable(isolated_home, monkeypatch, lottery_type):
    from src.activity_store import load_activities
    did = '1258890854758612999'
    replace_all_activities([{'dynamic_id': did, 'lottery_type': lottery_type,
        'activity_status': '未参加', 'draw_status': 'active', 'lottery_time': None, 'conditions': {}}])
    monkeypatch.setattr('src.participate_preflight.fetch_dynamic_detail', lambda *args: {'modules': {}})
    monkeypatch.setattr('src.participate_preflight.is_upower_dynamic', lambda item: False)
    monkeypatch.setattr('src.participate_preflight.fetch_notice_for_interact', lambda *args: ({'status': 0}, 0, did))
    monkeypatch.setattr('src.participate_preflight.fetch_notice_for_reserve', lambda *args: None)
    monkeypatch.setattr('src.participate_preflight.fetch_reserve_button_status', lambda *args: False)
    monkeypatch.setattr('src.participate_preflight.load_participations', lambda: {})
    result = ensure_activity_participatable(MagicMock(), did, lottery_type_hint=lottery_type)
    assert result['activity_status'] == '未参加' and result['draw_status'] == 'active'
    assert load_activities()[0]['lottery_time'] is None


@pytest.mark.parametrize('deadline', [None, 9999999999])
def test_live_ended_status_is_preserved_without_past_deadline(isolated_home, monkeypatch, deadline):
    from src.participate_preflight import ActivityNotParticipatable
    from src.activity_store import load_activities
    did = '1258890854758612999'
    replace_all_activities([{'dynamic_id': did, 'lottery_type': '互动抽奖',
        'activity_status': '未参加', 'draw_status': 'active', 'lottery_time': deadline, 'conditions': {}}])
    monkeypatch.setattr('src.participate_preflight.fetch_dynamic_detail', lambda *args: {'modules': {}})
    monkeypatch.setattr('src.participate_preflight.is_upower_dynamic', lambda item: False)
    monkeypatch.setattr('src.participate_preflight.fetch_notice_for_interact', lambda *args: ({'status': 1}, 0, did))
    monkeypatch.setattr('src.participate_preflight.load_participations', lambda: {})
    with pytest.raises(ActivityNotParticipatable, match='已结束'):
        ensure_activity_participatable(MagicMock(), did)
    assert load_activities()[0]['draw_status'] == 'ended'
