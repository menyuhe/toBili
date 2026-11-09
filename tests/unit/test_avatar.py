import httpx
import pytest

from tobili.adapters.avatar import AvatarCache, image_url

URL = "https://i0.hdslb.com/bfs/face/example.jpg"
PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"


def test_local_avatar_survives_offline_and_changed_url_has_own_cache(tmp_path):
    calls = []

    def fetch(request):
        calls.append(request)
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return httpx.Response(200, content=PNG)

    cache = AvatarCache(tmp_path, transport=httpx.MockTransport(fetch))
    assert cache.get(URL) == (PNG, "image/png")
    assert cache.get(URL) == (PNG, "image/png")
    assert len(calls) == 1
    cache.get(URL.replace("example", "changed"))
    assert len(calls) == 2
    offline = AvatarCache(tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert offline.get(URL) == (PNG, "image/png")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/bfs/face/a",
        "https://i0.hdslb.com.evil.test/bfs/a",
        "https://i0.hdslb.com:123/bfs/a",
        "https://a@i0.hdslb.com/bfs/a",
        "file:///a",
        "https://i0.hdslb.com/bfs/a?token=secret",
    ],
)
def test_no_arbitrary_proxy(url):
    with pytest.raises(ValueError):
        image_url(url)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"Location": "http://127.0.0.1"}),
        httpx.Response(200, content=b"<html>denied</html>"),
        httpx.Response(200, content=PNG + b"x" * 512_000),
    ],
)
def test_reject_bad_responses_and_backoff(tmp_path, response):
    calls = []

    def fetch(r):
        calls.append(r)
        return response

    cache = AvatarCache(tmp_path, transport=httpx.MockTransport(fetch))
    for _ in range(2):
        with pytest.raises(ValueError):
            cache.get(URL)
    assert len(calls) == 1


def test_legacy_http_and_webp_are_normalized():
    assert image_url(URL.replace("https:", "http:") + "@96w_96h_1c.webp") == URL + "@96w_96h_1c.png"
