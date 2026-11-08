"""收件箱。

两条规则最值钱，测试也都盯着它们：

1. **别拿营销话术当中奖。** 判宽了会被"恭喜你获得优惠券"骗，判严了会漏掉真中奖。
   强/弱两档关键词就是为了让理由能被看见 —— 人自己扫一眼就知道是哪种。
2. **一类读不到不能变成"没有新消息"。** 私信接口抽风时清单变空，比少几条危险得多。
"""

from __future__ import annotations

from datetime import UTC, datetime

from tobili.domain.inbox import (
    Message,
    annotate,
    digest,
    notify_line,
    parse_chat,
    parse_chat_body,
    prize_hint,
)
from tobili.usecases.inbox import Inbox


def _msg(text: str, *, kind: str = "chat", key: str = "k1", who: str = "UP") -> Message:
    return Message(kind=kind, key=key, who=who, text=text, unread=True)


# ---- 判定 ----


def test_strong_hit_is_a_prize() -> None:
    hit, why = prize_hint("恭喜你中奖了，请私信提供收货地址")
    assert hit is True
    assert "中奖" in why


def test_weak_hit_alone_is_not_enough() -> None:
    """抽奖区里"开奖""周边"满天飞，没有指向你的话就只是闲聊。

    真机验证过：不这么分的话"蹲一个周边"、"梦里都是开奖"都会被判成中奖。
    """
    assert prize_hint("本期抽奖结果已公布") == (False, "")
    assert prize_hint("蹲一个周边，心诚则灵冲冲冲") == (False, "")
    assert prize_hint("梦里都是开奖") == (False, "")


def test_weak_hit_plus_pointing_is_a_prize() -> None:
    hit, why = prize_hint("开奖时间！恭喜以下两位小伙伴获得激活码： @永夜")
    assert hit is True
    assert "指向你" in why


def test_plain_chat_is_not_a_prize() -> None:
    hit, word = prize_hint("在吗？帮我投个票")
    assert hit is False
    assert word == ""


def test_empty_text_is_not_a_prize() -> None:
    assert prize_hint("") == (False, "")


# ---- 私信正文解析 ----


def test_chat_body_is_unwrapped() -> None:
    assert parse_chat_body('{"content":"你好呀"}') == "你好呀"


def test_chat_body_plain_text_passes_through() -> None:
    assert parse_chat_body("就是一句话") == "就是一句话"


def test_broken_json_is_shown_as_is() -> None:
    """解析不出来也别吞掉 —— 显示一坨 JSON 也好过让人以为对方没说话。"""
    assert parse_chat_body('{"content":') == '{"content":'


def test_non_text_message_gets_a_readable_placeholder() -> None:
    assert "图片" in parse_chat_body('{"url":"https://x/y.jpg","height":100}')
    assert "撤回" in parse_chat_body('{"msg_status":1}')


# ---- 汇总 ----


def test_digest_counts_kinds() -> None:
    items = [_msg("a", kind="chat"), _msg("中奖了", kind="reply"), _msg("c", kind="at")]
    items = [items[0], items[1], items[2]]
    counts = digest(items)
    assert counts["total"] == 3
    assert counts["chat"] == 1
    assert counts["reply"] == 1
    assert counts["at"] == 1


def test_notify_line_is_empty_when_nothing_is_a_prize() -> None:
    """没有中奖消息就不该发通知。"有 12 条未读"只会让人关掉通知。"""
    assert notify_line([_msg("在吗"), _msg("帮我投票")]) == ""


def test_notify_line_lists_the_prize_ones() -> None:
    line = notify_line([_msg("恭喜中奖了", who="UP甲"), _msg("在吗", who="UP乙")])
    assert "UP甲" in line
    assert "UP乙" not in line


# ---- 用例 ----


class FakeRepo:
    def irrelevant(self, account_id: int) -> set[str]:
        return set()

    def __init__(self) -> None:
        self.rows: set[str] = set()

    def seen(self, account_id: int) -> set[str]:
        return set(self.rows)

    def mark(self, account_id: int, keys):  # type: ignore[no-untyped-def]
        before = len(self.rows)
        self.rows.update(keys)
        return len(self.rows) - before

    def forget(self, account_id: int) -> None:
        self.rows.clear()


class FakeGateway:
    def __init__(self, items: list[Message], *, system: int = 3, boom: bool = False) -> None:
        self.items = items
        self.system = system
        self.boom = boom
        self.unread_boom = False

    def inbox(self, *, pages: int = 1) -> list[Message]:
        if self.boom:
            raise RuntimeError("私信接口挂了")
        return list(self.items)

    def unread(self):  # type: ignore[no-untyped-def]
        if self.unread_boom:
            raise RuntimeError("未读读不到")

        class _Stat:
            def __init__(self, system: int) -> None:
                self.system = system
                # 计数默认跟清单对得上，否则每条测试都会先被"对账"那条提示拌住。
                self.chat = 0
                self.reply = 0
                self.at = 0

        return _Stat(self.system)


def test_fetch_marks_prize_messages() -> None:
    gateway = FakeGateway([_msg("恭喜你中奖了", key="chat:1")])
    view = Inbox(gateway=gateway, seen=FakeRepo()).fetch(1)  # type: ignore[arg-type]
    assert view.items[0].prize is True
    assert "中奖" in view.items[0].reason
    assert view.prize_line


def test_fetch_sorts_newest_first() -> None:
    old = Message("chat", "chat:1", "甲", "旧", datetime(2026, 1, 1, tzinfo=UTC))
    new = Message("chat", "chat:2", "乙", "新", datetime(2026, 5, 1, tzinfo=UTC))
    view = Inbox(gateway=FakeGateway([old, new]), seen=FakeRepo()).fetch(1)  # type: ignore[arg-type]
    assert [item.key for item in view.items] == ["chat:2", "chat:1"]


def test_seen_item_is_no_longer_unread() -> None:
    """本机看过就不再提醒。远端那条一直在，红点也一直在，只能在这里收敛。"""
    repo = FakeRepo()
    repo.mark(1, ["chat:1"])
    view = Inbox(gateway=FakeGateway([_msg("中奖了", key="chat:1")]), seen=repo).fetch(1)  # type: ignore[arg-type]
    assert view.items[0].unread is False
    # 但仍然标着"像中奖"，人翻回去还能看见。
    assert view.items[0].prize is True


def test_inbox_failure_is_reported_not_swallowed() -> None:
    """整个清单读不到时要留下原因，否则界面只能显示"没有新消息"。"""
    view = Inbox(gateway=FakeGateway([], boom=True), seen=FakeRepo()).fetch(1)  # type: ignore[arg-type]
    assert view.items == ()
    assert view.problems
    assert "消息列表读取失败" in view.problems[0]


def test_system_count_failure_does_not_kill_the_list() -> None:
    gateway = FakeGateway([_msg("在吗")])
    gateway.unread_boom = True
    view = Inbox(gateway=gateway, seen=FakeRepo()).fetch(1)  # type: ignore[arg-type]
    assert len(view.items) == 1
    assert any("系统通知" in problem for problem in view.problems)


def test_mark_seen_is_idempotent() -> None:
    repo = FakeRepo()
    inbox = Inbox(gateway=FakeGateway([]), seen=repo)  # type: ignore[arg-type]
    assert inbox.mark_seen(1, ["a", "b"]) == 2
    assert inbox.mark_seen(1, ["a", "b"]) == 0


def test_context_title_does_not_make_a_prize() -> None:
    """真机踩过的坑：自己的专栏标题里带"开奖"，被当成中奖通知刷满一屏。

    `context`（被回复的文章标题）只做显示，判定只看 `text`（对方原话）。
    """
    item = Message(
        kind="reply",
        key="reply:1",
        who="路人",
        text="心诚则灵，祝自己和UP主都顺顺利利！",
        context="示例作者｜精选24个抽奖活动 2026-11-05 开奖",
    )
    marked = annotate(item)
    assert marked.prize is False
    assert marked.reason == ""


def test_real_at_notice_is_caught() -> None:
    """真实的 @我 中奖通知长这样，必须能认出来。"""
    item = Message(
        kind="at",
        key="at:1",
        who="HAKII哈氪",
        text="恭喜@某人 一位氪官获得哈氪BOULDER领夹穿戴音箱，请在11.9前私信小哈领奖，逾期作废",
        context="【走近氪学】用可穿戴设计理念",
    )
    marked = annotate(item)
    assert marked.prize is True
    assert "恭喜获奖" in marked.reason or "领奖" in marked.reason or "中奖" in marked.reason


def test_count_mismatch_is_reported() -> None:
    """私信列表偶发整批返回空（限流），而计数写着十几条。

    这时必须点破，否则界面显示"没有新消息"，人真的会以为没有。
    """

    class _Stat:
        chat = 12
        system = 0
        reply = 0
        at = 0

    gateway = FakeGateway([_msg("在吗", kind="reply")])
    gateway.unread = lambda: _Stat()  # type: ignore[method-assign]
    view = Inbox(gateway=gateway, seen=FakeRepo()).fetch(1)  # type: ignore[arg-type]
    assert any("私信显示有 12 条未读" in problem for problem in view.problems)


def test_no_problem_when_counts_match() -> None:
    class _Stat:
        chat = 0
        system = 0
        reply = 0
        at = 0

    gateway = FakeGateway([_msg("在吗", kind="reply")])
    gateway.unread = lambda: _Stat()  # type: ignore[method-assign]
    view = Inbox(gateway=gateway, seen=FakeRepo()).fetch(1)  # type: ignore[arg-type]
    assert view.problems == ()


# ---- 系统卡片（官方账号发的通知）----
#
# 这一组是用户报的"消息里有一条全是乱码"。根因：系统卡片的 content 是
# {"title","text","modules"}，没有 content 键，早期只认 content 就把整串 JSON
# 当正文显示了。下面锁住这个形状。


def test_system_card_text_is_extracted() -> None:
    raw = (
        '{"title":"登录操作通知","text":"你的账号在新设备或平台登录成功",'
        '"jump_text":"查看详情","jump_uri":"https://x/a?mid=1&buvid="}'
    )
    text, subject = parse_chat(raw)

    assert text == "你的账号在新设备或平台登录成功"
    assert subject == "登录操作通知"
    # 整串 JSON 不能漏到正文里 —— 那正是"全是乱码"的样子。
    assert "jump_uri" not in text
    assert "{" not in text


def test_body_helper_drops_the_subject() -> None:
    # 只要正文的老接口保持可用；副标题走 parse_chat。
    assert parse_chat_body('{"title":"标题","text":"正文"}') == "正文"


def test_chat_with_content_still_wins_over_title() -> None:
    """真人对私信只有 content，不该被 title 抢走。"""
    raw = '{"title":"某标题","content":"在吗"}'
    text, subject = parse_chat(raw)
    assert text == "在吗"
    assert subject == "某标题"


def test_escaped_unicode_does_not_leak() -> None:
    """json 里的 \\u0026 必须变成 &，不能原样显示。"""
    raw = '{"title":"t","text":"a\\u0026b"}'
    text, _ = parse_chat(raw)
    assert text == "a&b"
