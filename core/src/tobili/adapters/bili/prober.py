"""三态探测。

这个文件是整个项目里最需要小心的地方。每一次返回 `ABSENT` 都意味着下游会真的
发出一个写请求；所以只有三种情况允许返回 `ABSENT`：

- 关系接口明确说 `attribute` 为 0 或 4（没有关注对方）
- 动态详情明确说 `liked == false`
- 翻自己的动态流时，**已经翻到了比活动更早的动态**，仍然没看到转发
- 评论总数不超过一页，且这一页里没有我

其余一律 `UNKNOWN`。宁可少做一次，也不重复做一次。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...domain.action import ActionKind
from ...domain.errors import Fault, MissingCredential, RemoteError
from ...domain.probe import ProbeState
from . import codes, endpoints
from .http import Wire
from .parser import epoch_of, like_status, reply_mid_list
from .reader import DynamicReader, _next_cursor

_MAX_FEED_PAGES = 3
_FEED_PAGE_SIZE = 20


class BiliProber:
    def __init__(self, transport: Wire, reader: DynamicReader) -> None:
        self._transport = transport
        self._reader = reader
        self._uid = ""
        self.last_reason = ""
        from .history_probe import ShareHistory

        self._shares = ShareHistory()
        self._comments = {}

    def whoami(self) -> str:
        if not self._uid:
            self._uid = self._reader.profile().uid
        return self._uid

    def did(self, action: ActionKind, *, dyn_id: str, payload: Mapping[str, str]) -> ProbeState:
        self.last_reason = ""
        try:
            result = self._route(action, dyn_id=dyn_id, payload=payload)
            if result is ProbeState.UNKNOWN and not self.last_reason:
                self.last_reason = "平台未返回完整的参与状态，请在原动态核对"
            return result
        except MissingCredential:
            raise
        except RemoteError as exc:
            if exc.fault in (
                Fault.AUTH,
                Fault.RISK,
                Fault.RATE_LIMIT,
                Fault.NETWORK,
                Fault.TIMEOUT,
            ):
                raise
            self.last_reason = f"查询参与状态失败（{exc.fault.value}），未重复发送"
            return ProbeState.UNKNOWN

    def _route(self, action: ActionKind, *, dyn_id: str, payload: Mapping[str, str]) -> ProbeState:
        if action is ActionKind.FOLLOW:
            return self._following(payload.get("up_uid", ""))
        if action is ActionKind.LIKE:
            return self._liked(dyn_id)
        if action is ActionKind.SHARE:
            return self._shared(dyn_id, _as_epoch(payload.get("published_at")))
        if action is ActionKind.COMMENT:
            return self._commented(dyn_id, payload)
        if action is ActionKind.RESERVE:
            return self._reserved(dyn_id)
        if action is ActionKind.FAVORITE:
            from .interaction_state import favorite_state

            state = favorite_state(self._transport, dyn_id)
            return (
                ProbeState.UNKNOWN
                if state is None
                else ProbeState.PRESENT
                if state
                else ProbeState.ABSENT
            )
        if action is ActionKind.UNFOLLOW:
            return _invert(self._following(payload.get("up_uid", "")))
        if action is ActionKind.DELETE_DYN:
            return self._deleted(dyn_id)
        # 未实现的动作保持未知。
        return ProbeState.UNKNOWN

    # ---- 关注 ----
    def _following(self, up_uid: str) -> ProbeState:
        if not up_uid:
            return ProbeState.UNKNOWN
        payload = self._transport.fetch(
            endpoints.RELATION,
            query={"fid": up_uid},
            referer=f"https://space.bilibili.com/{up_uid}",
        )
        codes.ensure_ok(payload, what="读取关注关系")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return ProbeState.UNKNOWN
        attribute = data.get("attribute")
        if not isinstance(attribute, int):
            return ProbeState.UNKNOWN
        if isinstance(attribute, bool):
            return ProbeState.UNKNOWN
        if attribute in (2, 6):
            return ProbeState.PRESENT
        if attribute in (0, 4):
            return ProbeState.ABSENT
        self.last_reason = "关注关系不可用（可能已拉黑），请在 B 站核对"
        return ProbeState.UNKNOWN

    # ---- 点赞 ----
    def _liked(self, dyn_id: str) -> ProbeState:
        item = self._reader.raw_detail(dyn_id)
        status = like_status(item)
        if status is None:
            return ProbeState.UNKNOWN
        return ProbeState.PRESENT if status else ProbeState.ABSENT

    # ---- 转发 ----
    def _shared(self, dyn_id: str, since_epoch: float | None) -> ProbeState:
        uid = self.whoami()
        if not uid:
            return ProbeState.UNKNOWN
        state, self.last_reason = self._shares.check(self._transport, uid, dyn_id, since_epoch)
        return state

    # ---- 评论 ----
    def _commented(self, dyn_id: str, payload: Mapping[str, str]) -> ProbeState:
        uid = self.whoami()
        if not uid:
            return ProbeState.UNKNOWN
        rid = payload.get("rid") or dyn_id
        comment_type = int(payload.get("comment_type") or 17)
        key = (rid, comment_type)
        cursor, visited, rows, fresh_rows = 0, set(), {}, {}
        signature = None
        for page in range(3):
            if cursor in visited:
                break
            visited.add(cursor)
            answer = self._reader._reply_page(
                rid=rid,
                comment_type=comment_type,
                cursor=cursor,
                page_size=_FEED_PAGE_SIZE,
                referer=endpoints.dyn_referer(dyn_id),
                resign=True,
                strict=True,
                mode=2,
            )
            if answer is None:
                self.last_reason = "签名评论查询未成功，无法确认本人评论，请稍后核对"
                return ProbeState.UNKNOWN
            mids = reply_mid_list(answer)
            if uid in mids:
                return ProbeState.PRESENT
            total = _reply_total(answer)
            if page == 0 and total is not None and total <= len(mids) and all(mids):
                return ProbeState.ABSENT
            data = answer.get("data") or {}
            replies = data.get("replies") or []
            current = {
                str(r.get("rpid") or r.get("rpid_str")): str(r.get("mid") or "")
                for r in replies
                if isinstance(r, Mapping) and (r.get("rpid") or r.get("rpid_str"))
            }
            page_cursor = data.get("cursor") or {}
            if page == 0:
                signature = (total, tuple(current.items()))
                cached = self._comments.get(key)
                if cached and cached["signature"] == signature:
                    rows.update(cached["rows"])
                    cursor = cached["cursor"]
                else:
                    cursor = _next_cursor(page_cursor)
            else:
                cursor = _next_cursor(page_cursor)
            rows.update(current)
            fresh_rows.update(current)
            # Cached hot-comment pages can be reordered by the platform. They help
            # find an existing comment, never prove absence across separate calls.
            if total is not None and total <= len(fresh_rows) and all(fresh_rows.values()):
                return ProbeState.ABSENT
            if cursor is None or isinstance(cursor, bool) or cursor in visited:
                break
            if len(self._comments) >= 200 and key not in self._comments:
                self._comments.pop(next(iter(self._comments)))
            self._comments[key] = dict(signature=signature, rows=rows.copy(), cursor=cursor)
            if len(rows) >= 10000:
                self._comments.pop(key, None)
                break
        self.last_reason = (
            f"评论历史核查中：已查 {max(len(rows), len(mids))} 条，评论区共 {total} 条，无法确认本人是否评论过"
            if total is not None
            else "评论接口未提供完整数量，无法确认本人是否评论过"
        )
        return ProbeState.UNKNOWN

    # ---- 预约 ----
    def _reserved(self, dyn_id: str) -> ProbeState:
        item = self._reader.raw_detail(dyn_id)
        state = _reserve_state(item)
        if state is None:
            return ProbeState.UNKNOWN
        return ProbeState.PRESENT if state else ProbeState.ABSENT

    # ---- 删除动态 ----
    def _deleted(self, dyn_id: str) -> ProbeState:
        """这条动态还在不在。

        `ABSENT` 表示"还在，可以删"，`PRESENT` 表示"已经没了"。语义和别的动作
        相反，是因为这里问的不是"我做过没有"，而是"目标还在不在"。

        只有拿到明确的"不存在"码才敢说 `PRESENT`。风控、网络、结构变化一律
        `UNKNOWN` —— 把"查不到"当成"已经删了"，会让人误以为清理完成了。
        """
        payload = self._transport.fetch(
            endpoints.DYN_DETAIL,
            query={"id": dyn_id},
            referer=endpoints.dyn_referer(dyn_id),
        )
        code = codes.code_of(payload)
        if code == codes.OK:
            item = (payload.get("data") or {}).get("item")
            return ProbeState.ABSENT if isinstance(item, Mapping) else ProbeState.UNKNOWN
        if codes.is_gone(code):
            return ProbeState.PRESENT
        return ProbeState.UNKNOWN


def _invert(state: ProbeState) -> ProbeState:
    """把"是否关注"翻译成"是否已取关"。UNKNOWN 保持 UNKNOWN。"""
    if state is ProbeState.PRESENT:
        return ProbeState.ABSENT
    if state is ProbeState.ABSENT:
        return ProbeState.PRESENT
    return ProbeState.UNKNOWN


def _as_epoch(text: str | None) -> float | None:
    return epoch_of(text)


def _pub_epoch(item: Mapping[str, Any]) -> float | None:
    modules = item.get("modules")
    if not isinstance(modules, Mapping):
        return None
    author = modules.get("module_author")
    if not isinstance(author, Mapping):
        return None
    # 这里同样要认字符串时间戳，否则"翻到更早的动态"这条边界永不成立。
    return epoch_of(author.get("pub_ts"))


def _reply_total(payload: Mapping[str, Any]) -> int | None:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return None
    page = data.get("page") or data.get("cursor")
    if not isinstance(page, Mapping):
        return None
    count = next(
        (page[k] for k in ("count", "acount", "all_count") if page.get(k) is not None), None
    )
    return (
        int(count)
        if isinstance(count, (int, float)) and not isinstance(count, bool) and count >= 0
        else None
    )


def _reserve_state(item: Mapping[str, Any]) -> bool | None:
    """预约按钮是否已点亮。字段不稳定，取不到就返回 None。"""
    modules = item.get("modules")
    if not isinstance(modules, Mapping):
        return None
    dynamic = modules.get("module_dynamic")
    if not isinstance(dynamic, Mapping):
        return None
    for holder in (dynamic.get("additional"), dynamic.get("major"), dynamic):
        if not isinstance(holder, Mapping):
            continue
        reserve = holder.get("reserve")
        if not isinstance(reserve, Mapping):
            continue
        button = reserve.get("button")
        if isinstance(button, Mapping):
            status = button.get("status")
            if status in (1, 2) and not isinstance(status, bool):
                return status == 2
        status = reserve.get("button_status")
        if isinstance(status, bool):
            return status
        if status in (1, 2):
            return status == 2
    # reserve.state describes the reservation/event, not whether this account joined.
    return None
