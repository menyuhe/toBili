"""Sync only immutable repost IDs; never replicate task or cleanup permissions."""
import hashlib
from sqlmodel import select
from src.catalog_protocol import RepostReference
from src.db.models import ParticipationLinkRow, SyncedRepostRow
from src.db.session import session_scope
from src.accounts import read_state, write_state


def synchronize(request, namespace):
    key = 'repost_sync:' + hashlib.sha256(namespace.encode()).hexdigest()
    state = read_state('__global__', key) or {}
    sent = set(state.get('sent', []))
    local = {}
    with session_scope() as session:
        rows = session.exec(select(ParticipationLinkRow)).all()
        for row in rows:
            if not row.repost_recorded or row.repost_id == row.dynamic_id:
                continue
            try:
                value = RepostReference(uid=row.uid, dynamic_id=row.dynamic_id, repost_id=row.repost_id)
            except ValueError:
                continue
            token = ':'.join((value.uid, value.dynamic_id, value.repost_id))
            local[token] = value.model_dump()
    uploaded = downloaded = 0
    pending = sorted(set(local) - sent)
    for start in range(0, len(pending), 100):
        batch = pending[start:start+100]
        response = request('POST', '/reposts', {'items': [local[k] for k in batch]})
        uploaded += int(response['inserted'])
        sent.update(batch)
        state['sent'] = sorted(sent)
        write_state('__global__', key, state)
    cursor = int(state.get('cursor', 0))
    while True:
        response = request('GET', '/reposts?after=' + str(cursor))
        values = [RepostReference.model_validate(value) for value in response['items']]
        next_cursor = int(response['cursor'])
        if next_cursor < cursor or (values and next_cursor <= cursor) or (response.get('more') and not values):
            raise ValueError('转发关联分页游标无效')
        with session_scope() as session:
            for value in values:
                if value.dynamic_id == value.repost_id:
                    raise ValueError('本人转发不能与原动态 ID 相同')
                pk = (value.uid, value.dynamic_id, value.repost_id)
                if session.get(SyncedRepostRow, pk) is None:
                    session.add(SyncedRepostRow(**value.model_dump()))
                    session.flush()
                    downloaded += 1
        # Advance only after a complete validated page commits.
        cursor = next_cursor
        state['cursor'] = cursor
        write_state('__global__', key, state)
        if not response.get('more'):
            break
    return {'reposts_uploaded': uploaded, 'reposts_downloaded': downloaded}


def references(uid, dynamic_id):
    with session_scope() as session:
        return [row.repost_id for row in session.exec(select(SyncedRepostRow).where(
            SyncedRepostRow.uid == str(uid), SyncedRepostRow.dynamic_id == str(dynamic_id))).all()]


def verified_repost(client, uid, dynamic_id):
    from src.cleanup_api import CleanupApi, own_repost
    from src.task_control import TaskCancelled
    ids = references(uid, dynamic_id)
    if not ids:
        return False
    api = CleanupApi(client, str(uid))
    for repost_id in ids:
        try:
            item = api.dynamic(repost_id)
            if item and own_repost(item, str(uid), str(dynamic_id)):
                return True
        except TaskCancelled:
            raise
        except Exception as exc:
            # Unknown history must not cause a duplicate post.
            raise RuntimeError('跨设备转发记录暂时无法核验，请稍后重试') from exc
    return False
