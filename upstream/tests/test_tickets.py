import json
from types import SimpleNamespace
from unittest.mock import MagicMock
import httpx
import pytest
from src import tickets as t
from src.db.models import TicketTaskRow
from src.db.session import session_scope
from src.ticket_api import TicketApi, project_id
from src.task_control import TaskCancelled

UID = "123"


@pytest.fixture(autouse=True)
def sandbox(isolated_home, monkeypatch):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("Unexpected network"))
    monkeypatch.setattr(t, "get_login_uid", lambda: UID)
    monkeypatch.setattr(t, "notify", lambda *a: None)


def seed(state="waiting", task_id="one", uid=UID, project="1"):
    config=dict(project_id=project, screen_id="2", sku_id="3", buyer_ids=["4"], address_id="", amount=100,
        interval=2, duration=10, name="测试演出", label="标准票", url="https://show.bilibili.com/platform/detail.html?id=1")
    with session_scope() as s:
        s.add(TicketTaskRow(id=task_id,uid=uid,state=state,config_json=json.dumps(config)))
    t.contact_path().write_text(json.dumps({task_id:dict(uid=uid,name="PRIVATE_NAME",phone="13812345678")}),encoding="utf-8")


class FakeApi:
    def __init__(self, response=None):
        self.response=response or {"errno":0,"data":{"orderId":12345}}
        self.creates=0
    def verify_login(self): pass
    def assert_account(self): pass
    def payload(self, config, contact): return {"amount":config["amount"]}
    def prepare(self,payload): return {"errno":0,"data":{"token":"private-token"}}
    def create(self, payload, token):
        self.creates+=1
        if isinstance(self.response,BaseException): raise self.response
        return self.response


def run(monkeypatch, api):
    monkeypatch.setattr(t,"TicketApi",lambda *a:api)
    return t.run_task(UID,"one",lambda **kw:None)


def test_success_is_payable_not_paid_and_no_secret_in_history(monkeypatch):
    seed(); api=FakeApi(); run(monkeypatch,api)
    row=t.get_task(UID,"one")
    assert row["state"]=="payment" and row["order_id"]=="12345"
    assert api.creates==1
    assert "PRIVATE_NAME" not in json.dumps(row) and "private-token" not in json.dumps(row)
    run(monkeypatch,api)
    assert api.creates==1
    with pytest.raises(ValueError):t.start_task(UID,"one")


@pytest.mark.parametrize("response", [TimeoutError(), {"errno":0,"data":{}}, {"errno":100044,"data":{}}, {"errno":100079,"data":{}}, {"data":{"orderId":"1"}}])
def test_uncertain_create_never_retries(monkeypatch,response):
    seed(); api=FakeApi(response); run(monkeypatch,api)
    assert t.get_task(UID,"one")["state"]=="unknown"
    assert api.creates==1
    seed(state="draft",task_id="two")
    with pytest.raises(ValueError):t.start_task(UID,"two")


def test_challenge_handoff_before_create(monkeypatch):
    seed();api=FakeApi();api.prepare=lambda p:{"errno":100044,"data":{"token":"bad"}}
    run(monkeypatch,api)
    assert t.get_task(UID,"one")["state"]=="handoff" and api.creates==0


def test_price_change_stops_without_accepting_new_amount(monkeypatch):
    seed();api=FakeApi({"errno":100034,"data":{"pay_money":50000}})
    run(monkeypatch,api)
    assert t.get_task(UID,"one")["state"]=="failed" and api.creates==1
    assert t.get_task(UID,"one")["config"]["amount"]==100


def test_explicit_out_of_stock_is_only_automatic_retry(monkeypatch):
    seed();api=FakeApi(); responses=iter([{"errno":100009,"data":{}},{"errno":0,"data":{"orderId":88}}])
    def create(*a):api.creates+=1;return next(responses)
    api.create=create
    monkeypatch.setattr(t,"cancellable_sleep",lambda _:None)
    run(monkeypatch,api)
    assert api.creates==2 and t.get_task(UID,"one")["state"]=="payment"


def test_cancellation_after_submission_preserves_unknown(monkeypatch):
    seed();api=FakeApi(TaskCancelled("任务已取消"))
    with pytest.raises(TaskCancelled):run(monkeypatch,api)
    assert t.get_task(UID,"one")["state"]=="unknown"


def test_switch_account_pauses_without_request(monkeypatch):
    seed();api=FakeApi();monkeypatch.setattr(t,"get_login_uid",lambda:"456")
    run(monkeypatch,api)
    assert api.creates==0 and t.get_task(UID,"one")["state"]=="paused"
    with pytest.raises(ValueError):t.get_task("456","one")


def test_restart_pauses_waiting_and_quarantines_ordering():
    seed();seed(state="ordering",task_id="two")
    t.recover_tasks()
    assert t.get_task(UID,"one")["state"]=="paused"
    assert t.get_task(UID,"two")["state"]=="unknown"


def test_waiting_task_only_due_runs_and_busy_defers():
    seed()
    with session_scope() as s:
        row=s.get(TicketTaskRow,"one");row.start_at=100;row.updated_at=99
    runner=MagicMock();runner.is_running.return_value=False;runner.try_start.return_value=1
    assert not t.tick(runner,now=99)
    runner.is_running.return_value=True
    assert not t.tick(runner,now=100)
    runner.is_running.return_value=False
    assert t.tick(runner,now=100)
    runner.try_start.assert_called_once_with("ticket_run",{"uid":UID,"task_id":"one"},source="system")


def test_missed_sale_window_does_not_start_late():
    seed(); runner=MagicMock();runner.is_running.return_value=False
    assert not t.tick(runner,now=1000)
    assert t.get_task(UID,"one")["state"]=="paused"


@pytest.mark.parametrize("url", ["https://evil.test/?id=1","http://show.bilibili.com/?id=1","https://mall.bilibili.com.evil.test/?id=1","https://show.bilibili.com/?id=x","0"])
def test_project_url_restrictions(url):
    with pytest.raises(ValueError):project_id(url)


def test_read_context_masks_buyer_and_address():
    api=object.__new__(TicketApi)
    api.project=lambda pid:dict(project_id=pid)
    api.buyers=lambda pid:[dict(id=1,name="张小明",personal_id="123456199901011234")]
    api.addresses=lambda:[dict(id=2,name="李小美",phone="13812345678",prov="浙江",city="杭州",addr="PRIVATE_STREET")]
    result=json.dumps(api.context("1"),ensure_ascii=False)
    assert "张小明" not in result and "PRIVATE_STREET" not in result and "13812345678" not in result
    assert "1234" in result


def test_posts_never_use_transport_retry():
    api=object.__new__(TicketApi);api.csrf="fake";api.request=MagicMock(return_value={"errno":0})
    api.prepare(dict(project_id=1,screen_id=2,sku_id=3,count=1,order_type=1,buyer_info=[]))
    assert api.request.call_args.kwargs["retries"]==0


def test_invalid_price_and_missing_buyer_are_rejected():
    api=object.__new__(TicketApi)
    api.project=lambda p:{"options":[dict(screen_id="2",sku_id="3",supported=True,price=100,express_fee=0)]}
    api.buyers=lambda p:[{"id":4}]
    config=dict(project_id="1",screen_id="2",sku_id="3",buyer_ids=["4"],amount=200,address_id="")
    with pytest.raises(ValueError,match="票价"):api.payload(config,{"name":"x","phone":"123456"})
    config["buyer_ids"]=["9"]
    with pytest.raises(ValueError,match="购票人"):api.payload(config,{})
