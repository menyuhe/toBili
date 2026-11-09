from tobili.domain.campaign import CampaignKind
from tobili.domain.extract import kind_from
from tobili.domain.participation import required_interactions,manual_requirement


def test_repost_requirement_outranks_unrelated_comment_invitation():
    text='互动抽奖。最近做过什么梦？来评论区说说吧！\n▶转发抽奖◀\n关注@交错战线 并转发本动态，我们抽取三位送色纸'
    kind=kind_from(text)
    assert kind is CampaignKind.SHARE
    assert required_interactions(text,kind)==(True,False,False)


def test_combined_actions_are_not_reduced_to_only_comment():
    text='关注@打个彼方，点赞、评论、转发此动态'
    assert required_interactions(text,kind_from(text))==(True,True,True)


def test_video_and_charge_conditions_are_explicit_manual_reasons():
    assert '视频' in manual_requirement('在横测视频底下留言即可',11)
    assert not manual_requirement('在视频评论区抽一位',1)
    assert '三连' in manual_requirement('三连评论本条视频即可参与抽奖',1)
    assert '两组' in manual_requirement('充电抽奖抽手办，转发抽奖抽背包',11)
