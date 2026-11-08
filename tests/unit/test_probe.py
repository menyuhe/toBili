"""三态语义。这几条断言是整个项目的行为底线。"""

from __future__ import annotations

from tobili.domain.probe import ProbeState, may_write, merge


def test_only_absent_allows_write() -> None:
    assert may_write(ProbeState.ABSENT) is True
    assert may_write(ProbeState.PRESENT) is False
    assert may_write(ProbeState.UNKNOWN) is False


def test_unknown_is_not_absent() -> None:
    """这条如果反过来，一次网络抖动就会变成一条重复转发。"""
    assert ProbeState.UNKNOWN is not ProbeState.ABSENT


def test_merge_prefers_present() -> None:
    assert merge(ProbeState.UNKNOWN, ProbeState.PRESENT) is ProbeState.PRESENT
    assert merge(ProbeState.ABSENT, ProbeState.PRESENT) is ProbeState.PRESENT


def test_merge_falls_back_to_unknown() -> None:
    assert merge(ProbeState.ABSENT, ProbeState.UNKNOWN) is ProbeState.UNKNOWN


def test_merge_all_absent() -> None:
    assert merge(ProbeState.ABSENT, ProbeState.ABSENT) is ProbeState.ABSENT


def test_merge_without_evidence_is_unknown() -> None:
    assert merge() is ProbeState.UNKNOWN


# ---- 已删的错误码 ----
def test_deleted_dynamic_answers_are_recognised() -> None:
    """删除成功后详情接口回的是 4101152，不是 4101105。

    2026-11-05 真机删掉一条转发后实测：4101152「动态不可见」。如果只认 4101105，
    删完之后复查会一直返回 UNKNOWN —— 不会重复删，但也永远不会承认删成功了。
    """
    from tobili.adapters.bili.codes import is_gone

    assert is_gone(4101152) is True
    assert is_gone(4101105) is True
    assert is_gone(4126143) is True


def test_unknown_code_is_not_treated_as_deleted() -> None:
    """风控码不能被当成"已经删了"，否则清理完成得莫名其妙。"""
    from tobili.adapters.bili.codes import is_gone

    for code in (-352, -412, -509, -101, 0, 4101153):
        assert is_gone(code) is False
