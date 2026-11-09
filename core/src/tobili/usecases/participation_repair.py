"""Correct old classification locally and reconcile missing follows without reposting."""
import json
from datetime import timedelta

from ..domain.action import Action, ActionKind
from ..domain.campaign import CampaignKind
from ..domain.extract import kind_from
from ..domain.probe import ProbeState
from ..domain.errors import Fault, RemoteError


def recheck_catalog(box, owner, *, limit=3, cancelled=lambda:False):
    """Rotate read-only incomplete catalogue checks without consuming new slots."""
    from .pool import history_pending
    from ..adapters.sqlite.repos import _EXPIRES, stamp
    from ..domain.enrollment import Stage
    from ..domain.errors import MissingCredential, WriteUnavailable
    from ..runtime.redact import safe_exception
    rows = box.db.connect().execute(f"""SELECT e.id,c.dyn_id,e.reason FROM enrollment e
        JOIN campaign c ON c.id=e.campaign_id WHERE e.account_id=? AND e.stage='blocked'
        AND NOT EXISTS(SELECT 1 FROM catalog_removed x WHERE x.dyn_id=c.dyn_id)
        AND ({_EXPIRES} IS NULL OR {_EXPIRES}>?)
        AND c.draw_state NOT IN ('ended','unsupported')
        AND NOT EXISTS(SELECT 1 FROM pool_item p WHERE p.account_id=e.account_id AND p.campaign_id=c.id)
        AND NOT EXISTS(SELECT 1 FROM up_block b WHERE b.uid=c.up_uid AND b.active=1)
        AND NOT EXISTS(SELECT 1 FROM closed_shelf s WHERE s.account_id=e.account_id AND s.campaign_id=c.id)
        ORDER BY e.updated_at,e.id""", (owner,stamp(box.clock.now()))).fetchall()
    selected = [r for r in rows if history_pending(r['reason'])][:limit]
    checked = ready = 0
    if not selected:return dict(checked=0,ready=0)
    enroll = box._enroll(owner)
    for row in selected:
        if cancelled():break
        checked += 1
        try:
            result = enroll.recheck(owner, row['dyn_id'])
        except (MissingCredential,WriteUnavailable):
            raise
        except Exception as exc:
            if isinstance(exc,RemoteError) and exc.fault not in (Fault.PARSE,Fault.REMOTE_BUSINESS):raise
            box.repos.enrollments.move(row['id'],Stage.BLOCKED,now=box.clock.now(),reason='复核需人工处理：'+safe_exception(exc))
            continue
        if result.skipped:continue
        complete = bool(result.steps) and all(step.outcome.value in (
            'settled','duplicate','skipped_present','not_applicable') for _,step in result.steps)
        # A dry run records PLANNED even for unknown probes; keep those quarantined.
        box.repos.enrollments.move(row['id'],Stage.PLANNED if complete else Stage.BLOCKED,
            now=box.clock.now(),reason='' if complete else (result.reason or row['reason']))
        ready += int(complete)
    return dict(checked=checked,ready=ready)


def repair_legacy_likes(box):
    """Retire explicit rejections made by the old form-only like adapter once.

    Keep the old receipt and time for audit/quota. The next normal schedule must
    still probe ABSENT, reserve a fresh intent, and hold the writer lease. Never
    release an ambiguous send, or a rejection from the corrected JSON adapter.
    """
    key = 'repair.like_json.v1'
    changed = 0
    with box.db.write() as db:
        if db.execute('SELECT 1 FROM setting WHERE account_id=0 AND key=?', (key,)).fetchone():
            return 0
        rows = db.execute("""SELECT i.id,i.account_id,i.campaign_id FROM intent i
            WHERE i.action='like' AND i.state='rejected' AND i.error_class='remote_business'
            AND i.id=(SELECT max(j.id) FROM intent j WHERE j.account_id=i.account_id
                AND j.campaign_id=i.campaign_id AND j.action='like')
            AND NOT EXISTS(SELECT 1 FROM intent j WHERE j.account_id=i.account_id
                AND j.campaign_id=i.campaign_id AND j.state IN ('unknown','reserved'))""").fetchall()
        for row in rows:
            db.execute("UPDATE intent SET state='legacy_like_rejected' WHERE id=?", (row['id'],))
            args = (row['account_id'], row['campaign_id'])
            db.execute("""UPDATE enrollment SET stage='planned',reason='点赞请求已修复，等待核查实际状态'
                WHERE account_id=? AND campaign_id=? AND stage='blocked' AND reason LIKE '%点赞%'""", args)
            db.execute("""UPDATE pool_item SET state='pending',message='点赞请求已修复，等待核查实际状态'
                WHERE account_id=? AND campaign_id=? AND state='review' AND message LIKE '%点赞%'""", args)
            changed += 1
        db.execute('INSERT INTO setting VALUES(0,?,?)', (key, str(changed)))
    return changed


def reclassify_checkins(box):
    if box.repos.settings.get(0,'repair.checkin.v2',''):
        return 0
    changed = 0
    with box.db.write() as db:
        for row in db.execute("SELECT id,body_text FROM campaign WHERE kind='check_in' AND body_text!=''").fetchall():
            kind = kind_from(row['body_text'])
            if kind in (CampaignKind.CHECK_IN, CampaignKind.UNKNOWN):continue
            db.execute('UPDATE campaign SET kind=? WHERE id=?',(kind.value,row['id']))
            db.execute("UPDATE pool_item SET message=? WHERE campaign_id=? AND state='review' AND message=?",
                       ('参与类型已更正，等待按常规流程复核',row['id'],'打卡活动需要手动完成指定任务，请查看原动态'))
            changed += 1
        db.execute("INSERT INTO setting VALUES(0,'repair.checkin.v2',?) ON CONFLICT(account_id,key) DO UPDATE SET value=excluded.value",(str(changed),))
    return changed


def reconcile_follows(box, owner, *, limit=3, cancelled=lambda:False):
    """Only original active participation is repaired, under the same account lane."""
    now=box.clock.now()
    from ..adapters.sqlite.repos import _EXPIRES
    rows=box.db.connect().execute(f"""SELECT DISTINCT c.id,c.dyn_id,c.up_uid,c.published_at,
        {_EXPIRES} expires
        FROM campaign c JOIN intent i ON i.campaign_id=c.id
        WHERE i.account_id=? AND i.action='share' AND i.state='done'
        AND NOT EXISTS(SELECT 1 FROM catalog_removed x WHERE x.dyn_id=c.dyn_id)
        AND i.created_at>=? AND c.up_uid!=''
        AND c.draw_state NOT IN ('ended','unsupported','unverified')
        AND NOT EXISTS(SELECT 1 FROM up_block b WHERE b.uid=c.up_uid AND b.active=1)
        AND NOT EXISTS(SELECT 1 FROM closed_shelf s WHERE s.account_id=? AND s.campaign_id=c.id)
        AND NOT EXISTS(SELECT 1 FROM tidy_item t JOIN tidy_plan p ON p.id=t.plan_id
            WHERE p.account_id=i.account_id AND t.campaign_id=c.id AND t.state='done')
        ORDER BY c.id DESC""",(owner,(now-timedelta(days=7)).isoformat(),owner)).fetchall()
    from .pool import _as_time
    rows=[r for r in rows if not r['expires'] or (_as_time(r['expires']) and _as_time(r['expires'])>now)]
    if not rows:return dict(checked=0,repaired=0)
    history=json.loads(box.repos.settings.get(owner,'participation.follow_audit','{}'))
    rows=sorted(rows,key=lambda r:float(history.get(str(r['id']),{}).get('at',0)))
    gateway=box.gateway_for(owner)
    journal=box.journal(gateway,owner)
    checked=repaired=0
    for row in rows:
        if checked>=limit or cancelled():break
        previous=history.get(str(row['id']),{})
        age=now.timestamp()-float(previous.get('at',0))
        if age < (86400 if previous.get('ok') else 900):continue
        if row['up_uid']==gateway.whoami():continue
        action=Action(ActionKind.FOLLOW,row['dyn_id'],{'up_uid':row['up_uid']})
        note='';ok=False
        try:
            state=gateway.did(ActionKind.FOLLOW,dyn_id=row['dyn_id'],payload=action.payload)
            if state is ProbeState.PRESENT:
                note='已关注';ok=True
            elif state is ProbeState.ABSENT:
                published=_as_time(row['published_at'])
                shared=gateway.did(ActionKind.SHARE,dyn_id=row['dyn_id'],payload={
                    'published_at':str(int(published.timestamp())) if published else ''})
                if shared is not ProbeState.PRESENT:
                    note='未核实本人转发，暂不补关注'
                elif not box.quota(owner).check(owner).allowed:
                    break
                else:
                    detail=gateway.detail(row['dyn_id'])
                    from .deadline_review import infer
                    evidence=infer(detail.text,detail.published_at or _as_time(row['published_at']))
                    expired=(detail.deadline and detail.deadline<=box.clock.now()) or (not detail.draw and evidence and evidence[0]<=box.clock.now())
                    if expired or (detail.draw and (detail.draw.state!='active' or
                            (detail.draw.deadline and detail.draw.deadline<=box.clock.now()))):
                        note=(detail.draw.reason if detail.draw else '') or '抽奖已结束或状态待核对，未补关注'
                        checked+=1
                        history[str(row['id'])]=dict(at=now.timestamp(),ok=False,note=note)
                        box.repos.settings.put(owner,'participation.follow_audit',json.dumps(history,ensure_ascii=False))
                        continue
                    step=journal.attempt(account_id=owner,campaign_id=row['id'],action=action)
                    ok=step.outcome.value in ('settled','skipped_present','duplicate')
                    note=step.message
                    repaired += int(ok and step.wrote)
                    if step.fault in (Fault.AUTH,Fault.RISK,Fault.RATE_LIMIT):
                        raise RemoteError(step.fault,note)
            else:
                note=getattr(gateway,'probe_reason','') or '关注关系待核对'
        except RemoteError as exc:
            if exc.fault not in (Fault.REMOTE_BUSINESS,Fault.PARSE):raise
            note=str(exc)
        checked+=1
        history[str(row['id'])]=dict(at=now.timestamp(),ok=ok,note=note)
        history=dict(sorted(history.items(),key=lambda pair:pair[1]['at'],reverse=True)[:1000])
        box.repos.settings.put(owner,'participation.follow_audit',json.dumps(history,ensure_ascii=False))
        box.bus.publish('participation.step',dict(account_id=owner,dyn_id=row['dyn_id'],message='补查关注 · '+note))
    return dict(checked=checked,repaired=repaired)
