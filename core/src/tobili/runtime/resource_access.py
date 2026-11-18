"""Optional edition policy, separate from persisted account schedules."""

NOTE = "本机不是此账号的执行设备，请使用“同步云端”；原排期保留。"


def allowed(box, owner=None):
    policy = getattr(box, "resource_policy", None)
    return policy is None or policy.allowed(owner)


def require(box, owner=None):
    if not allowed(box, owner):
        from ..domain.errors import WriteUnavailable

        raise WriteUnavailable(NOTE)
