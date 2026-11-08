"""收件箱：把散在三个地方的未读消息归一成一张清单。

B 站的消息中心分成几块，各有各的接口、各有各的字段：
私信是一串"会话"，回复我的和 @我的 是一串"条目"。系统通知**没有独立的列表接口**
（`/x/msgfeed/system`、`/sys_msg`、`/sysmsg`、`/official`、`/up` 实测全 404），
但官方账号发的**系统卡片会混在私信会话里**（`system_msg_type != 0`，
`content` 是 `{"title","text","modules"}` 而不是 `{"content"}`）—— 这一路能读到内容，
所以按 `kind="system"` 单独归类，不和真人对私信混在一起数。
界面要的是同一张表，所以这里先把它们压平成 `Message`，
判断"这条值不值得看一眼"的逻辑只写一份。

**能干什么、不能干什么要分清。** B 站没有"把这条标记为已读"的公开写接口，
红点只能人自己点掉。所以这里的"已看"是**本机的记录**，作用是"别每次都提醒我
同一条"，不是替用户清红点。假装能清，最后只会让人觉得它坏了。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime

# 中奖相关的说法，分两档。
#
# **强档单独成立**：这些词出现在抽奖语境里基本就只有一个意思。
STRONG = (
    "中奖",
    "恭喜获奖",
    "抽中",
    "获奖",
    "兑奖",
    "领奖",
    "领取奖励",
    "收货地址",
    "邮寄地址",
    "名单有你",
    "中选",
    "私信领取",
)

# **弱档必须配合"指向你"的证据**。真机验证过：只看弱档的话，
# "蹲一个周边"、"梦里都是开奖"这种评论会被判成中奖 —— 抽奖区的评论
# 本来就满口"开奖""周边""福利"，这些词在这里没有区分度。
WEAK = (
    "恭喜",
    "抽奖结果",
    "开奖",
    "发奖",
    "奖励",
    "奖品",
    "中签",
    "福利",
    "周边",
    "兑换码",
    "激活码",
)

# 指向你的词。要么是对你说，要么是让你去做点什么 —— 营销话术不会这样写。
POINTING = (
    "你",
    "@",
    "私信",
    "提供",
    "填写",
    "地址",
    "联系",
    "确认",
    "回复",
    "领取",
    "获取",
)


@dataclass(frozen=True, slots=True)
class Message:
    kind: str
    """chat / reply / at / system。"""

    key: str
    """稳定标识。用来记住"这条我看过了"，所以必须由远端 id 拼出来。"""

    who: str
    text: str
    """对方说的话。中奖判定只看这里 —— 被回复内容的标题不算。"""

    at: datetime | None = None
    unread: bool = True
    link: str = ""
    prize: bool = False
    reason: str = ""
    """为什么判成中奖相关。空字符串表示不是。"""

    context: str = ""
    """被回复的是哪条（标题/摘要）。只做显示，**不参与判定**。

    不加区分的话，自己的专栏标题里带"开奖"就会把自己刷成一屏中奖通知。

    放在最后是刻意的：它不是核心字段，插在 `text` 后面会悄悄挪走 `at` 等
    位置参数的次序，而那种错只有在某条测试突然断言成别的东西时才会露出来。
    """

    automatic: bool = False
    sender_uid: str = ""
    review_key: str = ""
    reviewed: bool = False

    @property
    def short(self) -> str:
        """一行摘要。列表上不换行，所以这里就把换行和连续空白压掉。"""
        text = " ".join(self.text.split())
        return text if len(text) <= 60 else text[:59] + "…"


def prize_hint(text: str) -> tuple[bool, str]:
    """这段话像不像中奖通知。

    返回 (是否像, 为什么)。

    规则是两段的：强关键词单独成立；弱关键词必须再配上一个"指向你"的词。
    真机验证过这个分界 —— 只用弱关键词时，"蹲一个周边"、"梦里都是开奖"
    都会被判成中奖，而抽奖区的评论区里全是这种话。
    """
    body = (text or "").lower()
    if not body:
        return False, ""
    for word in STRONG:
        if word in body:
            return True, f"提到「{word}」"
    weak_hit = next((word for word in WEAK if word in body), "")
    if not weak_hit:
        return False, ""
    point_hit = next((word for word in POINTING if word in body), "")
    if not point_hit:
        # 有"开奖""周边"却没指向任何人：大概率是在闲聊，不是在通知你。
        return False, ""
    return True, f"提到「{weak_hit}」，且指向你"


def annotate(message: Message) -> Message:
    """给一条消息补上"像不像中奖"的标注。已经标过的原样返回。

    **只看 `text`（对方原话）**，不看 `context`（被回复内容的标题）。
    """
    if message.automatic:
        return replace(message, prize=False, reason="自动回复")
    if message.prize:
        return message
    hit, why = prize_hint(message.text)
    if not hit:
        return message
    return replace(message, prize=True, reason=why)


def digest(messages: list[Message]) -> dict[str, int]:
    """给顶栏和通知用的一行数字。"""
    return {
        "total": len(messages),
        "unread": sum(1 for item in messages if item.unread),
        "prize": sum(1 for item in messages if item.prize),
        "chat": sum(1 for item in messages if item.kind == "chat"),
        "system": sum(1 for item in messages if item.kind == "system"),
        "reply": sum(1 for item in messages if item.kind == "reply"),
        "at": sum(1 for item in messages if item.kind == "at"),
    }


def notify_line(messages: list[Message], *, limit: int = 3) -> str:
    """凑一条适合推送出去的正文。

    只挑中奖相关的；一条都没有时返回空串 —— "有 12 条未读"这种通知，
    发出去只会让人关掉通知。

    自己补一遍标注：已经标过的再标一次是幂等的（`annotate` 直接返回原对象），
    但漏标的那条就会静默地不发通知 —— 这种错没人会发现。
    """
    prized = [item for item in map(annotate, messages) if item.prize]
    if not prized:
        return ""
    head = (
        f"有 {len(prized)} 条消息可能在说中奖：" if len(prized) > 1 else "有一条消息可能在说中奖："
    )
    lines = [f"{item.who}：{item.short}" for item in prized[:limit]]
    more = len(prized) - limit
    if more > 0:
        lines.append(f"…还有 {more} 条")
    return head + "\n" + "\n".join(lines)


def parse_chat_body(raw: str) -> str:
    """只要正文，不要副标题。"""
    return parse_chat(raw)[0]


def parse_chat(raw: str) -> tuple[str, str]:
    """拆一条私信的 `last_msg.content`，返回 (正文, 副标题)。

    `content` 是一个 JSON 字符串，但**不止一种形状**，这一点踩过坑：

    - 真人私信：`{"content":"在吗"}` —— 正文在 `content`。
    - 系统卡片（官方账号发的）：`{"title":"登录操作通知","text":"你的账号在新设备…",
      "jump_text":"查看详情","modules":[...]}` —— **正文在 `text`，标题在 `title`，
      没有 `content` 字段**。

    早期只认 `content`，于是四张系统卡片（社区中心、预约通知、服务通知、创作助手）
    整串 JSON 原样显示在列表里，用户看到的就是"一条全是乱码"。JSON 里的 `\u0026`
    也是这个原因露出来的。

    解析不出来就原样返回 —— 显示一坨 JSON 也比把这条消息吞掉好。
    """
    body = (raw or "").strip()
    if not body.startswith("{"):
        return body, ""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return body, ""
    if not isinstance(data, dict):
        return body, ""

    title = data.get("title")
    title = title.strip() if isinstance(title, str) else ""

    # 先认 `content`（真人私信），再认 `text`（系统卡片）。两个都空才往下走。
    for key in ("content", "text"):
        text = data.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip(), title

    # 撤回、图片、礼物这类没有文本，给一句人话，别让列表上出现空白行。
    if data.get("msg_status") == 1 or data.get("recall") is True:
        return "（对方撤回了一条消息）", title
    if data.get("url") or data.get("image") or data.get("img_url"):
        return "（发来一张图片）", title
    if data.get("gift_name") or data.get("gift"):
        return "（发来一个礼物）", title
    return body, title
