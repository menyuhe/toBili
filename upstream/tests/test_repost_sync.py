import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient
from test_catalog_sync import client
from src.repost_sync import synchronize, references, verified_repost
from src.db.models import ParticipationLinkRow, SyncedRepostRow, ParticipationRow
from src.db.session import session_scope
from cloud.reposts import RepostRow


def transport(c, requests):
    def request(method, path, body=None):
        requests.append((method,path,body))
        response = c.request(method, '/api/v1'+path, json=body)
        assert response.status_code == 200, response.text
        return response.json()
    return request


def test_two_accounts_devices_dedup_retention_and_tenant_isolation(isolated_home,tmp_path):
    c, app = client(tmp_path)
    refs = [dict(uid='11',dynamic_id='101',repost_id='901'),
            dict(uid='22',dynamic_id='101',repost_id='902'),
            dict(uid='11',dynamic_id='101',repost_id='903')]
    assert c.post('/api/v1/reposts',json={'items':refs}).json()['inserted']==3
    assert c.post('/api/v1/reposts',json={'items':refs}).json()['inserted']==0
    assert c.post('/api/v1/reposts',json={'items':[{**refs[0],'cookie':'no'}]}).status_code==422
    first=c.get('/api/v1/reposts?limit=2').json()
    assert first['more'] and len(first['items'])==2
    assert len(c.get('/api/v1/reposts?after='+str(first['cursor'])).json()['items'])==1
    from cloud.catalog import prune
    with app.state.store.transaction() as session:
        prune(session,9999999999)
        assert len(session.scalars(select(RepostRow)).all())==3
    calls=[]
    assert synchronize(transport(c,calls),'new-mac')['reposts_downloaded']==3
    assert set(references('11','101'))=={'901','903'}
    assert references('22','101')==['902']
    with session_scope() as session:
        assert session.exec(select(ParticipationLinkRow)).first() is None
        assert session.exec(select(ParticipationRow)).first() is None
    calls.clear()
    assert synchronize(transport(c,calls),'new-mac')['reposts_downloaded']==0
    assert all(method=='GET' for method,_,_ in calls)
    anonymous=TestClient(app,base_url='https://relay.test')
    assert anonymous.get('/api/v1/reposts').status_code==401
    r=anonymous.post('/api/v1/auth/register',headers={'Origin':'https://relay.test'},json={'username':'other','password':'long-test-password','device_name':'other','kind':'mac','invite':'test-invite'})
    anonymous.headers.update({'Authorization':'Bearer '+r.json()['device_token']})
    assert anonymous.get('/api/v1/reposts').json()['items']==[]


def test_upload_only_confirmed_and_retry_is_idempotent(isolated_home,tmp_path):
    c,_=client(tmp_path)
    with session_scope() as session:
        session.add(ParticipationLinkRow(uid='11',dynamic_id='101',repost_id='901',repost_recorded=True))
        session.add(ParticipationLinkRow(uid='22',dynamic_id='101',repost_id='902',repost_recorded=False))
    calls=[]
    assert synchronize(transport(c,calls),'win')['reposts_uploaded']==1
    calls.clear()
    synchronize(transport(c,calls),'win')
    assert not any(m=='POST' for m,_,_ in calls)
    # A lost acknowledgement/new device resends safely.
    assert synchronize(transport(c,calls),'retry')['reposts_uploaded']==0
    assert c.get('/api/v1/reposts').json()['items']==[dict(uid='11',dynamic_id='101',repost_id='901')]


def test_failed_page_does_not_advance_cursor(isolated_home):
    seen=[]
    def bad(method,path,body=None):
        seen.append(path)
        return {'items':[dict(uid='11',dynamic_id='101',repost_id='901'),{'uid':'invalid'}], 'cursor':2,'more':False}
    with pytest.raises(ValueError): synchronize(bad,'mac')
    assert references('11','101')==[]
    with pytest.raises(ValueError): synchronize(bad,'mac')
    assert seen==['/reposts?after=0','/reposts?after=0']


def test_verify_remote_evidence_never_grants_cleanup_or_confuses_uid(isolated_home,monkeypatch):
    with session_scope() as session:
        session.add(SyncedRepostRow(uid='11',dynamic_id='101',repost_id='901'))
    class Fake:
        value={'id_str':'901','type':'DYNAMIC_TYPE_FORWARD','modules':{'module_author':{'mid':'11'}},'orig':{'id_str':'101'}}
        def __init__(self,*a): pass
        def dynamic(self,did):
            if isinstance(self.value,Exception): raise self.value
            return self.value
    monkeypatch.setattr('src.cleanup_api.CleanupApi',Fake)
    assert verified_repost(None,'11','101')
    assert not verified_repost(None,'22','101')
    Fake.value=None
    assert not verified_repost(None,'11','101')
    Fake.value=RuntimeError('offline')
    with pytest.raises(RuntimeError,match='暂时无法核验'): verified_repost(None,'11','101')
