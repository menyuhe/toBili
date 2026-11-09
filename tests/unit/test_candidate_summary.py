from datetime import timedelta

from tobili.domain.campaign import Campaign, CampaignKind
from tobili.domain.enrollment import Stage


def test_candidate_summary_groups_category_not_enrollment_reason(repos, clock, account_id):
    for dyn, reason in [("1", "评论核查中"), ("2", "转发核查中"), ("3", "评论核查中")]:
        repos.campaigns.upsert(
            Campaign(
                dyn_id=dyn,
                up_uid="2002",
                title="键盘",
                kind=CampaignKind.SHARE,
                confidence=0.95,
                deadline=clock.now() + timedelta(days=10),
                seen_at=clock.now(),
            )
        )
        cid = repos.campaigns.by_dyn(dyn)[0]
        eid = repos.enrollments.create(account_id, cid, now=clock.now())
        repos.enrollments.move(eid, Stage.BLOCKED, now=clock.now(), reason=reason)
    note = repos.campaigns.no_candidate_reason(account_id, now=clock.now(), floor=0.8)
    assert "已有参与记录 3 条" in note
