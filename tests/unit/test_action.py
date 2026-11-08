"""动作指纹：幂等的哈希基础。"""

from __future__ import annotations

from tobili.domain.action import Action, ActionKind, digest


def test_digest_is_stable_across_key_order() -> None:
    left = digest(ActionKind.COMMENT, "dyn-1", {"text": "恭喜", "rid": "9"})
    right = digest(ActionKind.COMMENT, "dyn-1", {"rid": "9", "text": "恭喜"})
    assert left == right


def test_digest_changes_with_content() -> None:
    base = digest(ActionKind.COMMENT, "dyn-1", {"text": "恭喜"})
    other = digest(ActionKind.COMMENT, "dyn-1", {"text": "恭喜恭喜"})
    assert base != other


def test_digest_changes_with_target() -> None:
    assert digest(ActionKind.SHARE, "dyn-1", {}) != digest(ActionKind.SHARE, "dyn-2", {})


def test_digest_changes_with_action() -> None:
    assert digest(ActionKind.SHARE, "dyn-1", {}) != digest(ActionKind.LIKE, "dyn-1", {})


def test_action_fingerprint_matches_digest() -> None:
    action = Action(ActionKind.FOLLOW, "dyn-1", {"up_uid": "2002"})
    assert action.fingerprint == digest(ActionKind.FOLLOW, "dyn-1", {"up_uid": "2002"})


def test_undo_kind_marks_irreversible_actions() -> None:
    assert ActionKind.SHARE.undo_kind is None
    assert ActionKind.COMMENT.undo_kind is None
    assert ActionKind.LIKE.undo_kind is ActionKind.LIKE
