"""探测结果。

**只有三种状态，而且 `UNKNOWN` 绝不等于 `ABSENT`。**

这是整个项目最重要的一条类型约束。历史上绝大多数"重复转发"，都源于把一次
网络失败或风控拦截当成了"我还没做过"。在这里，那种情况是 `UNKNOWN`，
而 `may_write()` 对它返回 `False`。
"""

from __future__ import annotations

from enum import StrEnum


class ProbeState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


def may_write(state: ProbeState) -> bool:
    """只有确证"没做过"才允许写。"""
    return state is ProbeState.ABSENT


def merge(*states: ProbeState) -> ProbeState:
    """合并多个证据：任一确证存在即存在；否则任一存疑即存疑；全是否定才否定。

    顺序不能反。先看 PRESENT 是因为"已经做过"是安全结论；再退到 UNKNOWN 是
    因为"说不准"也是安全结论；最后才是 ABSENT —— 那是唯一会触发写操作的状态。
    """
    if not states:
        return ProbeState.UNKNOWN
    if any(s is ProbeState.PRESENT for s in states):
        return ProbeState.PRESENT
    if any(s is ProbeState.UNKNOWN for s in states):
        return ProbeState.UNKNOWN
    return ProbeState.ABSENT


def describe(state: ProbeState) -> str:
    return {
        ProbeState.PRESENT: "已做过，跳过",
        ProbeState.ABSENT: "确认未做过，可以执行",
        ProbeState.UNKNOWN: "无法确认，为避免重复已跳过",
    }[state]
