from dataclasses import replace
from datetime import UTC, datetime, timedelta
import pytest
from tobili.adapters.bili.official_draw import component, notice_facts, read_draw
from tobili.adapters.bili.public_sources import opus_item
from tobili.adapters.bili.parser import to_detail, to_feed_item
from tobili.domain.campaign import Campaign, CampaignKind as K
from tobili.domain.draw import DrawFacts
from tobili.domain.extract import deadline_from
from tobili.runtime.journal import Journal
from tobili.usecases.enroll import Enroll


def notice(bid="123", kind=1, **kw):
    return dict(
        lottery_id=100,
        business_id=bid,
        business_type=kind,
        status=0,
        lottery_time=1795574100,
        first_prize=2,
        first_prize_cmt="AMD拉杆箱",
        second_prize=2,
        second_prize_cmt="AMD电动螺丝刀",
        third_prize=20,
        third_prize_cmt="大会员",
        **kw,
    )


def usecase(repos, gateway, clock):
    return Enroll(
        gateway=gateway,
        campaigns=repos.campaigns,
        enrollments=repos.enrollments,
        journal=Journal(gateway=gateway, intents=repos.intents, clock=clock),
        clock=clock,
        standard_actions=True,
    )


def test_official_prizes_and_timestamp_need_no_model():
    facts = notice_facts(notice(), "123", 1)
    assert facts.reward == "AMD拉杆箱 × 2份；AMD电动螺丝刀 × 2份；大会员 × 20份"
    assert facts.deadline == datetime(2026, 11, 25, 2, 35, tzinfo=UTC)


def test_reservation_component_survives_opus_normalization():
    raw = {
        "id_str": "123",
        "modules": [
            {
                "module_content": {
                    "paragraphs": [
                        {
                            "link_card": {
                                "card": {
                                    "reserve": {
                                        "rid": 987,
                                        "desc3": {"text": "预约有奖：周边*3份"},
                                        "button": {"uncheck": {"text": "已结束"}},
                                    }
                                }
                            }
                        }
                    ]
                }
            }
        ],
    }
    item = opus_item(raw)
    assert component(item) == ("987", 10, "预约组件已结束")
    assert to_detail(item).reserve_id == "987"
    assert to_feed_item(item).draw_hint


def test_reservation_uses_rid_and_type_ten_even_when_text_says_interactive():
    class Wire:
        def fetch(self, url, *, query, referer):
            assert query == {"business_id": "987", "business_type": 10}
            return {
                "code": 0,
                "data": notice("987", 10, lottery_result={"first_prize_result": [{"uid": 5}]}),
            }

    item = {
        "id_str": "123",
        "modules": {
            "module_dynamic": {
                "desc": {"text": "互动抽奖，关注转发"},
                "additional": {"reserve": {"rid": "987"}},
            }
        },
    }
    facts = read_draw(Wire(), item)
    assert facts.business_type == 10 and facts.state == "ended"


def test_wrong_business_identity_is_rejected():
    with pytest.raises(ValueError):
        notice_facts(notice(), "999", 1)
    with pytest.raises(ValueError):
        notice_facts(notice(), "123", 10)


@pytest.mark.parametrize(
    "state,deadline",
    [("ended", None), ("active", datetime(2026, 9, 1, tzinfo=UTC)), ("unverified", None)],
)
def test_official_closed_or_unknown_never_writes(
    repos, gateway, clock, account_id, state, deadline
):
    original = gateway.detail("123")
    gateway.detail = lambda dyn: replace(
        original,
        text="关注并转发本动态抽奖",
        deadline=deadline,
        draw=DrawFacts("123", 1, "键盘", deadline, state, "官方状态"),
    )
    result = usecase(repos, gateway, clock).run(account_id, "123")
    assert not gateway.writes and not result.wrote_anything
    assert repos.campaigns.get(result.campaign_id)["draw_state"] == state


def test_stored_expiry_stops_before_refresh_could_erase_it(repos, gateway, clock, account_id):
    cid = repos.campaigns.upsert(
        Campaign("123", "42", "奖品", K.SHARE, deadline=clock.now() - timedelta(days=40))
    )
    gateway.detail = lambda _: pytest.fail("An expired entry must not proceed to writing")
    assert usecase(repos, gateway, clock).run(account_id, "123").skipped
    assert not gateway.writes and repos.campaigns.get(cid)["deadline_at"]


def test_refresh_without_time_preserves_existing_date_and_author(repos, clock):
    item = Campaign(
        "123", "42", "奖品", K.SHARE, up_name="作者", deadline=clock.now() + timedelta(days=2)
    )
    cid = repos.campaigns.upsert(item)
    repos.campaigns.upsert(replace(item, up_name="", up_uid="", deadline=None))
    row = repos.campaigns.get(cid)
    assert (
        row["deadline_at"] == item.deadline.isoformat()
        and row["up_uid"] == "42"
        and row["up_name"] == "作者"
    )


def test_official_correction_preserves_manual_prize_and_date(repos, clock):
    item = Campaign(
        "123", "42", "奖品", K.SHARE, deadline=clock.now() + timedelta(days=2), reward="手动奖品"
    )
    cid = repos.campaigns.upsert(item)
    with repos.db.write() as db:
        db.execute("CREATE TABLE pro_edits(dyn TEXT,accepted TEXT)")
        db.execute("INSERT INTO pro_edits VALUES('123','{\"deadline_at\":\"manual\"}')")
        db.execute("UPDATE campaign SET reward_locked=1 WHERE id=?", (cid,))
    repos.campaigns.upsert(replace(item, draw=notice_facts(notice(), "123", 1)))
    row = repos.campaigns.get(cid)
    assert row["reward"] == "手动奖品" and row["deadline_at"] == item.deadline.isoformat()
    assert row["draw_business_type"] == 1


def test_ended_without_time_not_an_automatic_candidate(repos, clock, account_id):
    repos.campaigns.upsert(
        Campaign(
            "123", "42", "奖品", K.SHARE, confidence=1, draw=DrawFacts("123", 1, state="ended")
        )
    )
    assert not repos.campaigns.candidates(account_id, now=clock.now(), floor=0.8, limit=10)


def test_old_september_date_is_not_rolled_to_next_year():
    assert deadline_from("9月1日18:00开奖", now=datetime(2026, 11, 9, tzinfo=UTC)).year == 2026


def test_winner_announcement_never_participates(repos, gateway, clock, account_id):
    gateway.detail_text = "抽奖已结束，获奖名单如下：恭喜几位中奖者。关注转发抽奖"
    result = usecase(repos, gateway, clock).run(account_id, "123")
    assert result.skipped and not gateway.writes


def test_expired_entry_does_not_prevent_next_entry(repos, gateway, clock, account_id):
    repos.campaigns.upsert(
        Campaign("123", "42", "奖品", K.SHARE, deadline=clock.now() - timedelta(days=1))
    )
    gateway.detail_text = "关注并转发本动态抽鼠标"
    runner = usecase(repos, gateway, clock)
    assert runner.run(account_id, "123").skipped
    assert runner.run(account_id, "124").wrote_anything


def test_official_scan_ignores_model_and_has_confidence(repos, gateway, clock):
    from tobili.usecases.scan_feeds import ScanFeeds
    from tobili.ports.models import FeedItem

    facts = notice_facts(notice(), "123", 1)
    original = gateway.detail("123")
    gateway.detail = lambda _: replace(original, text="奖品介绍，预约直播。", draw=facts)

    class Model:
        def extract(self, *a, **k):
            pytest.fail("Official components must not use LLM")

    scan = ScanFeeds(
        gateway=gateway, feeds=repos.feeds, campaigns=repos.campaigns, clock=clock, model=Model()
    )
    result = scan._build(FeedItem("123", "42", "", draw_hint=True), now=clock.now(), label="test")
    assert result.kind == K.SHARE and result.confidence == 1 and result.reward == facts.reward
    cid = repos.campaigns.upsert(result)
    assert repos.campaigns.get(cid)["draw_business_type"] == 1


def test_ended_cannot_be_added_to_queue(repos, clock, account_id):
    from tobili.usecases.pool import ManagePool

    cid = repos.campaigns.upsert(
        Campaign(
            "123",
            "42",
            "奖品",
            K.RESERVE,
            draw=DrawFacts("987", 10, state="ended", reason="官方已公布获奖名单"),
        )
    )
    pool = ManagePool(
        pool=repos.pool, campaigns=repos.campaigns, enrollments=repos.enrollments, clock=clock
    )
    assert "获奖名单" in pool._why_not(account_id, cid, clock.now())


def test_reservation_executes_reservation_instead_of_default_repost(
    repos, gateway, clock, account_id
):
    from tobili.domain.action import ActionKind

    original = gateway.detail("123")
    facts = notice_facts(notice("987", 10), "987", 10)
    gateway.detail = lambda _: replace(
        original, text="预约有奖，周边三份", reserve_id="987", draw=facts
    )
    result = usecase(repos, gateway, clock).run(account_id, "123")
    assert result.wrote_anything
    assert ActionKind.RESERVE in [a.kind for a in gateway.writes]
    assert not any(a.kind in (ActionKind.SHARE, ActionKind.COMMENT) for a in gateway.writes)


def test_component_label_and_closed_filter(repos, clock, account_id):
    facts = notice_facts(notice(), "123", 1)
    repos.campaigns.upsert(
        Campaign("123", "42", "奖品", K.SHARE, draw=replace(facts, state="ended", deadline=None))
    )
    assert repos.campaigns.catalog(account_id, kind="interactive")["total"] == 1
    assert repos.campaigns.catalog(account_id, availability="open")["total"] == 0


def test_old_false_next_year_date_cannot_override_fresh_expired_text(
    repos, gateway, clock, account_id
):
    cid = repos.campaigns.upsert(
        Campaign("123", "42", "奖品", K.SHARE, deadline=datetime(2027, 9, 1, tzinfo=UTC))
    )
    original = gateway.detail("123")
    gateway.detail = lambda _: replace(
        original,
        text="关注转发抽奖，9月1日18:00开奖",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    result = usecase(repos, gateway, clock).run(account_id, "123")
    assert result.skipped and not gateway.writes
    assert repos.campaigns.get(cid)["draw_state"] == "ended"
    assert repos.enrollments.get(result.enrollment_id).stage.value == "blocked"


def test_missing_previously_known_component_is_not_a_regular_post(
    repos, gateway, clock, account_id
):
    repos.campaigns.upsert(
        Campaign("123", "42", "奖品", K.RESERVE, draw=notice_facts(notice("987", 10), "987", 10))
    )
    gateway.detail_text = "关注并转发本动态抽鼠标"
    result = usecase(repos, gateway, clock).run(account_id, "123")
    assert not gateway.writes and "组件未能读取" in result.reason


def test_official_minute_is_not_lost_to_text_date_only_review(repos,clock):
    from tobili.usecases.deadline_review import review_existing
    facts=notice_facts(notice(),'123',1)
    cid=repos.campaigns.upsert(Campaign('123','42','奖品',K.SHARE,body_text='11月25日开奖',published_at=clock.now(),draw=facts))
    review_existing(repos.db,clock.now())
    row=repos.db.connect().execute('SELECT * FROM deadline_check WHERE campaign_id=?',(cid,)).fetchone()
    assert row['state']=='official' and row['precision']=='minute'
    assert repos.campaigns.get(cid)['deadline_at']==facts.deadline.isoformat()


def test_topic_named_interactive_draw_is_not_an_official_widget():
    from tobili.adapters.bili.official_draw import has_component
    item={'id_str':'123','modules':{'module_dynamic':{'desc':{'text':'#互动抽奖# 关注转发抽奖','rich_text_nodes':[{'type':'RICH_TEXT_NODE_TYPE_TOPIC','text':'互动抽奖'}]}}}}
    class NoRequest:
        def fetch(self,*a,**k):pytest.fail('Topic must not request official notice')
    assert not has_component(item)
    assert read_draw(NoRequest(),item) is None


def test_actual_lottery_rich_node_reads_notice():
    item={'id_str':'123','modules':{'module_dynamic':{'desc':{'rich_text_nodes':[{'type':'RICH_TEXT_NODE_TYPE_LOTTERY','text':'互动抽奖'}]}}}}
    class Wire:
        def fetch(self,*a,**k):return {'code':0,'data':notice()}
    assert read_draw(Wire(),item).business_type==1
