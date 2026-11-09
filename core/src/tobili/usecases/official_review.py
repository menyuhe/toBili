"""Refresh public component facts without any account interaction writes."""

import time
from dataclasses import replace

from ..domain.campaign import CampaignKind


def refresh(box, account_id, dyn_id):
    local = box.repos.campaigns.by_dyn(dyn_id)
    if not local:
        raise ValueError("活动奖品不存在")
    detail = box.gateway_for(account_id).detail(dyn_id)
    draw = detail.draw
    campaign = replace(
        local[1],
        up_uid=detail.up_uid or local[1].up_uid,
        up_name=detail.up_name or local[1].up_name,
        body_text=detail.text or local[1].body_text,
        published_at=detail.published_at or local[1].published_at,
        seen_at=box.clock.now(),
        draw=draw,
        confidence=1.0 if draw and draw.verified else local[1].confidence,
    )
    if draw:
        campaign = replace(
            campaign,
            kind=CampaignKind.RESERVE
            if draw.business_type == 10
            else CampaignKind.SHARE
            if draw.business_type == 1
            else CampaignKind.UNKNOWN,
        )
    box.repos.campaigns.upsert(campaign)
    with box.db.write() as db:
        db.execute(
            "UPDATE campaign SET draw_checked_at=? WHERE id=?",
            (box.clock.now().isoformat(), local[0]),
        )
    return draw


def pending(box, size=3):
    owner = box.account()
    if not owner:
        return 0
    if float(box.repos.settings.get(owner[0], "tasks.cooldown", "0")) > time.time():
        return 0
    rows = (
        box.db.connect()
        .execute(
            "SELECT dyn_id FROM campaign WHERE NOT EXISTS(SELECT 1 FROM catalog_removed x WHERE x.dyn_id=campaign.dyn_id) AND draw_checked_at='' AND (body_text LIKE '%互动抽奖%' OR kind='reserve') AND NOT EXISTS(SELECT 1 FROM up_block b WHERE b.uid=campaign.up_uid AND b.active=1) ORDER BY id DESC LIMIT ?",
            (size,),
        )
        .fetchall()
    )
    done = 0
    from ..runtime.work_priority import requested

    for row in rows:
        if float(box.repos.settings.get(owner[0], "tasks.cooldown", "0")) > time.time():
            break
        if requested(box, owner[0]):
            break
        with box.lanes.exclusive(owner[0]):
            if requested(box, owner[0]):
                break
            refresh(box, owner[0], row["dyn_id"])
        done += 1
    return done
