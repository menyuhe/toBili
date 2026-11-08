from types import SimpleNamespace

import pytest

from tobili.desktop.account_browser import (
    checked_url,
    command,
    cookie_pairs,
    install_cookies,
    status_path,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.bilibili.com/",
        "https://bilibili.com.evil.com/",
        "https://bilibili.com@evil.com/",
        "file:///tmp/cookies",
        "javascript:alert(1)",
        "https://127.0.0.1:8811/",
        "https://www.bilibili.com:8888/",
    ],
)
def test_account_browser_rejects_other_origins(url):
    with pytest.raises(ValueError):
        checked_url(url)


def test_account_cookie_mapping_and_secret_free_command(tmp_path):
    assert checked_url("https://message.bilibili.com/#/whisper/mid1")
    with pytest.raises(ValueError):
        cookie_pairs("DedeUserID=11; SESSDATA=secret", "22")
    jars = []
    for uid, secret in [("11", "private-a"), ("22", "private-b")]:
        jar = {}
        manager = SimpleNamespace(
            DeleteAllCookies=jar.clear,
            CreateCookie=lambda name, value, domain, path: SimpleNamespace(
                Name=name, Value=value, Domain=domain, Path=path
            ),
            AddOrUpdateCookie=lambda c, jar=jar: jar.update({c.Name: c}),
        )
        install_cookies(
            manager, cookie_pairs(f"DedeUserID={uid}; SESSDATA={secret}; bili_jct=csrf", uid)
        )
        assert (
            jar["DedeUserID"].Value == uid
            and jar["SESSDATA"].IsHttpOnly
            and not jar["bili_jct"].IsHttpOnly
        )
        jars.append(jar)
    assert jars[0]["SESSDATA"].Value == "private-a" and jars[1]["SESSDATA"].Value == "private-b"
    args = command(tmp_path, 2, "https://message.bilibili.com/", "a" * 32)
    assert "private-a" not in str(args) and "SESSDATA" not in str(args)
    with pytest.raises(ValueError):
        status_path(tmp_path, "../../vault/key")
