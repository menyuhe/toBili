import time
from fastapi.testclient import TestClient
from cloud.app import create_app
from cloud.catalog import CatalogRow, prune
from sqlalchemy import select


def client(tmp_path):
    app=create_app('sqlite:///'+str(tmp_path/'relay.db'),'https://relay.test','test-invite')
    c=TestClient(app,base_url='https://relay.test')
    response=c.post('/api/v1/auth/register',headers={'Origin':'https://relay.test'},json={'username':'tester','password':'long-test-password','device_name':'test','kind':'windows','invite':'test-invite'})
    assert response.status_code==200,response.text
    c.headers.update({'Authorization':'Bearer '+response.json()['device_token']})
    return c,app


def test_catalog_only_dedup_retention_and_legacy_rejection(tmp_path):
    c,app=client(tmp_path)
    now=int(time.time())
    items=[{'dynamic_id':str(i),'title':'奖品','lottery_time':stamp} for i,stamp in [(101,now+86400),(102,now-8*86400),(103,None),(104,now-6*86400)]]
    assert c.post('/api/v1/catalog',json={'items':items}).json()['inserted']==3
    assert c.post('/api/v1/catalog',json={'items':items}).json()['inserted']==0
    assert c.post('/api/v1/catalog',json={'items':[{**items[0],'activity_status':'已参加'}]}).status_code==422
    assert c.post('/api/v1/catalog',json={'items':[{**items[0],'cookie':'secret'}]}).status_code==422
    assert c.post('/api/v1/catalog/missing',json={'ids':['101','105']}).json()=={'ids':['105']}
    page=c.get('/api/v1/catalog?limit=2').json()
    assert len(page['items'])==2 and page['next']
    assert len(c.get('/api/v1/catalog?after='+page['next']).json()['items'])==1
    for path in ('tasks','sync/push','leases/acquire','records/keep'):
        assert c.post('/api/v1/'+path,json={}).status_code==410
    with app.state.store.transaction() as session: prune(session,now+2*86400)
    assert {a['dynamic_id'] for a in c.get('/api/v1/catalog').json()['items']}=={'101','103'}
    assert TestClient(app).get('/api/v1/catalog').status_code==401


def test_local_sync_never_uploads_personal_state_or_reuploads_existing(isolated_home,tmp_path):
    from src.activity_store import replace_all_activities
    from src.accounts import write_state,read_state
    from src.catalog_sync import synchronize
    from src.account_context import account_scope
    c,app=client(tmp_path)
    replace_all_activities([{'dynamic_id':'101','activity_title':'奖品','lottery_type':'转发抽奖','lottery_time':int(time.time())+86400,'activity_status':'已参加','platform_participated':True}])
    write_state('1','archives',['101'])
    requests=[]
    def request(method,path,body=None):
        requests.append((method,path,body))
        r=c.request(method,'/api/v1'+path,json=body)
        assert r.status_code==200,r.text
        return r.json()
    with account_scope('1'):
        assert synchronize(request)['uploaded']==1
        requests.clear()
        assert synchronize(request)['uploaded']==0
    assert not any(method=='POST' and path=='/catalog' for method,path,body in requests)
    assert read_state('1','archives')==['101']
    assert 'activity_status' not in c.get('/api/v1/catalog').json()['items'][0]


def test_tasks_stay_local_even_if_server_unavailable(monkeypatch):
    from src.cloud_client import CloudClient
    client=CloudClient()
    monkeypatch.setattr(client,'request',lambda *a,**k: (_ for _ in ()).throw(AssertionError('unexpected cloud request')))
    client.execution={'quarantine':True,'executor':'other'}
    assert client.blocked_reason()==''
    assert client.execute(1,'participate',{},lambda:{'ok':True})=={'ok':True}


def test_migration_deduplicates_accounts_and_purges_legacy_state(tmp_path):
    import json
    from cloud.models import Record, Task
    c, app = client(tmp_path)
    owner=c.get('/api/v1/me').json()['user_id']
    with app.state.store.transaction() as session:
        for uid in ('1','2'):
            session.add(Record(user_id=owner,bili_uid=uid,kind='activity',key='101',version=1,deleted=False,value_json=json.dumps({'dynamic_id':'101','title':'奖品','activity_status':'已参加'})))
            session.add(Record(user_id=owner,bili_uid=uid,kind='archive',key='101',version=2,deleted=False,value_json='{"archived_at":1}'))
    app.state.store.engine.dispose()
    updated=create_app('sqlite:///'+str(tmp_path/'relay.db'),'https://relay.test','test-invite')
    with updated.state.store.transaction() as session:
        rows=session.scalars(select(CatalogRow)).all()
        assert len(rows)==1 and 'activity_status' not in rows[0].payload
        assert session.scalars(select(Record)).all()==[]
        assert session.scalars(select(Task)).all()==[]
    assert (tmp_path/'relay.db.before-catalog-v2').exists()


def test_local_guard_has_no_cloud_lease_requirement(isolated_home):
    from src.cloud_guard import check_cloud_write
    from src.cloud_client import save_connection
    save_connection({'server':'https://offline.test','device_token':'fake'})
    check_cloud_write()
