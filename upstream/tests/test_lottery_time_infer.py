from __future__ import annotations

from pathlib import Path

from src.lottery_time import SIX_MONTHS_SECONDS, resolve_effective_lottery_time_unix
from src.participation_store import ParticipationRecord


def test_resolve_effective_lottery_time_uses_participation(tmp_path: Path) -> None:
    participate_at = 1_600_000_000
    item = {"dynamic_id": "1", "enriched_at": participate_at, "conditions": {}}
    participation = ParticipationRecord(
        dynamic_id="1",
        user_status="已参加",
        updated_at=participate_at,
    )
    inferred = resolve_effective_lottery_time_unix(item, participation, persist=True)
    assert inferred is None
    assert "lottery_time_inferred" not in item


def test_legacy_inferred_date_is_not_a_real_deadline():
    from src.lottery_time import lottery_time_text, lottery_time_unix, migrate_lottery_time_fields, is_activity_past_end
    item = {'lottery_time': 1600000000, 'conditions': {'lottery_time_inferred': True,
            'lottery_time_text': '2020-09-13 20:26'}}
    assert lottery_time_unix(item) is None
    assert lottery_time_text(item) == ''
    assert not is_activity_past_end(item)
    assert migrate_lottery_time_fields(item)
    assert item['lottery_time'] is None
    assert item['conditions'].get('lottery_time_text') == ''
