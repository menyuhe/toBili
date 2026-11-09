"""读路径：账号档案与单条动态详情。"""

from __future__ import annotations

from datetime import UTC, datetime

from ...domain.errors import Fault, MissingCredential, RemoteError
from ...domain.inbox import Message, parse_chat
from ...domain.tidy import ShareRef
from ...ports.models import AccountProfile, CampaignDetail, MessageStat, UserCandidate
from . import codes, endpoints
from .http import Wire
from .parser import author_of, dyn_id_of, orig_id_of, to_detail, to_user_candidate
from .wbi import WbiSigner


class DynamicReader:
    def __init__(self, transport: Wire) -> None:
        self._transport = transport
        self._uid = ""
        self._signer = WbiSigner(transport)
        self._names = {}

    def sender_name(self, uid: str) -> str:
        import time

        cached = self._names.get(uid)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        name = ""
        try:
            payload = codes.ensure_ok(
                self._transport.fetch(
                    f"{endpoints.API}/x/web-interface/card",
                    query={"mid": uid},
                    referer=f"https://space.bilibili.com/{uid}",
                ),
                what="读取消息发送者",
            )
            name = str(((payload.get("data") or {}).get("card") or {}).get("name") or "")
        except Exception:
            pass
        if len(self._names) >= 1000:
            self._names.clear()
        self._names[uid] = (name, time.monotonic() + (86400 if name else 300))
        return name

    def profile(self) -> AccountProfile:
        payload = self._transport.fetch(endpoints.NAV)
        code = codes.code_of(payload)
        if code == codes.NOT_LOGGED_IN:
            raise MissingCredential("登录态已失效，请重新扫码")
        if code != codes.OK:
            raise RemoteError(codes.fault_of(code), "读取账号信息失败", code=code)

        data = payload.get("data") or {}
        if not data.get("isLogin"):
            raise MissingCredential("登录态已失效，请重新扫码")

        level = (data.get("level_info") or {}).get("current_level") or 0
        return AccountProfile(
            uid=str(data.get("mid") or ""),
            nickname=str(data.get("uname") or ""),
            avatar=str(data.get("face") or ""),
            level=int(level),
            following=int(data.get("following") or 0),
            followers=int(data.get("follower") or 0),
        )

    def detail(self, dyn_id: str) -> CampaignDetail:
        payload = codes.ensure_ok(
            self._transport.fetch(
                endpoints.DYN_DETAIL,
                query={"id": dyn_id, "features": "itemOpusStyle"},
                referer=endpoints.dyn_referer(dyn_id),
            ),
            what="读取动态详情",
        )
        item = (payload.get("data") or {}).get("item")
        if not isinstance(item, dict):
            raise RemoteError(Fault.PARSE, f"动态 {dyn_id} 没有详情数据")
        from dataclasses import replace

        from .official_draw import read_draw
        from .parser import subject_of, text_of
        from .public_sources import OPUS, opus_item

        item = subject_of(item)
        if not text_of(item):
            opus = codes.ensure_ok(
                self._transport.fetch(
                    OPUS,
                    query={"id": str(item.get("id_str") or dyn_id)},
                    referer=endpoints.dyn_referer(dyn_id),
                ),
                what="读取图文详情",
            )
            raw = (opus.get("data") or {}).get("item")
            if isinstance(raw, dict):
                item = opus_item(raw)
        facts = read_draw(self._transport, item)
        detail = to_detail(item, dyn_id=dyn_id)
        return replace(detail, draw=facts, deadline=facts.deadline if facts else detail.deadline)

    def raw_detail(self, dyn_id: str) -> dict:
        """探测需要原始结构（比如点赞状态），单独开一个口子。"""
        payload = codes.ensure_ok(
            self._transport.fetch(
                endpoints.DYN_DETAIL,
                query={"id": dyn_id},
                referer=endpoints.dyn_referer(dyn_id),
            ),
            what="读取动态详情",
        )
        item = (payload.get("data") or {}).get("item")
        return item if isinstance(item, dict) else {}

    def comments(
        self,
        *,
        rid: str,
        comment_type: int = 17,
        pages: int = 3,
        page_size: int = 20,
        referer: str = "",
    ) -> list[str]:
        """抓评论区正文，供"挑一条真人评论当转发语"用。

        只读、可失败：评论关了、被折叠、签名过期，都返回已经拿到的部分。抓评论
        是加分项，它失败不该让参与流程失败 —— 上层会退回到模板文案。

        三件在真机上踩出来的事：

        1. `rid` 必须是动态详情里的 `basic.comment_id_str`，不是动态 ID。用动态
           ID 请求会得到 `-404 啥都木有`，看起来像"这条没人评论"。
        2. 接口要 wbi 签名，没签名是 `-403 访问权限不足`。两个错误码不能混为一谈。
        3. 分页游标在 `data.cursor.next`，不是自己加页码。
        """
        messages: list[str] = []
        cursor: int | str | None = 0
        for page in range(max(1, pages)):
            if cursor is None:
                break
            payload = self._reply_page(
                rid=rid,
                comment_type=comment_type,
                cursor=cursor,
                page_size=page_size,
                referer=referer,
                # 第一页签名过期是可能的（密钥按天换），重签一次；后面几页没必要，
                # 同一个密钥在同一次循环里不会两分钟就失效。
                resign=page == 0,
            )
            if payload is None:
                break
            data = payload.get("data") or {}
            if not isinstance(data, dict):
                break
            replies = data.get("replies")
            if isinstance(replies, list):
                for reply in replies:
                    if not isinstance(reply, dict):
                        continue
                    content = reply.get("content")
                    if isinstance(content, dict):
                        messages.append(str(content.get("message") or ""))
            cursor = _next_cursor(data.get("cursor"))
        return messages

    def _reply_page(
        self,
        *,
        rid: str,
        comment_type: int,
        cursor: int | str,
        page_size: int,
        referer: str,
        resign: bool,
        strict: bool = False,
        mode: int = 3,
    ) -> dict | None:
        query = {
            "oid": rid,
            "type": comment_type,
            "mode": mode,
            "next": cursor,
            "ps": page_size,
        }
        if isinstance(cursor, str):
            import json

            query.pop("next")
            query["pagination_str"] = json.dumps({"offset": cursor}, separators=(",", ":"))
        for attempt in range(2 if resign else 1):
            try:
                payload = self._transport.fetch(
                    endpoints.LIST_REPLY_WBI,
                    query=self._signer.sign(query, force=attempt > 0),
                    referer=referer or endpoints.FOLLOWING_REFERER,
                )
            except Exception:
                if strict:
                    raise
                return None
            if codes.code_of(payload) == codes.OK:
                return payload
            # -403 是签名不对（可能是密钥轮换了），重签一次还有机会。
            if codes.code_of(payload) != codes.FORBIDDEN:
                if strict:
                    codes.ensure_ok(payload, what="核对本人评论")
                return None
        return None

    def search_users(
        self, keyword: str, *, page: int = 1, limit: int = 20
    ) -> tuple[UserCandidate, ...]:
        """按关键词搜 UP 主。

        搜索接口只给视频、专栏、用户三类结果，**没有"搜动态"这一项**（实测）。所以
        这个能力只能用来找"可能值得订阅的人"，不能当作活动发现源 —— 把视频结果
        当成活动喂给参与流程，就会去做一套根本不存在的动作。
        """
        payload = codes.ensure_ok(
            self._transport.fetch(
                endpoints.SEARCH_TYPE,
                query={
                    "search_type": "bili_user",
                    "keyword": keyword,
                    "page": max(1, page),
                    "page_size": max(1, min(50, limit)),
                },
                referer=endpoints.SEARCH_REFERER,
            ),
            what="搜索 UP 主",
        )
        result = (payload.get("data") or {}).get("result") or []
        candidates = []
        for raw in result:
            if not isinstance(raw, dict):
                continue
            parsed = to_user_candidate(raw)
            if parsed is not None:
                candidates.append(parsed)
        return tuple(candidates[: max(1, limit)])

    # ---- 清理用 ----
    def me(self) -> str:
        if not self._uid:
            self._uid = self.profile().uid
        return self._uid

    def lottery_notice(self, business_id: str, business_type: int = 1) -> dict | None:
        import time

        for attempt in range(2):
            payload = self._transport.fetch(
                endpoints.LOTTERY_NOTICE,
                query={"business_id": business_id, "business_type": business_type},
                referer=endpoints.dyn_referer(business_id),
            )
            if codes.code_of(payload) != -9999 or attempt:
                break
            time.sleep(1)
        codes.ensure_ok(payload, what="读取官方开奖结果")
        notice = payload.get("data")
        if not isinstance(notice, dict) or not notice.get("lottery_id"):
            return None
        if str(notice.get("business_id")) != business_id or str(notice.get("business_type")) != str(
            business_type
        ):
            raise ValueError("返回的开奖结果与当前活动不一致")
        return notice

    def own_shares(self, *, cursor: str = "") -> dict:
        uid = self.me()
        if not uid:
            raise ValueError("未读取到当前账号")
        payload = codes.ensure_ok(
            self._transport.fetch(
                endpoints.SPACE_FEED,
                query={"host_mid": uid, "offset": cursor, "type": "all", "platform": "web"},
                referer=endpoints.SPACE_REFERER.format(uid=uid),
            ),
            what="读取本人转发",
        )
        data = payload.get("data") or {}
        if not isinstance(data.get("items"), list):
            raise ValueError("未返回有效动态列表")
        items = []
        for raw in data["items"]:
            if not isinstance(raw, dict):
                continue
            author, _, _ = author_of(raw)
            original = orig_id_of(raw)
            if author == uid and original and dyn_id_of(raw):
                up, _, _ = author_of(raw.get("orig") or {})
                items.append(dict(id=dyn_id_of(raw), original=original, up_uid=up))
        cursor = str(data.get("offset") or "") if data.get("has_more") else ""
        return dict(uid=uid, items=items, cursor=cursor, scanned=len(data["items"]))

    def find_share(self, orig_dyn_id: str, *, pages: int = 4) -> ShareRef | None:
        """在我的空间动态里找"转发自 orig_dyn_id 的那一条"。

        返回 `None` 表示翻完了没找到。翻不到有两种可能：我没转过，或者它比翻到
        的位置更早。两种情况都不该删任何东西，所以合并成 `None` 是安全的 —— 这个
        函数的返回值只用来**决定要不要动手**，不用来证明什么。删除之后要确认结果，
        靠的是再调一次它（找不到即认为已删）。

        注意这里必须扫**我自己的**空间：转发产生的是新动态，作者是我，`orig` 指向
        原活动。因此"出现在我的空间里"本身就是归属证据，不需要额外信任何人。
        """
        uid = self.me()
        if not uid:
            return None
        offset = ""
        for _ in range(max(1, pages)):
            payload = codes.ensure_ok(
                self._transport.fetch(
                    endpoints.SPACE_FEED,
                    query={
                        "host_mid": uid,
                        "offset": offset,
                        "type": "all",
                        "platform": "web",
                    },
                    referer=endpoints.SPACE_REFERER.format(uid=uid),
                ),
                what="读取我的动态",
            )
            data = payload.get("data") or {}
            items = data.get("items") or []
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                if orig_id_of(raw) != orig_dyn_id:
                    continue
                author_uid, _name, published = author_of(raw)
                return ShareRef(
                    dyn_id=dyn_id_of(raw),
                    author_uid=author_uid,
                    orig_dyn_id=orig_dyn_id,
                    published_at=published,
                )
            offset = str(data.get("offset") or "")
            if not offset or not data.get("has_more", True):
                break
        return None

    def unread(self) -> MessageStat:
        """消息中心未读数。清理闸门的安全输入，读不到就抛错让上层当"不可读"处理。"""
        payload = codes.ensure_ok(
            self._transport.fetch(
                endpoints.MSG_UNREAD,
                query={"platform": "web", "build": 0, "mobi_app": "web"},
                referer=endpoints.MESSAGE_REFERER,
            ),
            what="读取未读消息",
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RemoteError(Fault.PARSE, "未读消息结构异常")
        return MessageStat(
            chat=_count(data, "chat"),
            system=_count(data, "sys_msg"),
            reply=_count(data, "reply") + _count(data, "recv_reply"),
            at=_count(data, "at"),
            like=_count(data, "like") + _count(data, "recv_like"),
        )

    def inbox(self, *, pages: int = 1) -> list[Message]:
        """未读消息清单。

        三类来源形状完全不同，各自解析完再拼起来。**一类失败只跳过一类** ——
        私信的接口偶尔会比 msgfeed 先挂，那时仍然应该能看到回复和 @，
        而不是整张清单变空、看起来像"没有新消息"。
        """
        items: list[Message] = []
        for fetch in (self._chats, self._replies, self._ats):
            try:
                items.extend(fetch(pages))
            except Exception:
                # 静默跳过是刻意的：调用方拿不到"哪一类失败"也没法补救，
                # 而让整张清单为空会误导成"确实没有新消息"。
                continue
        items.sort(key=lambda item: item.at.timestamp() if item.at else 0, reverse=True)
        return items

    def _chats(self, pages: int) -> list[Message]:
        out: list[Message] = []
        seen: set[str] = set()
        for _page in range(max(1, pages)):
            payload = codes.ensure_ok(
                self._transport.fetch(
                    endpoints.MSG_SESSIONS,
                    query={
                        "session_type": 4,
                        "group_fold": 0,
                        "unfollow_fold": 0,
                        "size": 30,
                        "sort_rule": 2,
                        "build": 0,
                        "mobi_app": "web",
                    },
                    referer=endpoints.MESSAGE_REFERER,
                ),
                what="读取私信列表",
            )
            data = payload.get("data")
            if not isinstance(data, dict):
                break
            sessions = data.get("session_list")
            if not isinstance(sessions, list):
                break
            fresh = 0
            for row in sessions:
                if not isinstance(row, dict):
                    continue
                talker = str(row.get("talker_id") or "")
                if not talker or talker in seen:
                    continue
                seen.add(talker)
                fresh += 1
                last = row.get("last_msg") if isinstance(row.get("last_msg"), dict) else {}
                text, subject = parse_chat(str((last or {}).get("content") or ""))
                # 官方账号发的是系统卡片，不是真人。混在"私信"里数会让人以为
                # 有 12 个人找他，而且它们的 talker_id 是 844424930… 这种雪花号，
                # 当名字显示就是一串数字。名字优先取 account_info.name。
                system = bool(row.get("system_msg_type"))
                who = str(
                    (row.get("account_info") or {}).get("name")
                    if isinstance(row.get("account_info"), dict)
                    else ""
                ) or str(row.get("group_name") or row.get("nickname") or "")
                if not who and not system:
                    who = self.sender_name(talker)
                out.append(
                    Message(
                        kind="system" if system else "chat",
                        key=f"chat:{talker}",
                        who=who or "名称待获取",
                        sender_uid=talker,
                        automatic=(
                            str(last.get("msg_source")) in {"8", "9", "10", "11"}
                            or text.startswith(("[自动回复]", "【自动回复】"))
                        ),
                        text=text,
                        context=subject,
                        at=_stamp((last or {}).get("timestamp")),
                        unread=int(row.get("unread_count") or 0) > 0,
                        link=f"https://message.bilibili.com/#/whisper/mid{talker}",
                    )
                )
            # 会话列表接口不分页，第二页拿到的还是同一批。没新东西就停。
            if fresh == 0:
                break
        return out

    def _replies(self, pages: int) -> list[Message]:
        return self._msgfeed(endpoints.MSG_REPLY, "reply", "reply_time", pages)

    def _ats(self, pages: int) -> list[Message]:
        return self._msgfeed(endpoints.MSG_AT, "at", "at_time", pages)

    def _msgfeed(self, url: str, kind: str, time_key: str, pages: int) -> list[Message]:
        """回复我的 / @我的 形状几乎一样，解析合在一处。

        翻页靠 `cursor.id` 往下传：不传就永远拿到第一页，翻页参数等于没写。
        """
        out: list[Message] = []
        cursor_id = 0
        for _ in range(max(1, pages)):
            payload = codes.ensure_ok(
                self._transport.fetch(
                    url,
                    query={
                        "platform": "web",
                        "build": 0,
                        "mobi_app": "web",
                        "id": cursor_id,
                    },
                    referer=endpoints.MESSAGE_REFERER,
                ),
                what="读取消息列表",
            )
            data = payload.get("data")
            if not isinstance(data, dict):
                break
            rows = data.get("items")
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                item = row.get("item") if isinstance(row.get("item"), dict) else {}
                user = row.get("user") if isinstance(row.get("user"), dict) else {}
                # `source_content` 才是对方说的话。`title`/`desc` 是**被回复对象**
                # 自己的标题和摘要 —— 把标题拼进来会让"我的专栏标题里带'开奖'"
                # 变成一整屏的"可能中奖"（实测 20 条全是自己发的专栏）。
                text = _first_text(
                    item.get("source_content"),
                    item.get("target_reply_content"),
                    item.get("message"),
                )
                context = _first_text(item.get("title"), item.get("desc"))
                out.append(
                    Message(
                        kind=kind,
                        key=f"{kind}:{row.get('id') or item.get('source_id') or ''}",
                        who=str(user.get("nickname") or item.get("business") or "B 站"),
                        text=text,
                        context=context,
                        at=_stamp(row.get(time_key)),
                        unread=True,
                        link=str(item.get("uri") or ""),
                    )
                )
            cursor = data.get("cursor") if isinstance(data.get("cursor"), dict) else {}
            next_id = cursor.get("id")
            if not isinstance(next_id, int) or next_id == cursor_id:
                break
            cursor_id = next_id
        return out


def _first_text(*values: object) -> str:
    """取第一个非空字符串。字段缺失、不是字符串、全是空白，都当没有。"""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _stamp(value: object) -> datetime | None:
    """秒级时间戳转 datetime。字符串/None 一律当没有时间。"""
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(int(value), tz=UTC)
    return None


def _count(data: dict, key: str) -> int:
    value = data.get(key)
    return int(value) if isinstance(value, (int, float)) else 0


def _next_cursor(cursor: object) -> int | str | None:
    """下一批评论的游标。`is_end` 或没有 `next` 就是到头了。

    注意 `next=0` 是合法的（第一批），只有 `None` 才算结束 —— 把 0 当成"没有了"
    会让每次抓评论都只拿到第一页。
    """
    if not isinstance(cursor, dict):
        return None
    if cursor.get("is_end"):
        return None
    pagination = cursor.get("pagination_reply")
    if isinstance(pagination, dict):
        offset = pagination.get("next_offset")
        if isinstance(offset, str) and offset:
            return offset
    raw = cursor.get("next")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
