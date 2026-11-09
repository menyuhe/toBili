import json

import httpx
import pytest

from tobili.adapters.bili.http import Wire
from tobili.adapters.bili.writer import BiliWriter
from tobili.domain.action import Action, ActionKind
from tobili.domain.errors import Fault, RemoteError


def wire_for(reply, calls):
    wire = Wire("bili_jct=unit-test", gap=0)
    wire._session.close()
    wire._session = httpx.Client(
        transport=httpx.MockTransport(lambda req: calls.append(req) or reply)
    )
    return wire


@pytest.mark.parametrize("status", [2, "2", 1, None])
def test_reserve_json_contract_and_confirmed_state(status):
    calls = []
    with wire_for(
        httpx.Response(200, json={"code": 0, "data": {"final_btn_status": status}}), calls
    ) as wire:
        result = BiliWriter(wire).perform(Action(ActionKind.RESERVE, "123", {"reserve_id": "456"}))
    assert len(calls) == 1
    request = calls[0]
    assert request.url.path == "/x/dynamic/feed/reserve/click"
    assert request.url.params["csrf"] == "unit-test"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
    assert body["reserve_id"] == 456 and body["cur_btn_status"] == 1
    assert body["dynamic_id_str"] == "123" and "csrf" not in body
    assert result.ok == (status in (2, "2"))
    assert result.ambiguous == (status not in (2, "2"))


@pytest.mark.parametrize(
    "action",
    [
        Action(ActionKind.RESERVE, "123", {"reserve_id": "456"}),
        Action(ActionKind.COMMENT, "123", {"text": "test"}),
    ],
)
@pytest.mark.parametrize("body", ["<html>upstream error</html>", "[]", "{}", '{"code":"bad"}'])
def test_unreadable_write_never_claims_rejection_or_retries(action, body):
    calls = []
    with wire_for(httpx.Response(502, text=body), calls) as wire:
        receipt = BiliWriter(wire).perform(action)
    assert len(calls) == 1 and receipt.ambiguous and not receipt.ok
    assert receipt.fault is Fault.PARSE
    assert "unit-test" not in receipt.message


def test_reserved_button_never_toggles_back_off():
    calls = []
    with wire_for(httpx.Response(200, json={"code": 0}), calls) as wire:
        result = BiliWriter(wire).perform(
            Action(ActionKind.RESERVE, "123", {"reserve_id": "456", "button_status": "2"})
        )
    assert not result.ok and not calls


@pytest.mark.parametrize("status,fault", [(412, Fault.RISK), (429, Fault.RATE_LIMIT)])
def test_read_throttle_propagates_without_immediate_retry(status, fault):
    calls = []
    with (
        wire_for(httpx.Response(status, text="blocked"), calls) as wire,
        pytest.raises(RemoteError) as raised,
    ):
        wire.fetch("https://api.bilibili.com/x/test")
    assert raised.value.fault is fault and len(calls) == 1
