from types import SimpleNamespace
from datetime import timedelta

import pytest

from tobili.adapters.bili.writer import BiliWriter
from tobili.domain.action import Action, ActionKind as A
from tobili.domain.campaign import Campaign, CampaignKind as K
from tobili.domain.enrollment import Stage
from tobili.domain.probe import ProbeState as P
from tobili.runtime.journal import Journal, Step, Outcome
from tobili.usecases.enroll import EnrollReport
from tobili.usecases.participation_repair import recheck_catalog
from tobili.usecases.pool import history_pending

FAVORITE_REASON = '收藏已提交，但未确认成功；不会继续后续动作或自动重发'


@pytest.mark.parametrize('states,ok,reads', [([False,False,True],True,3),([False]*4,False,4),([None]*4,False,4)])
def test_accepted_favorite_waits_for_read_visibility_without_reposting(states,ok,reads):
    sent=[];seen=[];wait=[]
    def fetch(*args,**kwargs):
        state=states[len(seen)];seen.append(state)
        return {'code':0,'data':{'item':{'modules':[{'module_stat':{'favorite':{'status':state}}}]}}}
    wire=SimpleNamespace(csrf='test',fetch=fetch,
        submit_json=lambda *a,**kw:sent.append(a) or {'code':0})
    receipt=BiliWriter(wire,wait=wait.append).perform(Action(A.FAVORITE,'123',{'target':'opus'}))
    assert receipt.ok is ok and len(sent)==1 and len(seen)==reads
    assert len(wait)==reads-1


@pytest.mark.parametrize('state,expected',[(P.PRESENT,Outcome.SKIPPED_PRESENT),(P.ABSENT,Outcome.UNKNOWN),(P.UNKNOWN,Outcome.UNKNOWN)])
def test_previous_accepted_favorite_only_reconciles_positive_read(repos,clock,gateway,account_id,state,expected):
    cid=repos.campaigns.upsert(Campaign(dyn_id='123',up_uid='2002',title='test'))
    action=Action(A.FAVORITE,'123',{'target':'opus'})
    iid,_=repos.intents.reserve(account_id=account_id,campaign_id=cid,action=A.FAVORITE,
        fingerprint=action.fingerprint,content='',now=clock.now())
    repos.intents.settle(iid,ok=False,ambiguous=True,fault='parse',message=FAVORITE_REASON,now=clock.now())
    gateway.probe=state
    journal=Journal(gateway=gateway,intents=repos.intents,clock=clock)
    first=journal.inspect(account_id=account_id,campaign_id=cid,action=action)
    assert first.outcome is expected
    journal.attempt(account_id=account_id,campaign_id=cid,action=action)
    assert gateway.writes==[]
    assert repos.intents.state_of(iid)==('done' if state is P.PRESENT else 'unknown')
    if state is not P.PRESENT:assert history_pending('收藏：'+first.message)


def test_network_unknown_favorite_never_reconciles_or_resends(repos,clock,gateway,account_id):
    cid=repos.campaigns.upsert(Campaign(dyn_id='123',up_uid='2002',title='test'))
    action=Action(A.FAVORITE,'123',{'target':'opus'})
    iid,_=repos.intents.reserve(account_id=account_id,campaign_id=cid,action=A.FAVORITE,
        fingerprint=action.fingerprint,content='',now=clock.now())
    repos.intents.settle(iid,ok=False,ambiguous=True,fault='network',message='提交超时',now=clock.now())
    gateway.probe=P.PRESENT
    step=Journal(gateway=gateway,intents=repos.intents,clock=clock).attempt(account_id=account_id,campaign_id=cid,action=action)
    assert step.outcome is Outcome.UNKNOWN and gateway.writes==[] and gateway.probes==[]


def test_catalogue_read_checks_rotate_without_releasing_unknowns(repos,clock,account_id):
    ids=[]
    for n in range(4):
        cid=repos.campaigns.upsert(Campaign(dyn_id=str(n+123),up_uid='2002',title='test',kind=K.COMMENT,confidence=.95,deadline=clock.now()+timedelta(days=1)))
        eid=repos.enrollments.create(account_id,cid,now=clock.now())
        repos.enrollments.move(eid,Stage.BLOCKED,now=clock.now(),reason='转发：转发历史核查中')
        ids.append((cid,eid))
    checked=[]
    def recheck(owner,dyn):
        checked.append(dyn)
        n=int(dyn)-123;cid,eid=ids[n]
        step=Step(Outcome.SETTLED if n==0 else Outcome.SKIPPED_UNKNOWN,P.ABSENT if n==0 else P.UNKNOWN,'历史待核查')
        return EnrollReport(dyn,cid,eid,Stage.PLANNED,steps=(('转发',step),),reason='' if n==0 else '转发：转发历史核查中')
    box=SimpleNamespace(db=repos.db,repos=repos,clock=clock,_enroll=lambda aid:SimpleNamespace(recheck=recheck))
    clock.advance(seconds=1)
    assert recheck_catalog(box,account_id,limit=2)==dict(checked=2,ready=1)
    assert repos.enrollments.get(ids[0][1]).stage is Stage.PLANNED
    assert repos.enrollments.get(ids[1][1]).stage is Stage.BLOCKED
    clock.advance(seconds=1)
    recheck_catalog(box,account_id,limit=2)
    assert checked==['123','124','125','126']
    assert len(repos.campaigns.candidates(account_id,now=clock.now(),floor=.8,limit=10))==1


def test_catalogue_recheck_excludes_unrelated_rejections_and_cancellation(repos,clock,account_id):
    cid=repos.campaigns.upsert(Campaign(dyn_id='123',up_uid='2002',title='test'))
    eid=repos.enrollments.create(account_id,cid,now=clock.now())
    repos.enrollments.move(eid,Stage.BLOCKED,now=clock.now(),reason='评论：上次操作被拒绝')
    box=SimpleNamespace(db=repos.db,repos=repos,clock=clock,_enroll=lambda aid:None)
    assert recheck_catalog(box,account_id)==dict(checked=0,ready=0)
    repos.enrollments.move(eid,Stage.BLOCKED,now=clock.now(),reason='转发：转发历史核查中')
    assert recheck_catalog(box,account_id,cancelled=lambda:True)==dict(checked=0,ready=0)


def test_bad_catalogue_row_isolated_and_new_rows_remain_available(repos,clock,account_id):
    cid=repos.campaigns.upsert(Campaign(dyn_id='123',up_uid='2002',title='test'))
    eid=repos.enrollments.create(account_id,cid,now=clock.now())
    repos.enrollments.move(eid,Stage.BLOCKED,now=clock.now(),reason='转发：转发历史核查中')
    def broken(*args):raise ValueError('旧记录格式有误')
    box=SimpleNamespace(db=repos.db,repos=repos,clock=clock,_enroll=lambda aid:SimpleNamespace(recheck=broken))
    assert recheck_catalog(box,account_id)==dict(checked=1,ready=0)
    assert not history_pending(repos.enrollments.get(eid).reason)
