"""收件箱接口。

真实网关要连 B 站，测试里换成一个假用例 —— 这里验证的是**接口的承诺**：
未登录不给读、读不到要说原因、标记已看只影响本机。

"已看不是已读回执"这条也在接口层锁一遍：返回体里必须带着那句说明，
不然前端迟早会把它写成"已清空红点"。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tobili.api.app import create_app
from tobili.domain.inbox import Message
from tobili.ports.models import AccountProfile
from tobili.usecases.inbox import InboxView


class FakeInbox:
    def __init__(self) -> None:
        self.seen: set[str] = set()
        # 数一数真正打了几次网关 —— 缓存有没有生效只能看这个。
        self.fetches = 0
        self.items = [
            Message(
                kind="chat",
                key="chat:1",
                who="某个 UP",
                text="恭喜你中奖了，请提供收货地址",
                at=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
                prize=True,
                reason="提到「中奖」",
            ),
            Message(
                kind="reply",
                key="reply:2",
                who="路人",
                text="帮我投个票",
                at=datetime(2026, 11, 4, 10, 0, tzinfo=UTC),
            ),
        ]

    def fetch(self, account_id: int, *, pages: int = 1, limit: int = 60) -> InboxView:
        self.fetches += 1
        rows = tuple(
            Message(
                kind=item.kind,
                key=item.key,
                who=item.who,
                text=item.text,
                at=item.at,
                unread=item.key not in self.seen,
                link=item.link,
                prize=item.prize,
                reason=item.reason,
            )
            for item in self.items
        )
        return InboxView(
            items=rows,
            system_count=3,
            counts={"total": len(rows), "unread": len(rows), "prize": 1},
        )

    def mark_seen(self, account_id: int, keys: list[str]) -> int:
        before = len(self.seen)
        self.seen.update(keys)
        return len(self.seen) - before

    def forget_seen(self, account_id: int) -> None:
        self.seen.clear()


@pytest.fixture
def box(tmp_path):  # type: ignore[no-untyped-def]
    app = create_app(home=str(tmp_path / "home"))
    with TestClient(app) as client:
        container = app.state.container
        container.repos.accounts.upsert(AccountProfile(uid="1001", nickname="甲"), "ref")
        fake = FakeInbox()
        container.inbox = lambda account_id=None: fake  # type: ignore[method-assign]
        yield client, fake


def test_inbox_requires_login(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = create_app(home=str(tmp_path / "home"))
    with TestClient(app) as client:
        assert client.get("/api/inbox").status_code == 401
        assert client.post("/api/inbox/seen", json={"keys": ["a"]}).status_code == 401


def test_inbox_lists_messages(box) -> None:  # type: ignore[no-untyped-def]
    client, _ = box
    body = client.get("/api/inbox").json()
    assert len(body["items"]) == 2
    assert body["items"][0]["prize"] is True
    assert body["items"][1]["prize"] is False
    # 时间要能直接给 JS 的 Date 用。
    assert body["items"][0]["at"].startswith("2026-11-05T10:00")
    assert body["system_count"] == 3


def test_inbox_says_what_it_cannot_do(box) -> None:  # type: ignore[no-untyped-def]
    """「已看只是本机记录」必须写在返回体里。

    前端迟早会把它当成"清空红点"来做，那句话就是最后一道提示。
    """
    client, _ = box
    note = client.get("/api/inbox").json()["note"]
    assert "本机" in note
    assert "红点" in note


def test_mark_seen_flips_unread(box) -> None:  # type: ignore[no-untyped-def]
    client, fake = box
    assert client.post("/api/inbox/seen", json={"keys": ["chat:1"]}).json()["marked"] == 1
    body = client.get("/api/inbox").json()
    by_key = {item["key"]: item for item in body["items"]}
    assert by_key["chat:1"]["unread"] is False
    # 标了已看也仍然标着"像中奖"，翻回去还能看见。
    assert by_key["chat:1"]["prize"] is True
    assert "chat:1" in fake.seen


def test_clear_seen_makes_everything_unread_again(box) -> None:  # type: ignore[no-untyped-def]
    client, _ = box
    client.post("/api/inbox/seen", json={"keys": ["chat:1", "reply:2"]})
    client.delete("/api/inbox/seen")
    body = client.get("/api/inbox").json()
    assert all(item["unread"] for item in body["items"])


def test_inbox_reads_the_gateway_once_within_the_ttl(box) -> None:  # type: ignore[no-untyped-def]
    """两分钟内反复进这一页，只读一次 B 站。

    读一次收件箱要打三四个接口，而消息本身不会在两分钟里变出一批新的。
    但「刷新」必须真的去读 —— 手动点了还看到旧数据才是问题。
    """
    client, fake = box
    client.get("/api/inbox")
    client.get("/api/inbox")
    assert fake.fetches == 1
    client.get("/api/inbox?refresh=1")
    assert fake.fetches == 2


def test_marking_seen_does_not_read_again(box) -> None:  # type: ignore[no-untyped-def]
    """标「已看」是本地动作，不该顺手再拉一遍消息列表。"""
    client, fake = box
    client.get("/api/inbox")
    client.post("/api/inbox/seen", json={"keys": ["chat:1"]})
    body = client.get("/api/inbox").json()
    assert fake.fetches == 1
    assert {item["key"]: item for item in body["items"]}["chat:1"]["unread"] is False


def test_clearing_seen_drops_the_cache(box) -> None:  # type: ignore[no-untyped-def]
    """重置已看必须重新读，否则界面还会显示上一份"未读"状态。"""
    client, fake = box
    client.get("/api/inbox")
    client.delete("/api/inbox/seen")
    client.get("/api/inbox")
    assert fake.fetches == 2
