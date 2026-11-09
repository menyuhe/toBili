"""Explicit participation requirements; never treat background product text as instructions."""

import re

from .campaign import CampaignKind


def is_roundup(text: str) -> bool:
    """Recognize a directory of other draws, not a draw on this post.

    Require a roundup heading plus a structured list of linked draws. A normal
    draw mentioning a product collection or several prizes must stay eligible.
    """
    heading = re.search(
        r"(?:精选\s*\d+\s*个抽奖活动|本期\s*\d+\s*个活动\s*[·•|｜].{0,8}开奖区间)", text
    )
    links = set(re.findall(r"(?:t\.bilibili\.com/|bilibili\.com/opus/)(\d+)", text))
    return bool(heading and len(links) >= 2 and len(re.findall(r"开奖\s*[:：]", text)) >= 2)


def requires_check_in(text: str) -> bool:
    """A repeated check-in task, not a greeting or a product's daily-use copy."""
    return bool(
        re.search(
            r"(?:连续|累计|坚持|每天|每日)[^，,。；;！!\n]{0,10}(?:签到|打卡)|"
            r"(?:签到|打卡)[^，,。；;！!\n]{0,10}(?:[0-9一二三四五六七八九十]+[天次日]|积分|任务页)|"
            r"(?:前往|进入|打开).{0,15}(?:签到|打卡)(?:页面|专区|任务)",
            text,
        )
    )


def required_interactions(text: str, kind: CampaignKind) -> tuple[bool, bool, bool]:
    # An action chain is evidence of a requirement; a hashtag alone is not.
    chain = re.compile(
        r"(?:关注|关赞转评|评论|转发|点赞|转评赞)(?:[\s，,、并和及+＋➕【】~～]*)(?:关注|评论|转发|点赞|转评赞)"
    )
    clauses = [line for line in re.split(r"[\n。！!]", text) if chain.search(line)]
    joined = "\n".join(clauses)
    share = (
        kind is CampaignKind.SHARE or "转发" in joined or "转评赞" in joined or "关赞转评" in text
    )
    comment = (
        kind is CampaignKind.COMMENT or "评论" in joined or "转评赞" in joined or "关赞转评" in text
    )
    like = "点赞" in joined or "转评赞" in joined or "关赞转评" in text
    return share, comment, like


def manual_requirement(text: str, comment_type: int) -> str:
    if "充电抽奖" in text and "转发抽奖" in text:
        return "正文含充电和转发两组抽奖，需手动确认参与范围"
    if re.search(r"(?:一键|三连|投币).{0,12}(?:参与|评论|视频)|三连评论|一键三[～~]", text):
        return "活动要求视频三连或投币，需在 B 站手动完成"
    video = re.search(r"视频.{0,8}(?:评论区|底下留言)|评论本条视频", text)
    if video and comment_type != 1:
        return "活动要求在视频评论区参与，未取得对应视频入口，需手动核对"
    return ""
