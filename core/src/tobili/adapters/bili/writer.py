"""写路径。

一个入口 `perform(action)`，六种动作在内部派发。三条规矩写死在 `_execute` 里：

1. 没有 CSRF 就不发 —— 发了也必然被拒，只会白白增加一次可疑请求。
2. 传输异常 → `unclear`，不是失败。调用方据此记 `UNKNOWN` 而不是重试。
3. 远端明确说"已经做过" → 当作成功。这让重复调用变得无害。
"""

from __future__ import annotations

import json

from ...domain.action import Action, ActionKind
from ...domain.errors import AmbiguousWrite, Fault, RemoteError
from ...domain.receipt import Receipt
from . import codes, endpoints
from .http import Wire


class BiliWriter:
    def __init__(self, transport: Wire) -> None:
        self._transport = transport

    def perform(self, action: Action) -> Receipt:
        if not self._transport.csrf:
            return Receipt.rejected(Fault.AUTH, "缺少 bili_jct，无法执行写操作")
        try:
            payload = self._dispatch(action)
        except AmbiguousWrite as exc:
            return Receipt.unclear(str(exc), fault=Fault.TIMEOUT)
        except RemoteError as exc:
            return Receipt.rejected(exc.fault, str(exc))

        code = codes.code_of(payload)
        message = str(payload.get("message") or payload.get("msg") or "")
        if code == codes.OK:
            return Receipt.success(remote_id=_remote_id(payload), message=message or "已提交")
        if code in codes.ALREADY_DONE_CODES:
            return Receipt.success(message=message or "此前已完成")
        return Receipt.rejected(codes.fault_of(code), message or f"远端拒绝（{code}）")

    # ---- 派发 ----
    def _dispatch(self, action: Action) -> dict:
        handler = {
            ActionKind.FOLLOW: self._follow,
            ActionKind.SHARE: self._share,
            ActionKind.COMMENT: self._comment,
            ActionKind.LIKE: self._like,
            ActionKind.FAVORITE: self._favorite,
            ActionKind.RESERVE: self._reserve,
            ActionKind.UNFOLLOW: self._unfollow,
            ActionKind.DELETE_DYN: self._delete_dyn,
        }.get(action.kind)
        if handler is None:
            raise RemoteError(Fault.PARSE, f"不支持的动作：{action.kind}")
        return handler(action)

    def _unfollow(self, action: Action) -> dict:
        """取关。和关注同一个接口，只是 `act` 从 1 变成 2。

        这个接口**没有幂等保证**：对没关注的人取关会报错，对已关注的取两次也是
        报错。所以调用前必须先探测关系状态，这里不做任何"再试一次"的兜底。
        """
        up_uid = action.payload.get("up_uid") or action.payload.get("fid")
        if not up_uid:
            raise RemoteError(Fault.PARSE, "取关动作缺少目标 UID")
        return self._transport.submit(
            endpoints.RELATION_EDIT,
            {
                "fid": up_uid,
                "act": 2,
                "re_src": 11,
                "csrf": self._transport.csrf,
            },
            referer=f"https://space.bilibili.com/{up_uid}",
        )

    def _delete_dyn(self, action: Action) -> dict:
        """删除我自己发的一条动态（清理转发用）。

        这里传的必须是**我自己的**动态 ID —— 转发产生的是新动态，它的 ID 和原活动
        的 ID 不是一个。删错 ID 的后果不可恢复，所以 ID 由调用方在本地核对过归属
        才传进来，这个函数不做二次猜测。
        """
        if not action.dyn_id:
            raise RemoteError(Fault.PARSE, "删除动作缺少动态 ID")
        return self._transport.submit(
            endpoints.REMOVE_DYN,
            {"dynamic_id": action.dyn_id, "csrf": self._transport.csrf},
            referer=endpoints.FOLLOWING_REFERER,
        )

    def _follow(self, action: Action) -> dict:
        up_uid = action.payload.get("up_uid") or action.payload.get("fid")
        if not up_uid:
            raise RemoteError(Fault.PARSE, "关注动作缺少目标 UID")
        return self._transport.submit(
            endpoints.RELATION_EDIT,
            {
                "fid": up_uid,
                "act": 1,
                "re_src": 11,
                "csrf": self._transport.csrf,
            },
            referer=f"https://space.bilibili.com/{up_uid}",
        )

    def _share(self, action: Action) -> dict:
        text = action.payload.get("text", "").strip()
        if not text:
            raise RemoteError(Fault.PARSE, "转发动作缺少正文")
        body = {
            "dyn_req": {
                "content": {"contents": [{"raw_text": text, "type": 1, "biz_id": ""}]},
                "scene": 1,
                "attach_card": None,
            },
            "dyn_id_str": action.dyn_id,
        }
        return self._transport.submit_json(
            endpoints.PUBLISH_DYN,
            body,
            query={"platform": "web", "csrf": self._transport.csrf},
            referer=endpoints.dyn_referer(action.dyn_id),
        )

    def _comment(self, action: Action) -> dict:
        text = action.payload.get("text", "").strip()
        if not text:
            raise RemoteError(Fault.PARSE, "评论动作缺少正文")
        return self._transport.submit(
            endpoints.ADD_REPLY,
            {
                "oid": action.payload.get("rid") or action.dyn_id,
                "type": int(action.payload.get("comment_type") or 17),
                "message": text,
                "plat": 1,
                "csrf": self._transport.csrf,
            },
            referer=endpoints.dyn_referer(action.dyn_id),
        )

    def _like(self, action: Action) -> dict:
        return self._transport.submit(
            endpoints.THUMB_DYN,
            {"dyn_id_str": action.dyn_id, "up": 1, "csrf": self._transport.csrf},
            referer=endpoints.dyn_referer(action.dyn_id),
        )

    def _favorite(self, action: Action) -> dict:
        folder = action.payload.get("folder_id")
        if not folder:
            raise RemoteError(Fault.PARSE, "收藏动作缺少收藏夹 ID")
        return self._transport.submit(
            endpoints.FAV_DEAL,
            {
                "rid": action.payload.get("rid") or action.dyn_id,
                "type": 2,
                "add_media_ids": folder,
                "csrf": self._transport.csrf,
            },
            referer=endpoints.dyn_referer(action.dyn_id),
        )

    def _reserve(self, action: Action) -> dict:
        reserve_id = action.payload.get("reserve_id")
        if not reserve_id:
            raise RemoteError(Fault.PARSE, "预约动作缺少预约 ID")
        return self._transport.submit(
            endpoints.RESERVE_CLICK,
            {
                "reserve_id": reserve_id,
                "cur_btn_status": int(action.payload.get("button_status") or 1),
                "dynamic_id_str": action.dyn_id,
                "reserve_total": int(action.payload.get("reserve_total") or 0),
                "spmid": "333.1369.0.0",
                "csrf": self._transport.csrf,
            },
            referer=endpoints.dyn_referer(action.dyn_id),
        )


def _remote_id(payload: dict) -> str:
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("dyn_id_str", "dyn_id", "rpid_str", "rpid"):
            value = data.get(key)
            if value:
                return str(value)
    return ""


def describe(action: Action) -> str:
    """给日志用的短描述。不带任何凭据。"""
    extra = json.dumps(dict(action.payload), ensure_ascii=False)[:80]
    return f"{action.kind.value} on {action.dyn_id} {extra}".strip()
