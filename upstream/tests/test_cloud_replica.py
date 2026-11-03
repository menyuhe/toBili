import json
import uuid

import pytest
from sqlmodel import select

from src import cloud_replica as replica
from src.cloud_protocol import Change, canonical
from src.db.models import ActivityRow, DrawOutcomeRow, ParticipationRow, ProtectedUpRow
from src.db.session import session_scope

UID = "123456"
DID = "1234567890123456789"


class MemoryRelay:
    def __init__(self):
        self.records, self.receipts, self.revision = {}, {}, 0
        self.lose_response = False

    def request(self, method, path, body=None):
        if method == "POST":
            result = []
            for raw in body["changes"]:
                change = Change.model_validate(raw)
                pair = (change.kind, change.key)
                record = self.records.get(pair)
                if change.change_id in self.receipts:
                    response = self.receipts[change.change_id]
                elif (record["version"] if record else 0) != change.base_version:
                    response = {"change_id": change.change_id, "conflict": record}
                else:
                    self.revision += 1
                    record = {"kind": change.kind, "key": change.key, "version": self.revision,
                              "deleted": change.deleted, "value": change.value}
                    self.records[pair] = record
                    response = {"change_id": change.change_id, "record": record}
                    self.receipts[change.change_id] = response
                result.append(response)
            if self.lose_response:
                self.lose_response = False
                raise TimeoutError("simulated lost response")
            return {"results": result, "revision": self.revision}
        after = int(path.split("after=")[1].split("&")[0])
        rows = sorted((r for r in self.records.values() if r["version"] > after), key=lambda r: r["version"])
        return {"items": rows, "cursor": self.revision, "revision": self.revision, "has_more": False}

    def edit(self, kind, key, value, deleted=False):
        old = self.records.get((kind, key))
        return self.request("POST", "/sync/push", {"changes": [{"kind": kind, "key": key, "value": value,
            "base_version": old["version"] if old else 0, "change_id": uuid.uuid4().hex, "deleted": deleted}]})


def seed():
    with session_scope() as session:
        session.add(ActivityRow(dynamic_id=DID, lottery_type="互动抽奖", payload_json=json.dumps({
            "title": "测试奖品", "sender_uid": "900", "cookie": "SECRET_COOKIE", "conditions": {"api_key": "SECRET_KEY"}})))
        session.add(ParticipationRow(uid=UID, dynamic_id=DID, user_status="已参加", updated_at=10))
        session.add(ParticipationRow(uid="999", dynamic_id=DID, user_status="未参加", updated_at=20))
        session.add(DrawOutcomeRow(uid=UID, dynamic_id=DID, keep=False, outcome="unknown"))


def test_snapshot_never_exports_cookies_keys_or_other_uid(isolated_home):
    seed()
    exported = replica.snapshot(UID)
    serialized = canonical(list(exported.values()))
    assert "SECRET_COOKIE" not in serialized and "SECRET_KEY" not in serialized
    assert exported[("participation", DID)]["value"]["user_status"] == "已参加"
    assert "未参加" not in serialized


def test_outbox_survives_lost_response_and_replay(isolated_home):
    seed()
    ledger = replica.Ledger(isolated_home / "ledger.db", "owner-uid")
    relay = MemoryRelay()
    relay.lose_response = True
    with pytest.raises(TimeoutError):
        replica.synchronize(UID, ledger, relay.request)
    assert ledger.pending()
    revision = relay.revision
    recovered = replica.Ledger(isolated_home / "ledger.db", "owner-uid")
    result = replica.synchronize(UID, recovered, relay.request)
    assert relay.revision == revision and not recovered.pending() and not result["conflicts"]


def test_remote_keep_applies_without_overwriting_rich_local_details(isolated_home):
    seed()
    ledger, relay = replica.Ledger(isolated_home / "ledger.db", "owner"), MemoryRelay()
    replica.synchronize(UID, ledger, relay.request)
    relay.edit("keep", DID, {"keep": True})
    result = replica.synchronize(UID, ledger, relay.request)
    assert result["conflicts"] == 0
    with session_scope() as session:
        assert session.get(DrawOutcomeRow, (UID, DID)).keep
        assert "SECRET_COOKIE" in session.get(ActivityRow, DID).payload_json
        assert session.get(ParticipationRow, ("999", DID)).user_status == "未参加"


def test_conflicts_preserve_local_and_remote_and_tombstones(isolated_home):
    seed()
    with session_scope() as session:
        session.add(ProtectedUpRow(uid=UID, up_uid="900"))
    ledger, relay = replica.Ledger(isolated_home / "ledger.db", "owner"), MemoryRelay()
    replica.synchronize(UID, ledger, relay.request)
    relay.edit("outcome", DID, {"outcome": "won", "source": "official", "checked_at": 30})
    with session_scope() as session:
        session.get(DrawOutcomeRow, (UID, DID)).outcome = "lost"
        session.delete(session.get(ProtectedUpRow, (UID, "900")))
    result = replica.synchronize(UID, ledger, relay.request)
    assert result["conflicts"] == 1
    conflict = ledger.conflicts()[0]
    assert conflict["local"]["value"]["outcome"] == "lost" and conflict["remote"]["value"]["outcome"] == "won"
    assert relay.records[("protected_up", "900")]["deleted"]
    assert ledger.cursor() == relay.revision


def test_new_device_hydrates_summary_and_account_namespace_is_separate(isolated_home):
    ledger, relay = replica.Ledger(isolated_home / "ledger.db", "owner1"), MemoryRelay()
    relay.edit("activity", DID, {"dynamic_id": DID, "title": "云端奖品", "lottery_type": "互动抽奖"})
    relay.edit("participation", DID, {"user_status": "已参加", "updated_at": 30})
    replica.synchronize(UID, ledger, relay.request)
    with session_scope() as session:
        item = json.loads(session.get(ActivityRow, DID).payload_json)
        assert item["cloud_summary_only"] and item["activity_title"] == "云端奖品"
        assert session.get(ParticipationRow, (UID, DID)).user_status == "已参加"
    other = replica.Ledger(isolated_home / "ledger.db", "owner2")
    assert not other.shadows() and not other.cursor() and not other.conflicts()


def test_selection_and_participation_settings_round_trip(isolated_home):
    from src.db.models import UserSettingsRow
    from src import participation_pool as pool
    seed()
    with session_scope() as session:
        session.add(UserSettingsRow(uid=UID, participate_text="好运！", participate_text_mode="custom"))
    pool._save(UID, {"items": [{"dynamic_id": DID, "state": "pending", "title": "奖品", "lottery_type": "互动抽奖", "message": ""}], "paused": True})
    exported = replica.snapshot(UID)
    assert exported[("selection", "main")]["value"] == {"ids": [DID]}
    assert exported[("participation_settings", "main")]["value"]["participate_text"] == "好运！"
    replica.apply_record(UID, {"kind": "selection", "key": "main", "deleted": False, "value": {"ids": []}})
    assert pool.snapshot(UID)["items"] == []


def save_pool(ids):
    from src import participation_pool as pool
    pool._save(UID, {'items': [{'dynamic_id': did, 'state': 'pending', 'title': '奖品',
                             'lottery_type': '互动抽奖', 'message': ''} for did in ids], 'paused': True})


def pool_ids():
    return replica.snapshot(UID)[('selection', 'main')]['value']['ids']


def test_selection_merges_independent_additions_and_respects_removals(isolated_home):
    ledger, relay = replica.Ledger(isolated_home / 'ledger.db', 'owner'), MemoryRelay()
    save_pool(['100', '200'])
    replica.synchronize(UID, ledger, relay.request)
    save_pool(['100', '300'])
    relay.edit('selection', 'main', {'ids': ['100', '200', '400']})
    result = replica.synchronize(UID, ledger, relay.request)
    assert pool_ids() == ['100', '300', '400']
    assert relay.records[('selection', 'main')]['value']['ids'] == pool_ids()
    assert result['conflicts'] == result['pending'] == 0


def test_acknowledged_echo_does_not_erase_edits_during_pull(isolated_home):
    ledger, relay = replica.Ledger(isolated_home / 'ledger.db', 'owner'), MemoryRelay()
    save_pool(['100'])
    edited = False
    def request(method, path, body=None):
        nonlocal edited
        response = relay.request(method, path, body)
        if method == 'GET' and not edited:
            save_pool(['100', '200'])
            edited = True
        return response
    replica.synchronize(UID, ledger, request)
    assert pool_ids() == ['100', '200']
    assert relay.records[('selection', 'main')]['value']['ids'] == pool_ids()


@pytest.mark.parametrize('choice', ['local', 'remote'])
def test_resolve_legacy_selection_uses_current_pool_not_old_conflict(isolated_home, monkeypatch, choice):
    from src.cloud_client import CloudClient, save_connection
    cfg = {'server': 'https://relay.test', 'user_id': 'owner', 'bili_uid': UID}
    save_connection(cfg)
    client, relay = CloudClient(), MemoryRelay()
    ledger = client.ledger(cfg, UID)
    save_pool(['100'])
    replica.synchronize(UID, ledger, relay.request)
    remote = relay.records[('selection', 'main')]
    ledger.conflict(('selection', 'main'), {'deleted': False, 'value': {'ids': ['100', '200']}}, remote)
    save_pool(['100', '200', '300', '400'])
    monkeypatch.setattr(client, 'assert_idle', lambda: None)
    monkeypatch.setattr('src.bilibili_auth.get_login_uid', lambda: UID)
    monkeypatch.setattr(client, 'request', lambda cfg, method, path, body=None: relay.request(method, path, body))
    with pytest.raises(ValueError, match='不再同步账号状态'):
        client.resolve('selection', 'main', choice)
    assert pool_ids() == ['100', '200', '300', '400']
    assert client.conflicts() == []


def test_selection_merge_overflow_preserves_both_versions(isolated_home):
    ledger, relay = replica.Ledger(isolated_home / 'ledger.db', 'owner'), MemoryRelay()
    save_pool([str(i) for i in range(1, 151)])
    relay.edit('selection', 'main', {'ids': [str(i) for i in range(151, 251)]})
    with pytest.raises(ValueError, match='200'):
        replica.synchronize(UID, ledger, relay.request)
    assert len(pool_ids()) == 150 and ledger.pending()
    assert len(relay.records[('selection', 'main')]['value']['ids']) == 100


def test_remote_reorder_applies_without_local_membership_edits(isolated_home):
    ledger, relay = replica.Ledger(isolated_home / 'ledger.db', 'owner'), MemoryRelay()
    save_pool(['100', '200'])
    replica.synchronize(UID, ledger, relay.request)
    relay.edit('selection', 'main', {'ids': ['200', '100']})
    replica.synchronize(UID, ledger, relay.request)
    assert pool_ids() == ['200', '100']


def test_selection_echo_preserves_running_state_and_results(isolated_home):
    from src import participation_pool as pool
    ledger, relay = replica.Ledger(isolated_home / 'ledger.db', 'owner'), MemoryRelay()
    save_pool(['100'])
    pool._active.add(UID)
    try:
        data = pool._load(UID)
        data['items'][0].update(state='running', message='正在执行')
        data['paused'] = False
        data['last_run'] = {'joined': 2}
        pool._save(UID, data)
        replica.synchronize(UID, ledger, relay.request)
        assert pool._load(UID) == data
    finally:
        pool._active.discard(UID)
