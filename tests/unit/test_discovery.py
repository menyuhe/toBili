"""找源：搜索结果排序。

排序只影响展示顺序，不影响任何自动行为 —— 但顺序错了，用户会先看到一堆无关的人，
然后觉得这个功能没用。
"""

from __future__ import annotations

from tobili.domain.discovery import SPECIALTY_WORDS, rank, score_of
from tobili.ports.models import UserCandidate


def test_name_hit_beats_sign_hit() -> None:
    in_name = UserCandidate(uid="1", name="转发抽奖娘", fans=100)
    in_sign = UserCandidate(uid="2", name="某人", sign="日常转发抽奖", fans=100)
    assert score_of(in_name, "抽奖").score > score_of(in_sign, "抽奖").score


def test_specialty_word_adds_weight() -> None:
    plain = UserCandidate(uid="1", name="小明", fans=100)
    special = UserCandidate(uid="2", name="锦鲤本鲤", fans=100)
    assert score_of(special, "抽奖").score > score_of(plain, "抽奖").score
    assert "锦鲤" in SPECIALTY_WORDS


def test_fans_tiers() -> None:
    high = UserCandidate(uid="1", name="A", fans=50_000)
    mid = UserCandidate(uid="2", name="B", fans=5_000)
    low = UserCandidate(uid="3", name="C", fans=10)
    assert score_of(high, "").score > score_of(mid, "").score
    assert score_of(mid, "").score > score_of(low, "").score


def test_no_videos_is_noted_not_penalised() -> None:
    """只是转发号也可能天天抽奖，所以只备注，不扣分。"""
    quiet = UserCandidate(uid="1", name="转发抽奖娘", fans=1000, videos=0)
    ranked = score_of(quiet, "抽奖")
    assert any("没有投稿" in reason for reason in ranked.reasons)


def test_rank_is_stable_and_bounded() -> None:
    users = tuple(UserCandidate(uid=str(i), name=f"抽奖号{i}", fans=1000) for i in range(1, 6))
    first = rank(users, "抽奖", limit=3)
    second = rank(tuple(reversed(users)), "抽奖", limit=3)
    assert len(first) == 3
    # 同分时按 UID 定序，所以输入顺序变了结果也不变。
    assert [r.uid for r in first] == [r.uid for r in second]


def test_rank_keeps_the_most_relevant() -> None:
    users = (
        UserCandidate(uid="10", name="美食探店", fans=900_000),
        UserCandidate(uid="11", name="转发抽奖娘", fans=1_200),
    )
    picked = rank(users, "抽奖", limit=1)
    assert picked[0].uid == "11"
