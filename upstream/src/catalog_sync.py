import time
from sqlmodel import select
from src.catalog_protocol import CatalogActivity
from src.db.models import ActivityRow
from src.db.session import session_scope
from src.db.activity_codec import row_to_activity_dict, activity_dict_to_row


def synchronize(request):
    local = {}
    with session_scope() as session:
        for row in session.exec(select(ActivityRow)).all():
            item = row_to_activity_dict(row)
            if not row.dynamic_id.isdigit() or (row.lottery_time and row.lottery_time < time.time()-7*86400):
                continue
            prizes = item.get('prizes') or []
            title = item.get('activity_title') or item.get('title') or (prizes[0].get('description','') if prizes and isinstance(prizes[0],dict) else '')
            value = {key:item[key] for key in CatalogActivity.model_fields if key in item and item[key] is not None}
            value.update(title=str(title)[:1000],business_id=str(row.business_id or ''),business_type=str(row.business_type or ''),sender_uid=str(item.get('sender_uid') or ''))
            local[row.dynamic_id] = CatalogActivity.model_validate(value).model_dump()
    uploaded = 0
    ids = sorted(local)
    for start in range(0,len(ids),500):
        batch = ids[start:start+500]
        missing = request('POST','/catalog/missing',{'ids':batch})['ids']
        if not set(missing)<=set(batch): raise ValueError('服务端返回未知活动 ID')
        for offset in range(0,len(missing),100):
            uploaded += request('POST','/catalog',{'items':[local[did] for did in missing[offset:offset+100]]})['inserted']
    cursor = ''; seen = set(); downloaded = 0
    while True:
        response = request('GET','/catalog?after='+cursor)
        values = [CatalogActivity.model_validate(value) for value in response['items']]
        with session_scope() as session:
            for value in values:
                if session.get(ActivityRow,value.dynamic_id) is not None: continue
                item = value.model_dump(); item['activity_title'] = item.pop('title')
                item['prizes'] = [{'description': item['activity_title'], 'winner_count': 0}]
                item['activity_status'] = '未参加'
                item['source_url'] = 'https://t.bilibili.com/'+value.dynamic_id
                session.add(activity_dict_to_row(item)); session.flush(); downloaded += 1
        cursor = response.get('next','')
        if not cursor: break
        if cursor in seen: raise ValueError('活动分页游标重复')
        seen.add(cursor)
    return {'uploaded':uploaded,'downloaded':downloaded,'conflicts':0,'revision':0,'pending':0}
