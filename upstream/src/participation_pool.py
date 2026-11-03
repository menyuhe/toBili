"""Durable, account-scoped ordered participation selection."""
from __future__ import annotations

import json
import threading
from copy import deepcopy

from src.app_paths import data_dir
from src.sources.common import is_valid_dynamic_id
from src.task_control import check_cancelled, TaskCancelled
from src.participate_preflight import ActivityNotParticipatable

_lock = threading.RLock()
_active: set[str] = set()
LIMIT = 200


def _path(uid):
    uid = str(uid)
    if not uid.isdigit() or int(uid) <= 0:
        raise ValueError("请先登录账号")
    return data_dir() / f"participation_pool_{uid}.json"


def _save(uid, data):
    path = _path(uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


def _load(uid):
    path = _path(uid)
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'items': [], 'paused': False}
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise ValueError('预选池文件格式错误，请保留文件后检查')
    unfinished = [row for row in data['items'] if row['state'] not in ('success', 'skipped')]
    if len(unfinished) != len(data['items']):
        data['items'] = unfinished
        _save(uid, data)
    if str(uid) not in _active:
        interrupted = False
        for item in data['items']:
            if item['state'] == 'running':
                item.update(state='pending', message='上次执行中断，继续时会重新检查活动状态')
                interrupted = True
        if interrupted:
            data['paused'] = True
            _save(uid, data)
    return data


def snapshot(uid):
    with _lock:
        data = _load(uid)
        data['running'] = str(uid) in _active
        data['pending'] = sum(row['state'] == 'pending' for row in data['items'])
        return deepcopy(data)


def reconcile(uid, *, completed_ids=(), removed_ids=()):
    """Synchronize manual/batch participation and archiving without touching a running queue."""
    uid = str(uid)
    with _lock:
        data = _load(uid)
        if uid not in _active:
            discard = set(completed_ids) | set(removed_ids)
            remaining = [row for row in data['items'] if row['dynamic_id'] not in discard]
            if len(remaining) != len(data['items']):
                data['items'] = remaining
                _save(uid, data)
        return snapshot(uid)


def edit(uid, operation, *, ids=None, candidates=None, dynamic_id='', direction=0):
    with _lock:
        data = _load(uid)
        if str(uid) in _active:
            if operation != 'pause':
                raise ValueError('预选池正在执行，请暂停后再编辑')
            data['paused'] = True
        else:
            rows = data['items']
            if operation == 'add':
                known = {row['dynamic_id'] for row in rows}
                additions = []
                for did in ids or []:
                    if not is_valid_dynamic_id(did):
                        raise ValueError('活动 ID 无效')
                    if did in known:
                        continue
                    item = (candidates or {}).get(did)
                    if not item or not item.get('can_participate'):
                        raise ValueError('包含已结束、已参加或不可参与的活动，请刷新列表')
                    additions.append({'dynamic_id': did, 'title': item['activity_title'],
                        'lottery_type': item['lottery_type'], 'state': 'pending', 'message': ''})
                    known.add(did)
                if len(rows) + len(additions) > LIMIT:
                    raise ValueError(f'预选池最多保留 {LIMIT} 条，请先清理完成项')
                rows.extend(additions)
            elif operation == 'remove':
                data['items'] = [row for row in rows if row['dynamic_id'] != dynamic_id]
            elif operation == 'move':
                if direction not in (-1, 1):
                    raise ValueError('移动方向无效')
                index = next((i for i, row in enumerate(rows) if row['dynamic_id'] == dynamic_id), -1)
                target = index + direction
                if index >= 0 and 0 <= target < len(rows):
                    rows[index], rows[target] = rows[target], rows[index]
            elif operation == 'retry':
                for row in rows:
                    if row['state'] == 'failed':
                        row.update(state='pending', message='')
            elif operation == 'clear_done':
                data['items'] = [row for row in rows if row['state'] not in ('success', 'skipped')]
            elif operation == 'pause':
                data['paused'] = True
            else:
                raise ValueError('不支持的预选操作')
        _save(uid, data)
        return snapshot(uid)


def run(uid, *, participate, eligible, progress):
    """One activity at a time; pause at boundaries, persist each outcome."""
    uid = str(uid)
    with _lock:
        if uid in _active:
            raise ValueError('预选池已在执行')
        data = _load(uid)
        targets = [deepcopy(row) for row in data['items'] if row['state'] == 'pending']
        if not targets:
            raise ValueError('预选池没有待参与活动')
        data['paused'] = False
        data['last_run'] = {'joined': 0, 'skipped': 0, 'failed': 0, 'total': len(targets)}
        _save(uid, data)
        _active.add(uid)
    done = joined = failed = skipped = 0
    failure_message = ''
    current = None

    def update(did, state, message):
        with _lock:
            saved = _load(uid)
            for row in saved['items']:
                if row['dynamic_id'] == did:
                    row.update(state=state, message=message)
                    break
            if state in ('success', 'skipped'):
                saved['items'] = [row for row in saved['items'] if row['dynamic_id'] != did]
            if state in ('success', 'skipped', 'failed'):
                key = {'success': 'joined', 'skipped': 'skipped', 'failed': 'failed'}[state]
                saved['last_run'][key] += 1
            _save(uid, saved)

    try:
        for index, target in enumerate(targets):
            check_cancelled()
            with _lock:
                if _load(uid).get('paused'):
                    break
            did, title = target['dynamic_id'], target['title']
            current = did
            update(did, 'running', '正在检查活动状态')
            progress(step=done, total=len(targets), message=f'{index + 1}/{len(targets)} · {title}',
                     log_append=f'开始预选 {index + 1}/{len(targets)}：{title}（{did}）')
            try:
                reason = eligible(did)
                if reason:
                    update(did, 'skipped', reason)
                    skipped += 1
                    progress(step=done, total=len(targets), message=reason, log_append=f'{title}：{reason}')
                else:
                    def report(**event):
                        progress(step=done, total=len(targets), message=f'{index + 1}/{len(targets)} · {title} · {event.get("message", "")}',
                                 log_append=event.get('log_append'))
                    payload = participate(did, report)
                    if not payload.get('ok'):
                        raise RuntimeError(payload.get('message') or '参与失败')
                    update(did, 'success', '参与成功')
                    from src.cloud_guard import confirm_cloud_writes
                    confirm_cloud_writes()
                    joined += 1
                    progress(step=done, total=len(targets), message=f'{title}：参与成功',
                             log_append=f'{title}：参与成功\n{payload.get("log", "")}')
            except ActivityNotParticipatable as exc:
                check_cancelled()
                from web.user_messages import sanitize_log
                reason = sanitize_log(str(exc))
                update(did, 'skipped', reason)
                skipped += 1
                progress(step=done, total=len(targets), message='该活动不可参与，继续下一条',
                         log_append=f'{title}：已跳过，{reason}')
            except TaskCancelled:
                raise
            except Exception as exc:
                check_cancelled()
                from web.user_messages import sanitize_log
                message = sanitize_log(str(exc))
                failure_message = f'{title}：{message}'
                update(did, 'failed', message)
                failed += 1
                progress(step=done, total=len(targets), message='参与失败，预选池已暂停',
                         log_append=f'{title}：失败，{message}；剩余活动已保留')
                with _lock:
                    saved = _load(uid)
                    saved['paused'] = True
                    _save(uid, saved)
                current = None
                break
            done += 1
            current = None
            progress(step=done, total=len(targets), message=f'已处理 {done}/{len(targets)} 条预选活动')
    finally:
        with _lock:
            if current:
                update(current, 'pending', '执行中断，继续时会重新检查活动状态')
                saved = _load(uid)
                saved['paused'] = True
                _save(uid, saved)
            _active.discard(uid)
    final = snapshot(uid)
    message = f'预选池：成功 {joined} 条，跳过 {skipped} 条，失败 {failed} 条，待参与 {final["pending"]} 条。'
    if failure_message:
        message = f'预选池已暂停，{failure_message}。待参与 {final["pending"]} 条。'
    return {'ok': not failed, 'message': message, 'log': message,
            'result': {'joined': joined, 'failed': failed, 'skipped_count': skipped, 'pending': final['pending'], 'paused': final['paused']}}
