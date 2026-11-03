from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src import participation_pool as pool
from src.activity_store import load_activities
from src.participation_store import load_participations
from src.lottery_classifier import PARTICIPATABLE_TYPES
from web.account_service import get_account_profile
from web.activity_service import _normalize_activity
from web.api_errors import require_login

router = APIRouter(prefix='/api/participation-pool')


def account_uid():
    account = get_account_profile()
    require_login(account)
    uid = str(account.get('mid') or '')
    if not uid.isdigit():
        raise HTTPException(401, '请刷新账号信息后再试')
    return uid


def candidates():
    from src.activity_archive import archived_activity_ids
    archived = archived_activity_ids()
    participations = load_participations()
    return {str(row['dynamic_id']): _normalize_activity(row, {}, participations.get(str(row['dynamic_id'])))
            for row in load_activities() if str(row['dynamic_id']) not in archived and row.get('lottery_type') in PARTICIPATABLE_TYPES}


class PoolEdit(BaseModel):
    operation: Literal['add', 'remove', 'move', 'retry', 'clear_done', 'pause']
    ids: list[str] = Field(default_factory=list, max_length=200)
    dynamic_id: str = ''
    direction: int = 0


@router.get('')
def get_pool():
    from sqlmodel import select
    from src.db.models import ParticipationRow
    from src.db.session import session_scope
    from src.activity_archive import archived_activity_ids
    uid = account_uid()
    with session_scope() as session:
        completed = set(session.exec(select(ParticipationRow.dynamic_id).where(
            ParticipationRow.uid == uid, ParticipationRow.user_status == '已参加')).all())
    return pool.reconcile(uid, completed_ids=completed, removed_ids=archived_activity_ids())


@router.post('')
def edit_pool(body: PoolEdit):
    from web.job_runner import runner
    uid = account_uid()
    job = runner.get_status()
    if job.state == 'running' and job.action == 'participate_pool' and body.operation != 'pause':
        raise HTTPException(409, '预选池正在执行，请暂停后再编辑')
    try:
        return pool.edit(uid, body.operation, ids=body.ids, dynamic_id=body.dynamic_id,
                         direction=body.direction, candidates=candidates() if body.operation == 'add' else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
