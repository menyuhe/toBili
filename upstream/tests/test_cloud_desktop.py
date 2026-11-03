import json

import httpx
import pytest

from src.cloud_client import CloudClient, load_connection, save_connection, validated_origin
from src.cloud_guard import check_cloud_write, guarded_writes
from src.log_redact import redact_obj


def test_url_transport_and_secret_masking(isolated_home):
    for address in ["http://public.example", "https://user:password@public.example", "https://public.example/path", "https://public.example?token=secret"]:
        with pytest.raises(ValueError):
            validated_origin(address)
    assert validated_origin("http://localhost:8787/") == "http://localhost:8787"
    assert validated_origin("https://public.example") == "https://public.example"
    assert redact_obj({"device_token": "SECRET", "lease_token": "SECRET"}) == {"device_token": "***", "lease_token": "***"}




def test_connect_stores_only_device_session_and_preserves_library_owner(isolated_home, monkeypatch):
    from src.cloud_client import connection_path
    client = CloudClient()
    monkeypatch.setattr(client, "assert_idle", lambda: None)
    calls = []
    owner = ["owner-one"]
    def request(config, method, path, body=None):
        calls.append(path)
        return {"user_id": owner[0], "username": "tester", "device_id": "device001", "device_token": "DEVICE_TOKEN", "protocol": 1}
    monkeypatch.setattr(client, "request", request)
    values = {"server": "https://relay.test", "username": "tester", "password": "NEVER_SAVED_PASSWORD", "device_name": "本机"}
    result = client.connect(values)
    assert result["connected"] and "DEVICE_TOKEN" not in json.dumps(result)
    assert "NEVER_SAVED_PASSWORD" not in connection_path().read_text(encoding="utf-8")
    client.disconnect()
    assert not load_connection()
    owner[0] = "different-owner"
    with pytest.raises(ValueError, match="另一应用账号"):
        client.connect(values)
    assert calls[-1] == "/auth/logout" and not load_connection()


def test_real_bilibili_post_calls_guard_before_transport(isolated_home, monkeypatch):
    from src.bilibili_client import BilibiliClient
    from src import bilibili_client
    monkeypatch.setattr(bilibili_client, "acquire_bilibili_request_slot", lambda: None)
    events = []
    with BilibiliClient(warmup=False) as client:
        client._client.close()
        client._client = httpx.Client(transport=httpx.MockTransport(lambda request: events.append("post") or httpx.Response(200, json={"code": 0})))
        with guarded_writes(lambda: events.append("lease")):
            client.post_form("https://api.bilibili.com/test", {})
        assert events == ["lease", "post"]
        def denied():
            raise RuntimeError("lease lost")
        with guarded_writes(denied), pytest.raises(RuntimeError, match="lease lost"):
            client.post_form("https://api.bilibili.com/test", {})
        assert events == ["lease", "post"]








def test_network_login_failure_is_not_reported_as_expired_cookie():
    from web.api_errors import require_login, AppError, ErrorCode
    with pytest.raises(AppError) as error:
        require_login({'logged_in': False, 'network_error': True})
    assert error.value.status_code == 503
    assert '暂时无法核验' in str(error.value)
    with pytest.raises(AppError) as error:
        require_login({'logged_in': False, 'expired': True})
    assert error.value.code == ErrorCode.AUTH_REQUIRED






