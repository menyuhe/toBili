"""开奖判定。

这里每条测试对应一条"不能判错"的规则。尤其是：**找不到公告绝不等于没中奖** ——
判错的代价是删转发、取关，不可恢复。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tobili.domain.result import Announcement, decide, mentions
from tobili.domain.tidy import Outcome

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
UID = "1001"
NICK = "玖仟"


def _announce(text: str, *, days_ago: float = 2, media: int = 0) -> Announcement:
    return Announcement(
        dyn_id="d9",
        text=text,
        published_at=NOW - timedelta(days=days_ago),
        media=media,
    )


def test_no_announcement_never_becomes_lost() -> None:
    decision = decide([], uid=UID, nickname=NICK, now=NOW)
    assert decision.outcome is Outcome.PENDING


def test_ordinary_post_is_not_treated_as_announcement() -> None:
    decision = decide([_announce("今天直播八点开始")], uid=UID, nickname=NICK, now=NOW)
    assert decision.outcome is Outcome.PENDING


def test_announcement_mentioning_me_wins() -> None:
    decision = decide([_announce("开奖名单：@玖仟 @别人")], uid=UID, nickname=NICK, now=NOW)
    assert decision.outcome is Outcome.WON
    assert "d9" in decision.evidence


def test_image_only_announcement_keeps_waiting() -> None:
    """名单只在图片里，文本是空的 —— 核对不了，不能当作没中奖。"""
    decision = decide([_announce("", media=3)], uid=UID, nickname=NICK, now=NOW)
    assert decision.outcome is Outcome.PENDING


def test_fresh_announcement_keeps_waiting() -> None:
    """开奖公告可能分批发，刚发出来的不能立刻判死。"""
    decision = decide(
        [_announce("开奖结果已公布，恭喜以下小伙伴", days_ago=0.1)],
        uid=UID,
        nickname=NICK,
        now=NOW,
    )
    assert decision.outcome is Outcome.PENDING


def test_settled_announcement_without_me_is_lost() -> None:
    decision = decide(
        [_announce("开奖结果已公布，恭喜以下小伙伴", days_ago=3)],
        uid=UID,
        nickname=NICK,
        now=NOW,
    )
    assert decision.outcome is Outcome.LOST


def test_announcement_without_publish_time_keeps_waiting() -> None:
    item = Announcement(dyn_id="d9", text="开奖结果已公布", published_at=None)
    decision = decide([item], uid=UID, nickname=NICK, now=NOW)
    assert decision.outcome is Outcome.PENDING


def test_short_nickname_is_not_trusted() -> None:
    """单字昵称出现在任何句子里，用它做包含判断必然误判中奖。"""
    assert mentions("这是小明中的奖", uid="", nickname="小") is False
    assert mentions("这是小明的奖", uid="", nickname="小明") is True


def test_uid_only_matches_bare_mention() -> None:
    assert mentions("恭喜 @1001 中奖", uid=UID, nickname="") is True
    assert mentions("订单号 10010 已发货", uid=UID, nickname="") is False


def test_any_hit_wins_even_if_newest_is_silent() -> None:
    """旧公告提到过就算中奖，不该被后面一条无所谓的公告盖掉。"""
    old = Announcement(
        dyn_id="d1",
        text="中奖名单：@玖仟",
        published_at=NOW - timedelta(days=10),
    )
    new = _announce("开奖结果已公布，感谢参与", days_ago=1)
    decision = decide([new, old], uid=UID, nickname=NICK, now=NOW)
    assert decision.outcome is Outcome.WON
    assert "d1" in decision.evidence
