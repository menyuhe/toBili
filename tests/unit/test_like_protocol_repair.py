from types import SimpleNamespace

import pytest

from tobili.adapters.bili import endpoints
from tobili.adapters.bili.writer import BiliWriter
from tobili.domain.action import Action, ActionKind as A
from tobili.domain.campaign import Campaign
from tobili.domain.errors import AmbiguousWrite, Fault
from tobili.domain.probe import ProbeState as P
from tobili.domain.receipt import Receipt
from tobili.runtime.journal import Journal, Outcome
from tobili.usecases.participation_repair import repair_legacy_likes


def test_like_requires_json_and_csrf_query():
    calls = []
    def send(url, body, **options):
        assert url == endpoints.THUMB_DYN
        assert body == dict(dyn_id_str='123', up=1, spmid='333.1369.0.0', from_spmid='333.999.0.0')
        assert options['query'] == {'csrf': 'test'}
        calls.append(url)
        return {'code': 0}
    writer = BiliWriter(SimpleNamespace(csrf='test', submit_json=send))
    assert writer.perform(Action(A.LIKE, '123')).ok
    assert len(calls) == 1


@pytest.mark.parametrize('code', [0, 65006, -400, -412])
def test_like_receipt_preserves_rejection_code(code):
    wire = SimpleNamespace(csrf='test', submit_json=lambda *a, **k: {'code': code, 'message': '测试答复'})
    receipt = BiliWriter(wire).perform(Action(A.LIKE, '123'))
    assert receipt.ok == (code in (0, 65006))
    if not receipt.ok:
        assert str(code) in receipt.message
        assert receipt.fault == (Fault.RISK if code == -412 else Fault.REMOTE_BUSINESS)


def test_like_timeout_never_falls_back_to_form():
    calls = []
    def send(*a, **k):
        calls.append(1)
        raise AmbiguousWrite('timeout')
    receipt = BiliWriter(SimpleNamespace(csrf='test', submit_json=send)).perform(Action(A.LIKE, '123'))
    assert receipt.ambiguous and calls == [1]


def seed(repos, clock, account_id, action=A.LIKE, state='rejected', fault='remote_business', dyn_id='123'):
    cid = repos.campaigns.upsert(Campaign(dyn_id=dyn_id, up_uid='2002', title='测试', reward='奖品'))
    a = Action(action, dyn_id)
    iid, _ = repos.intents.reserve(account_id=account_id, campaign_id=cid, action=action,
                                  fingerprint=a.fingerprint, content='', now=clock.now())
    with repos.db.write() as db:
        db.execute('UPDATE intent SET state=?,error_class=?,message=? WHERE id=?', (state, fault, '请求错误', iid))
        db.execute("INSERT OR IGNORE INTO enrollment(account_id,campaign_id,stage,reason,created_at,updated_at) VALUES(?,?,'blocked','点赞：请求错误',?,?)",
                   (account_id,cid,clock.now().isoformat(),clock.now().isoformat()))
    return cid, iid, a


@pytest.mark.parametrize('probe,expected_writes', [(P.ABSENT, 1), (P.PRESENT, 0), (P.UNKNOWN, 0)])
def test_legacy_rejection_recovery_always_probes(repos, clock, gateway, account_id, probe, expected_writes):
    cid, iid, action = seed(repos, clock, account_id)
    box = SimpleNamespace(db=repos.db)
    assert repair_legacy_likes(box) == 1
    assert repair_legacy_likes(box) == 0
    assert repos.intents.state_of(iid) == 'legacy_like_rejected'
    assert repos.db.connect().execute('SELECT message FROM intent WHERE id=?',(iid,)).fetchone()[0] == '请求错误'
    gateway.probe = probe
    gateway.receipt = Receipt.success()
    journal = Journal(gateway=gateway, intents=repos.intents, clock=clock)
    journal.attempt(account_id=account_id, campaign_id=cid, action=action)
    journal.attempt(account_id=account_id, campaign_id=cid, action=action)
    assert len(gateway.writes) == expected_writes
    assert gateway.probes


@pytest.mark.parametrize('action,state,fault', [
    (A.LIKE,'unknown','network'), (A.LIKE,'reserved',''), (A.LIKE,'done',''),
    (A.LIKE,'rejected','risk'), (A.LIKE,'rejected','auth'), (A.SHARE,'rejected','remote_business'),
    (A.COMMENT,'rejected','remote_business'),
])
def test_repair_preserves_other_receipts(repos, clock, account_id, action, state, fault):
    _, iid, _ = seed(repos, clock, account_id, action, state, fault)
    assert repair_legacy_likes(SimpleNamespace(db=repos.db)) == 0
    assert repos.intents.state_of(iid) == state


def test_uncertain_other_action_blocks_recovery(repos, clock, account_id):
    _, iid, _ = seed(repos, clock, account_id)
    seed(repos, clock, account_id, A.SHARE, 'unknown', 'network')
    assert repair_legacy_likes(SimpleNamespace(db=repos.db)) == 0
    assert repos.intents.state_of(iid) == 'rejected'


def test_new_rejection_is_not_repeated_after_restart(repos, clock, account_id, gateway):
    cid, _, action = seed(repos, clock, account_id)
    box = SimpleNamespace(db=repos.db)
    repair_legacy_likes(box)
    gateway.receipt = Receipt.rejected(Fault.REMOTE_BUSINESS, '动态不可点赞（-400）')
    journal = Journal(gateway=gateway, intents=repos.intents, clock=clock)
    assert journal.attempt(account_id=account_id, campaign_id=cid, action=action).outcome == Outcome.REJECTED
    assert repair_legacy_likes(box) == 0
    journal.attempt(account_id=account_id, campaign_id=cid, action=action)
    assert len(gateway.writes) == 1
