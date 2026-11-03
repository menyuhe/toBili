"""Strict read probes and single-attempt mutations used by maintenance only."""
from __future__ import annotations
import httpx

from src.bilibili_auth import get_login_uid, require_login
from src.lottery_api import DYNAMIC_DETAIL_URL, LOTTERY_NOTICE_URL
from src.task_control import check_cancelled

NAV = "https://api.bilibili.com/x/web-interface/nav"
RELATION = "https://api.bilibili.com/x/relation"
MODIFY_RELATION = "https://api.bilibili.com/x/relation/modify"
DELETE = "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/rm_dynamic"
FEED = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
RISK_CODES = {-101, -111, -352, -412, -509, -799, 429}


class MaintenanceBlocked(RuntimeError):
    pass


def checked(payload: dict) -> dict:
    code = payload.get("code")
    if code in RISK_CODES:
        raise MaintenanceBlocked("登录失效或请求受限，本轮维护已停止，请检查账号后重试")
    if code != 0:
        raise RuntimeError(f"接口未确认操作结果（code={code}），已保留")
    return payload.get("data") or {}


def own_repost(item: dict, uid: str, original: str) -> bool:
    author = (item.get("modules") or {}).get("module_author") or {}
    return (item.get("type") == "DYNAMIC_TYPE_FORWARD"
            and str(author.get("mid")) == uid
            and str((item.get("orig") or {}).get("id_str") or "") == original)


class CleanupApi:
    def __init__(self, client, uid: str):
        self.client, self.uid = client, uid
        self.csrf, current = require_login()
        if str(current) != uid:
            raise MaintenanceBlocked("当前账号已改变，停止维护")

    def assert_account(self):
        check_cancelled()
        if str(get_login_uid()) != self.uid:
            raise MaintenanceBlocked("当前账号已改变，停止维护")

    def request(self, method, *args, **kwargs):
        self.assert_account()
        try:
            return getattr(self.client, method)(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403, 412, 429):
                raise MaintenanceBlocked("登录失效或请求受限，本轮维护已停止") from None
            raise

    def verify_login(self):
        self.assert_account()
        data = checked(self.request("request_json", NAV, retries=0))
        if data.get("isLogin") is not True or str(data.get("mid")) != self.uid:
            raise MaintenanceBlocked("登录失效或账号不一致，停止维护")

    def notice(self, item: dict):
        self.assert_account()
        business_type = int(item.get("business_type") or (1 if item.get("lottery_type") == "互动抽奖" else 0))
        business_id = str(item.get("business_id") or (item.get("dynamic_id") if business_type == 1 else "") or "")
        if business_type not in (1, 10) or not business_id:
            return None
        payload = self.request("request_json", LOTTERY_NOTICE_URL,
            {"business_id": business_id, "business_type": business_type}, retries=0)
        data = checked(payload)
        if not data.get("lottery_id"):
            return None
        # Reject a result for another draw, even when the upstream returned code=0.
        if str(data.get("business_id")) != business_id or data.get("business_type") != business_type:
            raise RuntimeError("抽奖结果与活动标识不一致，保留待确认")
        return data

    def dynamic(self, dynamic_id: str):
        self.assert_account()
        payload = self.request("request_json", DYNAMIC_DETAIL_URL, {"id": dynamic_id}, retries=0)
        # Only an explicit business not-found code is absence. HTTP errors/empty data are unknown.
        if payload.get("code") == -404:
            return None
        data = checked(payload)
        item = data.get("item")
        if not isinstance(item, dict) or not item.get("id_str") or str(item["id_str"]) != dynamic_id:
            raise RuntimeError("动态详情不完整，无法核验归属")
        return item

    def following(self, up_uid: str) -> bool:
        self.assert_account()
        data = checked(self.request("request_json", RELATION, {"fid": up_uid}, retries=0))
        if "attribute" not in data:
            raise RuntimeError("关注状态未知，保留原关注")
        attribute = data["attribute"]
        if attribute not in (0, 1, 2, 6, 128):
            raise RuntimeError("关注状态未知，保留原关注")
        return attribute in (2, 6)

    def find_reposts(self, original: str, *, max_pages: int = 5) -> list[str]:
        return sorted(self.find_repost_candidates({original}, max_pages=max_pages).get(original, {}))

    def repost_page(self, offset: str = "") -> dict:
        data = checked(self.request("request_json", FEED,
            {"host_mid": self.uid, "offset": offset, "type": "all"}, retries=0))
        if not isinstance(data.get("items"), list) or data.get("has_more") not in (True, False, 0, 1):
            raise RuntimeError("动态列表不完整，已保留扫描进度，请稍后重试")
        next_offset = str(data.get("offset") or "")
        if data["has_more"] and (not next_offset or next_offset == offset or not data["items"]):
            raise RuntimeError("动态分页游标异常，已保留扫描进度，请稍后重试")
        return {**data, "offset": next_offset}

    def find_repost_candidates(self, originals: set[str], *, max_pages: int = 5) -> dict[str, dict[str, str]]:
        matches, offset = {}, ""
        for _ in range(max_pages):
            self.assert_account()
            data = checked(self.request("request_json", FEED,
                {"host_mid": self.uid, "offset": offset, "type": "all"}, retries=0))
            for item in data.get("items") or []:
                original = str((item.get("orig") or {}).get("id_str") or "")
                if original in originals and own_repost(item, self.uid, original) and str(item.get("id_str") or "").isdigit():
                    up = ((item.get("orig") or {}).get("modules") or {}).get("module_author") or {}
                    matches.setdefault(original, {})[str(item["id_str"])] = str(up.get("mid") or "")
            next_offset = str(data.get("offset") or "")
            if not data.get("has_more") or not next_offset or next_offset == offset:
                break
            offset = next_offset
        return matches

    def delete_repost(self, dynamic_id: str):
        self.assert_account()
        payload = self.request("post_form", DELETE,
            {"dynamic_id": dynamic_id, "csrf": self.csrf, "csrf_token": self.csrf},
            retries=0, raise_on_code=False)
        if payload.get("code") == 500404:
            return
        checked(payload)

    def unfollow(self, up_uid: str):
        self.assert_account()
        checked(self.request("post_form", MODIFY_RELATION,
            {"fid": up_uid, "act": 2, "re_src": 11, "csrf": self.csrf},
            retries=0, raise_on_code=False))
