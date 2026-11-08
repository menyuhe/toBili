"""三态探测。

这个文件是整个项目里最需要小心的地方。每一次返回 `ABSENT` 都意味着下游会真的
发出一个写请求；所以只有三种情况允许返回 `ABSENT`：

- 关系接口明确说 `attribute == 0`
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
from .parser import epoch_of, like_status, orig_id_of, reply_mid_list
from .reader import DynamicReader

_MAX_FEED_PAGES = 3
_FEED_PAGE_SIZE = 20


class BiliProber:
    def __init__(self, transport: Wire, reader: DynamicReader) -> None:
        self._transport = transport
        self._reader = reader
        self._uid = ""

    def whoami(self) -> str:
        if not self._uid:
            self._uid = self._reader.profile().uid
        return self._uid

    def did(self, action: ActionKind, *, dyn_id: str, payload: Mapping[str, str]) -> ProbeState:
        try:
            return self._route(action, dyn_id=dyn_id, payload=payload)
        except MissingCredential:
            raise
        except RemoteError as exc:
            if exc.fault is Fault.AUTH:
                raise
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
        if action is ActionKind.UNFOLLOW:
            return _invert(self._following(payload.get("up_uid", "")))
        if action is ActionKind.DELETE_DYN:
            return self._deleted(dyn_id)
        # 收藏需要收藏夹列表才能判断，M1 不做猜测。
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
        if codes.code_of(payload) != codes.OK:
            return ProbeState.UNKNOWN
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return ProbeState.UNKNOWN
        attribute = data.get("attribute")
        if not isinstance(attribute, int):
            return ProbeState.UNKNOWN
        return ProbeState.PRESENT if attribute != 0 else ProbeState.ABSENT

    # ---- 点赞 ----
    def _liked(self, dyn_id: str) -> ProbeState:
        item = self._reader.raw_detail(dyn_id)
        status = like_status(item)
        if status is None:
            return ProbeState.UNKNOWN
        return ProbeState.PRESENT if status else ProbeState.ABSENT

    # ---- 转发 ----
    def _shared(self, dyn_id: str, since_epoch: float | None) -> ProbeState:
        """翻自己的动态流。

        `since_epoch` 是活动的发布时间。一旦翻到更早的动态都没找到，说明这段时间
        的转发已经全部看过了 —— 此时才敢说 `ABSENT`。没有这个边界就只能 `UNKNOWN`。
        """
        uid = self.whoami()
        if not uid:
            return ProbeState.UNKNOWN

        offset = ""
        for _ in range(_MAX_FEED_PAGES):
            payload = self._transport.fetch(
                endpoints.SPACE_FEED,
                query={
                    "host_mid": uid,
                    "offset": offset,
                    "type": "all",
                    "platform": "web",
                },
                referer=endpoints.SPACE_REFERER.format(uid=uid),
            )
            if codes.code_of(payload) != codes.OK:
                return ProbeState.UNKNOWN

            data = payload.get("data") or {}
            items = data.get("items") or []
            if not items:
                return ProbeState.UNKNOWN

            for raw in items:
                if not isinstance(raw, Mapping):
                    continue
                if orig_id_of(raw) == dyn_id:
                    return ProbeState.PRESENT
                published = _pub_epoch(raw)
                if since_epoch and published and published < since_epoch:
                    return ProbeState.ABSENT

            offset = str(data.get("offset") or "")
            if not offset:
                break
        return ProbeState.UNKNOWN

    # ---- 评论 ----
    def _commented(self, dyn_id: str, payload: Mapping[str, str]) -> ProbeState:
        uid = self.whoami()
        if not uid:
            return ProbeState.UNKNOWN
        answer = self._transport.fetch(
            endpoints.LIST_REPLY,
            query={
                "oid": payload.get("rid") or dyn_id,
                "type": int(payload.get("comment_type") or 17),
                "mode": 3,
                "next": 0,
                "ps": _FEED_PAGE_SIZE,
            },
            referer=endpoints.dyn_referer(dyn_id),
        )
        if codes.code_of(answer) != codes.OK:
            return ProbeState.UNKNOWN

        mids = reply_mid_list(answer)
        if uid in mids:
            return ProbeState.PRESENT
        total = _reply_total(answer)
        # 总数不超过一页，说明这一页就是全部；没找到就是真的没评论过。
        if total is not None and total <= len(mids):
            return ProbeState.ABSENT
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
    page = data.get("page")
    if not isinstance(page, Mapping):
        return None
    count = page.get("count") or page.get("acount")
    return int(count) if isinstance(count, (int, float)) else None


def _reserve_state(item: Mapping[str, Any]) -> bool | None:
    """预约按钮是否已点亮。字段不稳定，取不到就返回 None。"""
    modules = item.get("modules")
    if not isinstance(modules, Mapping):
        return None
    dynamic = modules.get("module_dynamic")
    if not isinstance(dynamic, Mapping):
        return None
    major = dynamic.get("major")
    if not isinstance(major, Mapping):
        return None
    reserve = major.get("reserve")
    if not isinstance(reserve, Mapping):
        return None
    status = reserve.get("button_status") or reserve.get("state")
    if isinstance(status, bool):
        return status
    if isinstance(status, int):
        return status != 0
    return None
