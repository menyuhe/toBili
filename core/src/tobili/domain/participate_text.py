"""参与文案。

转发抽奖的转发语，是这个工具唯一会被别人看见的输出。写死一句"转发抽奖"的后果
不只是难看：同一条文案出现在几十条转发里，是最容易被判定为机器行为的特征之一。

三条路，按可靠性排序，任何一条失败都往下退，最后一定有东西可发：

    custom          用户自己写（带变量），最稳
    random_comment  抓原动态评论区，挑一条真人写的（老版本的做法）
    llm            让模型读正文现写一句

"往下退"这件事必须是设计的一部分，而不是异常处理：评论区关了的、模型欠费的、
网络抖动的，都不该让一次参与失败。

这里只放纯逻辑 —— 什么算"能用的评论"、模板怎么填、prompt 怎么写。取评论、
调模型都在适配器里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

# B 站动态转发最多 233 字（评论 1000）。取 60 是刻意的：转发语比正文长本身
# 就很可疑，而且越长越容易撞上敏感词。
MAX_LEN = 60
MIN_LEN = 4


def required_comment_text(body: str) -> str:
    """Only an explicitly quoted comment instruction is a mandatory literal.

    Product descriptions and quoted prize names are not comment instructions.
    Keep the literal intact rather than asking a model to paraphrase it.
    """
    matches = re.findall(
        r"(?:评论(?:区)?(?:中)?(?:留言|回复|输入)?|留言|回复)"
        r'\s*(?:关键词|口令|内容)?\s*[:：]?\s*[“"「『【]([^”"」』】\n]{1,60})[”"」』】]',
        body or "",
    )
    unique = list(dict.fromkeys(x.strip() for x in matches if x.strip()))
    return unique[0] if len(unique) == 1 else ""


# 老版本只跳过前 5 条。前几条通常是 UP 置顶和自己人，照抄风险高；太靠后
# 又多是"我也是""蹲一个"。这个窗口是它的经验值，保留。
SKIP_HEAD = 5
POOL_END = 65


class TextMode(StrEnum):
    CUSTOM = "custom"
    RANDOM_COMMENT = "random_comment"
    LLM = "llm"


DEFAULT_CUSTOM = "来啦，这次试试运气"
DEFAULT_FALLBACK = "转发一下"


# 出现在转发语里最像模板的词。命中就不选 —— 真人很少在转发语里说"转发抽奖"。
_TEMPLATE_WORDS = (
    "转发抽奖",
    "抽奖",
    "转发",
    "求中",
    "参与",
    "中奖",
    "抽我",
    "求好运",
    "开奖",
    "恭喜",
    "已转发",
    "蹲",
)

_URL = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|cn|net|top|xyz)\b)", re.I)
_MENTION = re.compile(r"@\S+")
_HAN = re.compile(r"[\u4e00-\u9fff]")
# B 站的表情在接口里是 `[大笑]` 这样的文本标记。它们在字数统计里会伪装成真汉字，
# 所以先剥掉再数 —— 否则三条表情从字数上看像一句完整的话。
_BRACKET_EMOTE = re.compile(r"\[[^\[\]]{1,8}\]")
# 纯表情在评论里很常见。这类东西贴进转发语只会显得更假。
_EMOTE_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)


def fill_template(template: str, *, up_name: str = "", title: str = "", prize: str = "") -> str:
    """模板变量替换。

    只认四个变量，多一个都不支持：变量越灵活，越容易写出一条谁也看不懂的模板，
    而它每天会被发几十次。
    """
    text = template or ""
    for key, value in (
        ("{up}", up_name),
        ("{title}", title),
        ("{prize}", prize),
    ):
        text = text.replace(key, value or "")
    return text.strip()


def clean_candidate(text: str) -> str | None:
    """判断一条评论能不能直接拿来当转发语。不能就返回 None。

    这里刻意偏严：宁可少一条候选，也不要发出去一句"蹲蹲蹲蹲"。
    """
    value = " ".join((text or "").split())
    if not (MIN_LEN <= len(value) <= MAX_LEN):
        return None
    if _URL.search(value) or _MENTION.search(value):
        return None
    if _EMOTE_ONLY.match(value):
        return None
    if len(_HAN.findall(_BRACKET_EMOTE.sub("", value))) < 3:
        # 三个汉字都凑不出来的，基本是表情、标点、或者英文水贴。
        return None
    lowered = value.lower()
    if any(word in lowered for word in _TEMPLATE_WORDS):
        return None
    return value


def build_comment_pool(messages: list[str]) -> list[str]:
    """从抓回来的评论里挑出能用的。

    顺序上先去重再过滤：重复的评论往往正是刷屏的那些，先去掉能少判断很多次。
    """
    window = messages[SKIP_HEAD:POOL_END]
    seen: set[str] = set()
    pool: list[str] = []
    for raw in window:
        cleaned = clean_candidate(raw)
        if cleaned is None or cleaned in seen:
            continue
        seen.add(cleaned)
        pool.append(cleaned)
    return pool


def trim(text: str) -> str:
    """最后一道长度保险。模型和抓取的评论都可能超长。"""
    value = " ".join((text or "").split())
    return value[:MAX_LEN] if len(value) > MAX_LEN else value


@dataclass(frozen=True, slots=True)
class Draft:
    text: str
    source: str
    note: str = ""

    @property
    def fallback(self) -> bool:
        return self.source == "fallback"


def llm_messages(*, up_name: str, title: str, body: str) -> list[dict[str, str]]:
    """给模型的两条消息。

    要点在 system 里那几条禁令：不提抽奖、不用感叹号堆砌、要贴着正文说一句具体的。
    这些都是"像真人"的具体做法，不是形容词。正文截断到 500 字 —— 抽奖动态真正
    有用的信息都在前几行，全部塞进去只会让模型抓错重点，还更贵。
    """
    system = (
        "你在帮一个人写 B 站动态的转发评论。写出来要像真人随手写的，不要像营销号，"
        "也不要像机器人。硬性要求：\n"
        "1. 20 到 40 个字，一句话为主。\n"
        "2. 不要出现「抽奖」「转发」「求中」「参与」「恭喜」这些词。\n"
        "3. 最多一个感叹号，最多一个表情。\n"
        "4. 必须贴着正文里提到的具体内容说一句（比如提到的作品、角色、时间、奖品），"
        "不要空泛夸「支持」「加油」。\n"
        "5. 正文明确要求评论特定关键词时原样保留；要求回答问题时回答该问题，不要只夸奖品。\n"
        "6. 只输出评论正文本身。不要引号，不要解释，不要换行。"
    )
    excerpt = " ".join((body or "").split())[:500]
    user = f"UP 主：{up_name or '未知'}\n动态标题：{title or '（无标题）'}\n正文：{excerpt}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
