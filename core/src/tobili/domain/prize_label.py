"""Compact prize labels, leaving evidence and participation requirements elsewhere."""

import re


def concise_reward(text: str) -> str:
    parts = []
    for item in re.split(r"[；;\n]+", text or ""):
        s = item.strip()
        # Only strip an explicit draw preamble; never remove product/model digits.
        s = re.sub(
            r"^(?:将|会|我们将|本次将)?\s*(?:抽取|抽出|揪出|抽|选出)\s*[\d一二两三四五六七八九十百]+\s*(?:位|名|个)\s*(?:(?:幸运)?(?:小伙伴|伙伴|董事|粉丝|用户|玩家|朋友|观众|宝子|幸运儿))?\s*(?:送出|赠送|获得|送|获得奖品|可获得)?\s*[:：]?",
            "",
            s,
        )
        s = re.sub(r"^(?:送出|赠送|奖品[：:]|奖[：:])\s*", "", s).strip()
        s = s.replace("【", "").replace("】", " ").replace("京东卡", "京东e卡").strip()
        s = re.sub(r"(?<![A-Za-z])[×*xX]\s*(\d+)\s*(?:份|个|张|台|套|件|只)?$", r" ×\1", s)
        if not re.search(r"×\d+$", s):
            s = re.sub(r"\s+(\d+)\s*(?:份|个|张|台|套|件|只)$", r" ×\1", s)
        s = re.sub(r"\s+", " ", s).strip(" ，,。:：")
        if s and s not in parts:
            parts.append(s)
    return "；".join(parts)
