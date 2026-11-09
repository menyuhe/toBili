from types import SimpleNamespace

import httpx
import pytest

from tobili.adapters.bili.http import Wire
from tobili.domain.errors import RemoteError
from tobili.usecases.official_review import pending


def test_reads_retries_and_writes_share_request_spacing(monkeypatch):
    now=[100.0]
    monkeypatch.setattr('tobili.adapters.bili.http.time.monotonic',lambda:now[0])
    monkeypatch.setattr('tobili.adapters.bili.http.time.sleep',lambda seconds:now.__setitem__(0,now[0]+seconds))
    calls=[]
    def handler(request):
        calls.append((request.method,now[0]))
        return httpx.Response(503 if len(calls)==1 else 200,json={'code':0,'data':{}})
    with Wire(gap=1.5,attempts=1) as wire:
        wire._session.close()
        wire._session=httpx.Client(transport=httpx.MockTransport(handler))
        wire.fetch('https://api.bilibili.com/read')
        wire.fetch('https://api.bilibili.com/check')
        wire.submit('https://api.bilibili.com/write',{})
    assert [m for m,_ in calls]==['GET','GET','GET','POST']
    assert all(b-a>=1.5 for (_,a),(_,b) in zip(calls,calls[1:]))


def test_412_is_not_retried():
    calls=[]
    with Wire(gap=0,attempts=3) as wire:
        wire._session.close()
        wire._session=httpx.Client(transport=httpx.MockTransport(lambda req:(calls.append(req),httpx.Response(412))[1]))
        with pytest.raises(RemoteError):wire.fetch('https://api.bilibili.com/read')
    assert len(calls)==1


def test_official_background_respects_account_cooldown(monkeypatch):
    monkeypatch.setattr('tobili.usecases.official_review.time.time',lambda:100)
    box=SimpleNamespace(account=lambda:(2,object()),repos=SimpleNamespace(settings=SimpleNamespace(get=lambda *args:'200')))
    # No database or gateway is supplied: neither may be touched during cooldown.
    assert pending(box)==0
