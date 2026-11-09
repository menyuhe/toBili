"""User-selected curated catalog; keep write journals for deduplication."""
from datetime import datetime, timedelta, timezone
from ..adapters.sqlite.db import parse_stamp, stamp
from ..domain.prize_label import concise_reward
from .deadline_review import infer

CN = timezone(timedelta(hours=8))


def enabled(db):
    row = db.connect().execute("SELECT value FROM setting WHERE account_id=0 AND key='catalog.curated'").fetchone()
    return bool(row and row[0] == '1')


def choose_deadline(row):
    existing=parse_stamp(row['deadline_at'])
    if existing and (row.get('date_pinned') or row.get('draw_state') in ('active','ended')):
        return existing,'existing'
    day = row.get('candidate_at')
    if day and row.get('precision') == 'day' and row.get('date_state') != 'conflict':
        return parse_stamp(day).astimezone(CN).replace(hour=0,minute=0,second=0,microsecond=0), '日期默认零点'
    published = parse_stamp(row['published_at'])
    fact = infer(row.get('body_text') or '', published)
    if existing:
        if fact and fact[1]=='day' and fact[0].astimezone(CN).date()==existing.astimezone(CN).date():
            return existing.astimezone(CN).replace(hour=0,minute=0,second=0,microsecond=0),'日期默认零点'
        return existing,'existing'
    if fact:
        dt, precision, _ = fact
        return (dt.astimezone(CN).replace(hour=0,minute=0,second=0,microsecond=0) if precision=='day' else dt), '正文开奖时间'
    if published:
        return (published.astimezone(CN)+timedelta(days=7)).replace(hour=0,minute=0,second=0,microsecond=0), '发布时间后七天默认零点'
    return None, ''


def maintain(db, now, *, discard_pending=False):
    if not enabled(db): return {'removed':0,'dates':0,'labels':0}
    counts={'removed':0,'dates':0,'labels':0}
    with db.write() as c:
        import json
        pinned=set()
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='pro_edits'").fetchone():
            pinned={r[0] for r in c.execute('SELECT dyn,accepted FROM pro_edits') if json.loads(r[1]).get('deadline_at')}
        rows=c.execute("SELECT c.*,d.candidate_at,d.precision,d.state date_state FROM campaign c LEFT JOIN deadline_check d ON d.campaign_id=c.id WHERE NOT EXISTS(SELECT 1 FROM catalog_removed x WHERE x.dyn_id=c.dyn_id)").fetchall()
        for raw in rows:
            r=dict(raw); cid=r['id']
            r['date_pinned']=r['dyn_id'] in pinned
            override=c.execute('SELECT value FROM deadline_override WHERE campaign_id=?',(cid,)).fetchone()
            dt,origin=(parse_stamp(override[0]),'manual') if override else choose_deadline(r)
            if dt and (parse_stamp(r['deadline_at']) != dt or (r.get('candidate_at') and origin != 'existing')):
                c.execute('UPDATE campaign SET deadline_at=? WHERE id=?',(stamp(dt),cid))
                c.execute('DELETE FROM deadline_check WHERE campaign_id=?',(cid,))
                if not override:c.execute("INSERT OR REPLACE INTO deadline_origin VALUES(?,?,?)",(cid,origin,stamp(dt)))
                counts['dates']+=1
            label=concise_reward(r['reward'])
            if label!=r['reward']:
                c.execute('UPDATE campaign SET reward=? WHERE id=?',(label,cid));counts['labels']+=1
            reason = ('已开奖或过期' if r['draw_state']=='ended' or (dt and dt<=now) else
                      '参与类型不可用' if r['kind'] in ('unknown','check_in') or r['draw_state']=='unsupported' else
                      '奖品未识别' if r['reward_status']=='unknown' or label in ('奖品待补全','待识别','未知','未识别') or (discard_pending and (not label or r['reward_status'] not in ('verified','manual'))) else
                      '资料未核对' if discard_pending and (r['draw_state']=='unverified' or not dt) else '')
            if reason:
                c.execute('INSERT OR IGNORE INTO catalog_removed VALUES(?,?,?)',(r['dyn_id'],reason,stamp(now)))
                c.execute("DELETE FROM pool_item WHERE campaign_id=?",(cid,))
                counts['removed']+=1
    return counts
