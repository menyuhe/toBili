"""订阅源管理：输入校验只该有一份，CLI 和 HTTP 共用。"""

from __future__ import annotations

import pytest

from tobili.adapters.sqlite import Repositories
from tobili.usecases.manage_feeds import FeedRejected, ManageFeeds


def test_add_normalizes_uid(repos: Repositories, account_id: int) -> None:
    manage = ManageFeeds(repos.feeds)
    feed_id = manage.add(account_id, kind="SPACE", target=" uid:12345 ")

    items = manage.list(account_id)
    assert [item.id for item in items] == [feed_id]
    assert items[0].kind == "space"
    assert items[0].target == "12345"
    assert items[0].label == "12345"
    assert items[0].enabled is True


def test_label_falls_back_to_uid(repos: Repositories, account_id: int) -> None:
    manage = ManageFeeds(repos.feeds)
    manage.add(account_id, kind="space", target="777", label="   ")
    assert manage.list(account_id)[0].label == "777"


def test_custom_label_is_kept(repos: Repositories, account_id: int) -> None:
    manage = ManageFeeds(repos.feeds)
    manage.add(account_id, kind="space", target="777", label="某个 UP")
    assert manage.list(account_id)[0].label == "某个 UP"


@pytest.mark.parametrize("kind", ["search", "bad-topic", "opus", "", "  "])
def test_unsupported_kind_is_rejected(repos: Repositories, account_id: int, kind: str) -> None:
    manage = ManageFeeds(repos.feeds)
    with pytest.raises(FeedRejected):
        manage.add(account_id, kind=kind, target="777")


def test_following_needs_no_target(repos: Repositories, account_id: int) -> None:
    manage = ManageFeeds(repos.feeds)
    feed_id = manage.add(account_id, kind="following", target="")
    item = manage.list(account_id)[0]

    assert item.id == feed_id
    assert item.kind == "following"
    assert item.target == ""
    assert item.label == "关注流"


def test_following_ignores_given_target(repos: Repositories, account_id: int) -> None:
    """关注流属于账号自己。target 留着不同的值会造出多份一模一样的源。"""
    manage = ManageFeeds(repos.feeds)
    manage.add(account_id, kind="following", target="123")
    manage.add(account_id, kind="following", target="456")

    items = manage.list(account_id)
    assert len(items) == 1
    assert items[0].target == ""


@pytest.mark.parametrize("target", ["abc", "", "12ab", "uid:", "-5", "1" * 21])
def test_invalid_uid_is_rejected(repos: Repositories, account_id: int, target: str) -> None:
    manage = ManageFeeds(repos.feeds)
    with pytest.raises(FeedRejected):
        manage.add(account_id, kind="space", target=target)


def test_remove_marks_disabled(repos: Repositories, account_id: int) -> None:
    manage = ManageFeeds(repos.feeds)
    feed_id = manage.add(account_id, kind="space", target="777")
    manage.remove(account_id, feed_id)

    item = manage.list(account_id)[0]
    assert item.enabled is False


def test_remove_foreign_feed_is_rejected(repos: Repositories, account_id: int) -> None:
    """不能靠"删不存在的 ID"来探测别人有没有这条订阅。"""
    manage = ManageFeeds(repos.feeds)
    with pytest.raises(FeedRejected):
        manage.remove(account_id, 99999)


def test_rejected_input_touches_nothing(repos: Repositories, account_id: int) -> None:
    manage = ManageFeeds(repos.feeds)
    with pytest.raises(FeedRejected):
        manage.add(account_id, kind="space", target="not-a-uid")
    assert manage.list(account_id) == []


def test_disable_then_enable_roundtrip(repos: Repositories, account_id: int) -> None:
    """停用要能撤回。只能停不能启的开关，等于让人不敢碰它。"""
    manage = ManageFeeds(repos.feeds)
    feed_id = manage.add(account_id, kind="space", target="777")

    manage.remove(account_id, feed_id)
    assert manage.list(account_id)[0].enabled is False

    manage.enable(account_id, feed_id)
    assert manage.list(account_id)[0].enabled is True


def test_delete_really_removes_the_row(repos: Repositories, account_id: int) -> None:
    """删除和停用是两件事：删除之后列表里就不该有它了。"""
    manage = ManageFeeds(repos.feeds)
    feed_id = manage.add(account_id, kind="space", target="777")

    manage.delete(account_id, feed_id)
    assert manage.list(account_id) == []


def test_delete_foreign_feed_is_rejected(repos: Repositories, account_id: int) -> None:
    manage = ManageFeeds(repos.feeds)
    with pytest.raises(FeedRejected):
        manage.delete(account_id, 99999)


def test_rename_keeps_a_readable_label(repos: Repositories, account_id: int) -> None:
    manage = ManageFeeds(repos.feeds)
    feed_id = manage.add(account_id, kind="space", target="777", label="旧名字")

    manage.rename(account_id, feed_id, "  新名字  ")
    assert manage.list(account_id)[0].label == "新名字"

    # 清空备注要退回默认名，否则列表上会出现一行没名字的源。
    manage.rename(account_id, feed_id, "   ")
    assert manage.list(account_id)[0].label == "777"


def test_rename_following_falls_back_to_kind_name(repos: Repositories, account_id: int) -> None:
    manage = ManageFeeds(repos.feeds)
    feed_id = manage.add(account_id, kind="following", target="")
    manage.rename(account_id, feed_id, "")
    assert manage.list(account_id)[0].label == "关注流"


def test_rename_rejects_overlong_label(repos: Repositories, account_id: int) -> None:
    manage = ManageFeeds(repos.feeds)
    feed_id = manage.add(account_id, kind="space", target="777")
    with pytest.raises(FeedRejected):
        manage.rename(account_id, feed_id, "长" * 41)
