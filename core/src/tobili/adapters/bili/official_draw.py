"""Resolve Bilibili draw components by their own business identity, never by LLM."""

from collections.abc import Mapping
from datetime import UTC, datetime

from ...domain.draw import DrawFacts
from ...domain.errors import Fault, RemoteError
from . import codes, endpoints


def objects(value):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def component(item):
    """A reservation card is not an interactive draw, even when prose says 抽奖."""
    from .parser import dyn_id_of, subject_of

    subject = subject_of(item)
    reserve = None
    for node in objects(subject.get("modules", {})):
        if (
            isinstance(node.get("upower_lottery"), Mapping)
            or node.get("type") == "ADDITIONAL_TYPE_UPOWER_LOTTERY"
        ):
            return dyn_id_of(subject), 12, "充电专属抽奖需手动处理"
        if isinstance(node.get("reserve"), Mapping) and node["reserve"].get("rid"):
            reserve = node["reserve"]
    if reserve:
        ended = any(
            "已结束" in str(n.get("text", "")) or "已开奖" in str(n.get("text", ""))
            for n in objects(reserve.get("button", {}))
        )
        return str(reserve["rid"]), 10, "预约组件已结束" if ended else ""
    return dyn_id_of(subject), 1, ""


def has_component(item):
    _bid, kind, _ = component(item)
    if kind in (10, 12):
        return True
    return any(
        "LOTTERY" in str(n.get("type", "")).upper() for n in objects(item.get("modules", {}))
    )


def notice_facts(notice, bid, kind, *, origin="notice"):
    if not isinstance(notice, Mapping) or not notice.get("lottery_id"):
        return None
    if str(notice.get("business_id")) != bid or str(notice.get("business_type")) != str(kind):
        raise ValueError("官方抽奖返回了不同的活动编号，未采用")
    parts = []
    for tier in ("first", "second", "third"):
        text = str(notice.get(tier + "_prize_cmt") or "").strip()
        description = str(notice.get(tier + "_item_desc") or "").strip()
        if description and description != text:
            text = description if text in description else text + "（" + description + "）"
        count = notice.get(tier + "_prize")
        if text:
            count = int(count or 0)
            parts.append(text + (f" × {count}份" if count > 0 else ""))
    raw = notice.get("lottery_time")
    deadline = datetime.fromtimestamp(int(raw), UTC) if raw and int(raw) > 0 else None
    result = notice.get("lottery_result") or {}
    winners = (
        any(isinstance(v, list) and bool(v) for v in result.values())
        if isinstance(result, Mapping)
        else bool(result)
    )
    ended = winners or int(notice.get("status") or 0) != 0
    return DrawFacts(
        bid,
        kind,
        "；".join(parts),
        deadline,
        "ended" if ended else "active",
        "官方已公布获奖名单" if winners else "官方抽奖已结束" if ended else "",
        origin,
    )


def probe_notice(wire, dyn_id, *, kinds=(1, 12)):
    """没有抽奖组件的动态，也去官方索引里问一次。

    组件是"渲染出来的结果"，不是"活动的存在证明"：同一个 UP 发的同一类抽奖，
    在话题卡片、转发态、Opus 详情的不同读法下，组件可能压根不出现，但官方公告
    仍然在。反过来，靠正文正则认出来的"抽奖"，绝大多数在官方索引里查无此项 ——
    这就是"猜出来的活动"和"官方登记过的活动"之间唯一可靠的界线。

    返回 `None` 表示官方索引里确实没有这条动态。中途遇到风控/网络问题按异常上抛，
    因为"查不到"和"没查成"必须分开：前者是结论，后者不是。
    """
    for kind in kinds:
        try:
            response = wire.fetch(
                endpoints.LOTTERY_NOTICE,
                query={"business_id": str(dyn_id), "business_type": kind},
                referer=endpoints.dyn_referer(str(dyn_id)),
            )
        except RemoteError as exc:
            if exc.fault in (
                Fault.AUTH,
                Fault.RISK,
                Fault.RATE_LIMIT,
                Fault.NETWORK,
                Fault.TIMEOUT,
            ):
                raise
            continue
        if codes.code_of(response) != codes.OK:
            continue
        facts = notice_facts(response.get("data"), str(dyn_id), kind)
        if facts:
            return facts
    return None


def read_draw(wire, item):
    bid, kind, closed = component(item)
    # A #互动抽奖# topic is ordinary text, not the official lottery widget.
    if kind == 1 and not has_component(item):
        return None
    if kind == 12:
        return DrawFacts(bid, kind, state="unsupported", reason=closed, origin="component")
    if not bid:
        return DrawFacts(reason="官方组件缺少活动编号")
    try:
        response = wire.fetch(
            endpoints.LOTTERY_NOTICE,
            query={"business_id": bid, "business_type": kind},
            referer=endpoints.dyn_referer(str(item.get("id_str") or bid)),
        )
        codes.ensure_ok(response, what="读取官方抽奖详情")
        facts = notice_facts(response.get("data"), bid, kind, origin="component")
        if facts:
            if closed and facts.state != "ended":
                from dataclasses import replace

                facts = replace(facts, state="ended", reason=closed)
            return facts
        if kind == 10 or has_component(item):
            return DrawFacts(
                bid,
                kind,
                state="ended" if closed else "unverified",
                reason=closed or "官方组件未返回抽奖详情，禁止按普通转发参与",
                origin="component",
            )
        return None
    except RemoteError as exc:
        if exc.fault in (Fault.AUTH, Fault.RISK, Fault.RATE_LIMIT, Fault.NETWORK, Fault.TIMEOUT):
            raise
        return DrawFacts(
            bid,
            kind,
            state="ended" if closed else "unverified",
            reason=closed or "官方抽奖详情暂不可核实",
            origin="component",
        )
    except (ValueError, TypeError, OverflowError):
        return DrawFacts(
            bid,
            kind,
            state="unverified",
            reason="官方抽奖字段不完整或活动编号不匹配",
            origin="component",
        )
