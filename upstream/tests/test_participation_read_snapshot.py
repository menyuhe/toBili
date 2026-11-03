import httpx
import pytest

from src.bilibili_client import BilibiliClient
from src.lottery_api import DYNAMIC_DETAIL_URL, OPUS_DETAIL_URL, fetch_dynamic_detail
from src.participation import _resolve_reserve_info


@pytest.fixture
def mock_client(monkeypatch):
    monkeypatch.setattr("src.bilibili_client._load_cookie_string", lambda: "")
    monkeypatch.setattr("src.bilibili_client.acquire_bilibili_request_slot", lambda: None)
    with BilibiliClient(warmup=False) as client:
        client._client.close()
        yield client


def test_successful_reads_reused_only_in_scope_and_post_invalidates(mock_client):
    calls = []
    def handle(request):
        calls.append((request.method, str(request.url)))
        return httpx.Response(200, json={"code": 0, "data": len(calls)})
    mock_client._client = httpx.Client(transport=httpx.MockTransport(handle))
    url = "https://api.bilibili.com/test"
    with mock_client.read_snapshot():
        assert mock_client.get_json(url)["data"] == 1
        assert mock_client.get_json(url)["data"] == 1
        mock_client._http_post(url)
        assert mock_client.get_json(url)["data"] == 3
        # Different params represent another read.
        assert mock_client.get_json(url, {"id": "2"})["data"] == 4
    assert mock_client.get_json(url)["data"] == 5


def test_api_errors_are_not_cached(mock_client):
    responses = iter([{"code": -1}, {"code": 0}])
    mock_client._client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json=next(responses))))
    with mock_client.read_snapshot():
        assert mock_client.request_json("https://api.bilibili.com/test")["code"] == -1
        assert mock_client.request_json("https://api.bilibili.com/test")["code"] == 0


def test_412_falls_back_once_and_opus_reserve_is_found(mock_client):
    calls = []
    reserve = {"rid": "5815332", "reserve_total": "4275", "button": {"status": 2}}
    opus = {"id_str": "1258255633390501892", "modules": [
        {"module_author": {"mid": "196356191"}},
        {"module_content": {"paragraphs": [{"link_card": {"card": {"reserve": reserve}}}]}},
        {"module_stat": {"forward": {"count": 10}}},
    ]}
    def handle(request):
        url = str(request.url).split("?")[0]
        calls.append(url)
        if url == DYNAMIC_DETAIL_URL:
            return httpx.Response(412)
        assert url == OPUS_DETAIL_URL
        return httpx.Response(200, json={"code": 0, "data": {"item": opus}})
    mock_client._client = httpx.Client(transport=httpx.MockTransport(handle))
    with mock_client.read_snapshot():
        for _ in range(5):
            info = _resolve_reserve_info(mock_client, opus["id_str"])
            assert info["reserve_id"] == 5815332
            assert info["button_status"] == 2
        detail = fetch_dynamic_detail(mock_client, opus["id_str"])
        assert "module_content" in detail["modules"]
        assert detail["modules"]["module_stat"]["forward"]["count"] == 10
    assert calls == [DYNAMIC_DETAIL_URL, OPUS_DETAIL_URL]


def test_snapshot_exception_does_not_leak(mock_client):
    with pytest.raises(RuntimeError), mock_client.read_snapshot():
        raise RuntimeError("interrupted")
    assert mock_client._read_snapshot is None


@pytest.mark.parametrize('eventually_visible', [True, False])
def test_favorite_verification_refreshes_snapshot(mock_client, monkeypatch, eventually_visible):
    from src.lottery_actions import favorite_dynamic
    monkeypatch.setattr('src.lottery_actions.cancellable_sleep', lambda seconds: None)
    reads, writes = [], []
    def handle(request):
        if request.method == 'POST':
            writes.append(request)
            return httpx.Response(200, json={'code': 0})
        reads.append(request)
        status = eventually_visible and len(reads) >= 3
        return httpx.Response(200, json={'code': 0, 'data': {'item': {
            'modules': [{'module_stat': {'favorite': {'status': status}}}]}}})
    mock_client._client = httpx.Client(transport=httpx.MockTransport(handle))
    with mock_client.read_snapshot():
        result = favorite_dynamic(mock_client, dynamic_id='123', csrf='test', referer='https://www.bilibili.com/')
    assert result.ok == eventually_visible
    assert len(writes) == 1
    assert len(reads) == (3 if eventually_visible else 5)


def test_nested_snapshot_write_invalidates_outer_snapshot(mock_client):
    calls = []
    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, json={"code": 0, "data": len(calls)})
    mock_client._client = httpx.Client(transport=httpx.MockTransport(handle))
    url = "https://api.bilibili.com/test"
    with mock_client.read_snapshot():
        assert mock_client.get_json(url)["data"] == 1
        with mock_client.read_snapshot():
            mock_client._http_post(url)
        assert mock_client.get_json(url)["data"] == 3
