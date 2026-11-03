from unittest.mock import Mock

import httpx
import pytest

from src.bilibili_client import BilibiliClient
from src.lottery_actions import REPOST_URL, repost_dynamic


def call(client):
    return repost_dynamic(client, dynamic_id="1263316672551321640", my_uid=123,
                          csrf="test-csrf", referer="https://t.bilibili.com/1263316672551321640",
                          content="好运" * 150)


def test_new_repost_json_and_cleanup_association():
    client = Mock(spec=BilibiliClient)
    client.post_json.return_value = {"code": 0, "data": {"dyn_id_str": "999888777666555444"}}
    result = call(client)
    assert result.ok and result.metadata == {"repost_id": "999888777666555444"}
    args, kwargs = client.post_json.call_args
    assert args[0] == "https://api.bilibili.com/x/dynamic/feed/create/dyn"
    assert args[1]["dyn_req"]["scene"] == 4
    assert args[1]["web_repost_src"] == {"dyn_id_str": "1263316672551321640"}
    assert len(args[1]["dyn_req"]["content"]["contents"][0]["raw_text"]) == 233
    assert kwargs["params"] == {"csrf": "test-csrf"}
    assert kwargs["retries"] == 0
    client.post_form.assert_not_called()


@pytest.mark.parametrize("message", ["该动态已删除", "账号已被封禁", "重复请求被拒绝"])
def test_failure_is_not_mistaken_for_already_reposted(message):
    client = Mock(spec=BilibiliClient)
    client.post_json.return_value = {"code": -1, "message": message}
    assert not call(client).ok


def test_uncertain_post_is_never_retried_or_sent_to_legacy_endpoint(monkeypatch):
    client = BilibiliClient.__new__(BilibiliClient)
    send = Mock(side_effect=httpx.ReadTimeout("uncertain result"))
    monkeypatch.setattr(client, "_http_post", send)
    with pytest.raises(RuntimeError):
        call(client)
    assert send.call_count == 1
    assert send.call_args.args[0] == REPOST_URL
