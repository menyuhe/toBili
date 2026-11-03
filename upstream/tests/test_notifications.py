from __future__ import annotations
import base64
import hashlib
import hmac
import httpx
import pytest
from src import notifications as n
from src.db.models import NotificationDeliveryRow
from src.db.session import session_scope


@pytest.fixture(autouse=True)
def sandbox(isolated_home, monkeypatch):
    monkeypatch.setattr(n,"settings_path",lambda:isolated_home/"config"/"notifications.json")
    monkeypatch.setattr(httpx.Client,"send",lambda *a,**k:pytest.fail("No real notification sending in tests"))


def configure():
    return n.save_settings({"audio":True,"dingtalk":True,"webhook":"https://oapi.dingtalk.com/robot/send?access_token=private-token","sign_secret":"SECprivate"})


def test_sign_and_masked_settings_retain_credentials():
    s=configure()
    assert "private" not in str(s) and s["webhook_hint"]
    n.save_settings({"webhook":None,"sign_secret":None,"prefix":"notice"})
    assert n.load_settings()["sign_secret"]=="SECprivate"
    expected=base64.b64encode(hmac.new(b"secret",b"123\nsecret",hashlib.sha256).digest()).decode()
    assert n.signed_params("secret",123)=={"timestamp":"123","sign":expected}
    assert n.signed_params("",123)=={}
    with pytest.raises(ValueError): n.save_settings({"webhook":"https://example.com/robot/send?access_token=x"})


def test_delivery_dedup_audio_and_dingtalk(monkeypatch):
    configure()
    calls=[]
    monkeypatch.setattr(n,"play_audio",lambda:calls.append("audio"))
    monkeypatch.setattr(n,"send_dingtalk",lambda *a:calls.append("dingtalk"))
    n.enqueue("123","won","event1","win","body")
    n.enqueue("123","won","event1","win","body")
    n.deliver_pending(); n.deliver_pending()
    assert sorted(calls)==["audio","dingtalk"]
    assert all(x["state"]=="sent" for x in n.history("123"))
    assert n.history("124")==[]


def test_finite_retries_and_restart_does_not_replay_audio(monkeypatch):
    configure(); calls=[]
    def fail(*a): calls.append(1); raise TimeoutError("https://secret")
    monkeypatch.setattr(n,"send_dingtalk",fail)
    n.enqueue("123","won","e","win","body",channel="dingtalk")
    for i in range(5):
        with session_scope() as s:
            row=s.get(NotificationDeliveryRow,("123","e","dingtalk"));row.next_attempt=0
        n.deliver_pending()
    assert len(calls)==3 and n.history("123")[0]["state"]=="failed"
    assert "secret" not in str(n.history("123"))
    n.enqueue("123","won","audio","win","body",channel="audio")
    with session_scope() as s: s.get(NotificationDeliveryRow,("123","audio","audio")).state="sending"
    n.recover_deliveries()
    monkeypatch.setattr(n,"play_audio",lambda:pytest.fail("must not repeat interrupted sound"))
    n.deliver_pending()
    assert any(r["state"]=="unknown" for r in n.history("123"))


def test_dingtalk_token_and_business_failure(monkeypatch):
    configure()
    captured=[]
    class Client:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,url,**kwargs):
            captured.append(kwargs)
            return httpx.Response(200,json={"errcode":310000,"errmsg":"invalid private-token"},request=httpx.Request("POST",url))
    monkeypatch.setattr(n.httpx,"Client",Client)
    with pytest.raises(RuntimeError) as exc: n.send_dingtalk(n.load_settings(),"test","body")
    assert "private-token" not in str(exc.value)
    assert captured[0]["params"]["access_token"]=="private-token"
    assert captured[0]["params"]["sign"]


def test_sensitive_files_and_keys_excluded():
    from src.secrets_inventory import SECRET_FILENAMES,is_sanitize_secret_key,is_redact_secret_key
    from src.log_redact import redact_text
    assert "notifications.json" in SECRET_FILENAMES
    assert is_sanitize_secret_key("webhook") and is_redact_secret_key("sign_secret")
    assert "abcdef123456" not in redact_text("https://oapi.dingtalk.com/robot/send?access_token=abcdef123456")


def test_repeated_state_does_not_notify_but_new_transition_does(monkeypatch):
    configure()
    n.save_settings({"events":["at","login"]})
    n.observe_value("123","at",4,"@","body",initial=3)
    n.observe_value("123","at",4,"@","body",initial=3)
    n.observe_value("123","at",4,"@","body",initial=3)
    assert len(n.history("123"))==2
    n.observe_value("123","at",1,"@","body")
    n.observe_value("123","at",4,"@","body")
    assert len(n.history("123"))==4
    n.observe_value("123","login",1,"expired","body")
    n.observe_value("123","login",1,"expired","body")
    assert len(n.history("123"))==6
    n.observe_value("123","login",0,"","")
    n.observe_value("123","login",1,"expired","body")
    assert len(n.history("123"))==8


def test_builtin_audio_is_finite_pcm_not_browser_audio(monkeypatch):
    import sys
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(n.sys,"platform","win32")
    monkeypatch.setitem(sys.modules,"winsound",SimpleNamespace(SND_MEMORY=4,PlaySound=lambda data,flags:calls.append((data,flags))))
    n.play_audio()
    assert len(calls)==1 and calls[0][0].startswith(b"RIFF")
    assert len(calls[0][0]) < 22050 * 2
