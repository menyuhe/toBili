"""写路径。

一个入口 `perform(action)`，六种动作在内部派发。三条规矩写死在 `_execute` 里：

1. 没有 CSRF 就不发 —— 发了也必然被拒，只会白白增加一次可疑请求。
2. 传输异常 → `unclear`，不是失败。调用方据此记 `UNKNOWN` 而不是重试。
3. 远端明确说"已经做过" → 当作成功。这让重复调用变得无害。
"""

from __future__ import annotations

import json
import time

from ...domain.action import Action, ActionKind
from ...domain.errors import AmbiguousWrite, Fault, RemoteError
from ...domain.receipt import Receipt
from . import codes, endpoints
from .http import Wire


class BiliWriter:
    def __init__(self, transport: Wire, *, wait=time.sleep) -> None:
        self._transport = transport
        self._wait = wait

    def perform(self, action: Action) -> Receipt:
        if not self._transport.csrf:
            return Receipt.rejected(Fault.AUTH, "缺少 bili_jct，无法执行写操作")
        try:
            payload = self._dispatch(action)
        except AmbiguousWrite as exc:
            return Receipt.unclear(str(exc), fault=exc.fault)
        except RemoteError as exc:
            return Receipt.rejected(exc.fault, str(exc))

        code = codes.code_of(payload)
        message = str(payload.get("message") or payload.get("msg") or "")
        verify_state = action.kind is ActionKind.FOLLOW or (action.kind is ActionKind.FAVORITE and action.payload.get('target') == 'opus')
        if verify_state and (code == codes.OK or code in codes.ALREADY_DONE_CODES or (action.kind is ActionKind.FAVORITE and code == 75008)):
            return self._verify_interaction(action)
        if code == codes.OK:
            if action.kind is ActionKind.SHARE:
                return self._verify_share(action, _remote_id(payload))
            if action.kind is ActionKind.RESERVE:
                data = payload.get("data")
                if isinstance(data, dict) and data.get("final_btn_status") in (2, "2"):
                    return Receipt.success(message="已核实预约成功")
                return Receipt.unclear(
                    "预约提交已返回，但未确认预约状态；请在原动态核对，不会自动重试",
                    fault=Fault.PARSE,
                )
            return Receipt.success(remote_id=_remote_id(payload), message=message or "已提交")
        if code in codes.ALREADY_DONE_CODES:
            return Receipt.success(message=message or "此前已完成")
        return Receipt.rejected(codes.fault_of(code), f"{message or '远端拒绝'}（{code}）")

    def _verify_share(self, action: Action, remote_id: str) -> Receipt:
        # A successful create response alone does not establish that it was a repost.
        # Read the returned object; never repeat the POST if that read is inconclusive.
        from .parser import dyn_id_of, is_repost, orig_id_of

        if remote_id:
            try:
                payload = codes.ensure_ok(
                    self._transport.fetch(
                        endpoints.DYN_DETAIL,
                        query={"id": remote_id, "features": "itemOpusStyle"},
                        referer=endpoints.dyn_referer(remote_id),
                    ),
                    what="核对转发关联",
                )
                item = (payload.get("data") or {}).get("item") or {}
                if (
                    isinstance(item, dict)
                    and dyn_id_of(item) == remote_id
                    and is_repost(item)
                    and orig_id_of(item) == action.dyn_id
                ):
                    return Receipt.success(remote_id=remote_id, message="已核实转发关联")
            except (RemoteError, ValueError, TypeError):
                pass
        return Receipt(
            False,
            message=f"提交已返回，但未核实原动态关联（动态 {remote_id or '未返回编号'}）；请复核，不会自动重发",
            remote_id=remote_id,
            fault=Fault.PARSE,
            ambiguous=True,
        )

    def _verify_interaction(self, action):
        from .interaction_state import favorite_state, following_attribute
        label = '关注' if action.kind is ActionKind.FOLLOW else '收藏'
        try:
            if action.kind is ActionKind.FOLLOW:
                uid=action.payload.get('up_uid') or action.payload.get('fid')
                answer=codes.ensure_ok(self._transport.fetch(endpoints.RELATION,query={'fid':uid},
                    referer=f'https://space.bilibili.com/{uid}'),what='核实关注状态')
                state=following_attribute((answer.get('data') or {}).get('attribute'))
            else:
                for turn in range(4):
                    state=favorite_state(self._transport,action.dyn_id)
                    if state is True:break
                    if turn < 3:self._wait(0.6 + 0.4 * turn)
            if state is True:return Receipt.success(message=f'已核实{label}成功')
        except RemoteError as exc:
            return Receipt.unclear(f'{label}提交后未能确认状态：{exc}',fault=exc.fault)
        return Receipt.unclear(f'{label}已提交，但未确认成功；不会继续后续动作或自动重发',fault=Fault.PARSE)

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
        if not action.dyn_id.isascii() or not action.dyn_id.isdigit():
            raise RemoteError(Fault.PARSE, "转发动作缺少有效的原动态 ID")
        text = action.payload.get("text", "").strip()
        if not text:
            raise RemoteError(Fault.PARSE, "转发动作缺少正文")
        body = {
            "dyn_req": {
                "content": {"contents": [{"raw_text": text, "type": 1, "biz_id": ""}]},
                "scene": 4,
                "meta": {"app_meta": {"from": "create.dynamic.web", "mobi_app": "web"}},
            },
            "web_repost_src": {"dyn_id_str": action.dyn_id},
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
        return self._transport.submit_json(
            endpoints.THUMB_DYN,
            {"dyn_id_str": action.dyn_id, "up": 1,
             "spmid": "333.1369.0.0", "from_spmid": "333.999.0.0"},
            query={"csrf": self._transport.csrf},
            referer=endpoints.dyn_referer(action.dyn_id),
        )

    def _favorite(self, action: Action) -> dict:
        if action.payload.get('target') == 'opus':
            return self._transport.submit_json(
                endpoints.OPUS_ACTION,
                {'entity':{'object_id_str':action.dyn_id,'type':{'biz':2}},'action':3,
                 'meta':{'spmid':'444.42.0.0','from_spmid':'333.1365.0.0','from':'unknown'}},
                query={'csrf':self._transport.csrf},referer=endpoints.dyn_referer(action.dyn_id),
            )
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
        if not str(reserve_id or "").isascii() or not str(reserve_id).isdigit():
            raise RemoteError(Fault.PARSE, "预约动作缺少预约 ID")
        if action.payload.get("button_status", 1) not in (1, "1"):
            raise RemoteError(Fault.PARSE, "预约按钮不是可预约状态，未提交")
        return self._transport.submit_json(
            endpoints.RESERVE_CLICK,
            {
                "reserve_id": int(reserve_id),
                "cur_btn_status": 1,
                "dynamic_id_str": action.dyn_id,
                "reserve_total": int(action.payload.get("reserve_total") or 0),
                "spmid": "333.1369.0.0",
            },
            query={"csrf": self._transport.csrf},
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
