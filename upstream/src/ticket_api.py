"""Membership ticket transport; no generic write retries or challenge bypass."""
from __future__ import annotations
import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from src.cleanup_api import CleanupApi

BASE = "https://show.bilibili.com"


def project_id(value: str) -> str:
    value = value.strip()
    if value.isdigit() and 0 < int(value) < 10**16:
        return value
    url = urlsplit(value)
    if url.scheme != "https" or url.hostname not in {"show.bilibili.com", "mall.bilibili.com"}:
        raise ValueError("请输入会员购项目数字 ID 或官方 HTTPS 票务链接")
    raw = parse_qs(url.query).get("id", parse_qs(url.query).get("project_id", [""]))[0]
    if not re.fullmatch(r"[1-9][0-9]{0,15}", raw):
        raise ValueError("票务链接缺少有效项目 ID")
    return raw


def checked(payload):
    code = payload.get("errno", payload.get("code"))
    if code != 0 or not isinstance(payload.get("data"), dict):
        raise ValueError(f"会员购接口未返回有效数据（代码 {code}），请打开官方页面核实")
    return payload["data"]


def stamp(value):
    try:
        if str(value).isdigit():
            n = int(value); return n // 1000 if n > 10**11 else n
        return int(datetime.fromisoformat(str(value).replace(" ", "T")).timestamp())
    except (ValueError, TypeError):
        return 0


class TicketApi(CleanupApi):
    def get(self, endpoint, params=None):
        return checked(self.request("request_json", BASE + endpoint, params or {}, retries=0))

    def post(self, endpoint, payload):
        return self.request("post_json", BASE + endpoint, {**payload, "csrf": self.csrf},
                            referer=BASE + "/", retries=0, raise_on_code=False)

    def project(self, pid):
        data = self.get("/api/ticket/project/getV2", {"id": pid, "project_id": pid, "version": 134})
        if str(data.get("id")) != str(pid):
            raise ValueError("项目身份不一致，请重新读取")
        options = []
        for screen in data.get("screen_list") or []:
            for ticket in screen.get("ticket_list") or []:
                if not str(screen.get("id", "")).isdigit() or not str(ticket.get("id", "")).isdigit():
                    continue
                price = ticket.get("price")
                if not isinstance(price, int) or price < 0:
                    continue
                options.append(dict(screen_id=str(screen["id"]), sku_id=str(ticket["id"]),
                    label=f"{screen.get('name', '')} · {ticket.get('desc', '')}", price=price,
                    express_fee=0 if data.get("has_eticket") else int(screen.get("express_fee") or 0),
                    sale_start=stamp(ticket.get("sale_start")),
                    supported=not bool(screen.get("is_seat") or screen.get("seat_flag") or screen.get("link_id"))))
        if not options:
            raise ValueError("未读取到常规场次票档，请使用官方页面选择日期或座位")
        return dict(project_id=str(pid), name=str(data.get("name") or "会员购项目"),
                    url=f"{BASE}/platform/detail.html?id={pid}", options=options)

    def buyers(self, pid):
        return self.get("/api/ticket/buyer/list", {"projectId": pid}) .get("list") or []

    def addresses(self):
        return self.get("/api/ticket/addr/list").get("addr_list") or []

    def context(self, pid):
        project = self.project(pid)
        project["buyers"] = [dict(id=str(b["id"]), label=f"{str(b.get('name') or '购票人')[:1]}** · 证件尾号 {str(b.get('personal_id') or '')[-4:]}") for b in self.buyers(pid) if b.get("id")]
        project["addresses"] = [dict(id=str(a["id"]), label=f"{str(a.get('name') or '')[:1]}** · {a.get('prov', '')}{a.get('city', '')} · 手机尾号 {str(a.get('phone') or '')[-4:]}") for a in self.addresses() if a.get("id")]
        return project

    def payload(self, config, contact):
        project = self.project(config["project_id"])
        option = next((o for o in project["options"] if o["screen_id"] == config["screen_id"] and o["sku_id"] == config["sku_id"]), None)
        if not option or not option["supported"]:
            raise ValueError("票档已变化或需要手动选座，请重新配置")
        buyers = {str(b["id"]): b for b in self.buyers(config["project_id"]) if b.get("id")}
        if not config["buyer_ids"] or any(i not in buyers for i in config["buyer_ids"]):
            raise ValueError("购票人资料已变化，请重新读取")
        amount = option["price"] * len(config["buyer_ids"]) + option["express_fee"]
        if amount != config["amount"]:
            raise ValueError("票价或运费已变化，请重新核对金额后创建任务")
        address = next((a for a in self.addresses() if str(a.get("id")) == config["address_id"]), {}) if config["address_id"] else {}
        if option["express_fee"] and not address:
            raise ValueError("此票档需要配送地址")
        return dict(project_id=int(config["project_id"]), screen_id=int(config["screen_id"]), sku_id=int(config["sku_id"]),
            count=len(config["buyer_ids"]), order_type=1, pay_money=amount,
            buyer_info=[buyers[i] for i in config["buyer_ids"]], buyer=contact["name"], tel=contact["phone"],
            deliver_info=dict(name=address.get("name", ""), tel=address.get("phone", ""), addr_id=address.get("id", 0),
                              addr="".join(str(address.get(k) or "") for k in ("prov", "city", "area", "addr"))))

    def prepare(self, payload):
        fields = {k: payload[k] for k in ("project_id", "screen_id", "sku_id", "count", "order_type", "buyer_info")}
        return self.post("/api/ticket/order/prepare", {**fields, "token": "", "newRisk": True, "requestSource": "neul-next"})

    def create(self, payload, token):
        import time
        return self.post("/api/ticket/order/createV2", {**payload, "buyer_info": json.dumps(payload["buyer_info"], ensure_ascii=False),
            "deliver_info": json.dumps(payload["deliver_info"], ensure_ascii=False), "token": token,
            "timestamp": int(time.time()*1000), "again": 1, "newRisk": True, "requestSource": "neul-next"})

    def payment_available(self, order_id):
        # A successful pay-parameter lookup proves this known order remains payable.
        # Failure is NOT evidence of payment, cancellation, or absence of an order.
        return bool(self.get("/api/ticket/order/getPayParam", {"order_id": order_id}).get("code_url"))
