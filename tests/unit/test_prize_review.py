import json
from datetime import timedelta
from types import SimpleNamespace

from tobili.domain.campaign import Campaign, CampaignKind
from tobili.runtime.events import EventBus
from tobili.usecases.prize_review import review_batch
from tobili.usecases.scan_model import ScanModel


def test_retail_contents_are_not_lottery_prizes():
    from tobili.usecases.prize_review import valid_reward

    body = "礼盒含键盘、鼠标，限量500份。11月23日抽5位玩家获得游戏礼包码及挂件。"
    quote = "11月23日抽5位玩家获得游戏礼包码及挂件。"
    assert (
        valid_reward(dict(rewards=["游戏礼包码", "挂件"], evidence=quote), body)
        == "游戏礼包码；挂件"
    )
    assert not valid_reward(dict(rewards=["键盘", "鼠标"], evidence=quote), body)
    assert not valid_reward(dict(rewards=["键盘"], evidence="抽送键盘"), body)


def test_model_rejection_does_not_resurrect_heuristic_prize(repos):
    from datetime import UTC, datetime

    from tobili.ports.models import FeedItem
    from tobili.usecases.scan_model import facts_for

    model = ScanModel(
        SimpleNamespace(chat=lambda *a, **k: '{"reward":""}', describe=lambda: {}), repos.db
    )
    item = FeedItem(dyn_id="41", up_uid="7", text="奖品键盘是宣传图，实际活动取消")
    assert facts_for(item, datetime.now(UTC), model)[2] == ""


def test_prizes_are_evidence_bound_and_survive_ordinary_scans(repos, clock):
    cid = repos.campaigns.upsert(
        Campaign(
            dyn_id="99",
            up_uid="7",
            title="送给你",
            reward="送给你",
            body_text="奖品：机械键盘2把",
            seen_at=clock.now(),
        )
    )
    llm = SimpleNamespace(
        ready=lambda: True,
        chat=lambda *a, **k: json.dumps(
            {"items": [{"id": cid, "rewards": ["机械键盘2把"], "evidence": "奖品：机械键盘2把"}]},
            ensure_ascii=False,
        ),
    )
    box = SimpleNamespace(db=repos.db, repos=repos, llm=lambda: llm, clock=clock, bus=EventBus())
    assert review_batch(box) == 1
    assert repos.campaigns.get(cid)["reward"] == "机械键盘2把"
    repos.campaigns.upsert(
        Campaign(
            dyn_id="99",
            up_uid="7",
            title="标题",
            reward="幸运玩家",
            body_text="奖品：机械键盘2把",
            seen_at=clock.now(),
        )
    )
    assert repos.campaigns.get(cid)["reward"] == "机械键盘2把"
    assert review_batch(box) == 0
    repos.campaigns.upsert(
        Campaign(
            dyn_id="99", up_uid="7", title="标题", body_text="奖品：鼠标一只", seen_at=clock.now()
        )
    )
    assert repos.campaigns.get(cid)["reward_status"] == "pending"
    assert review_batch(box) == 1
    assert (
        repos.campaigns.get(cid)["reward_status"] == "unknown"
        and repos.campaigns.get(cid)["reward"] == ""
    )


def test_scan_model_has_no_three_call_cap(repos):
    calls = []

    def chat(messages, **kw):
        calls.append(1)
        return json.dumps(
            {"reward": messages[-1]["content"], "reward_evidence": messages[-1]["content"]},
            ensure_ascii=False,
        )

    model = ScanModel(SimpleNamespace(chat=chat, describe=lambda: {"model": "fixture"}), repos.db)
    for n in range(8):
        assert model.read(f"鼠标{n}")["reward"] == f"鼠标{n}"
    assert len(calls) == 8


def test_prize_result_does_not_overwrite_new_source_text(repos, clock):
    cid = repos.campaigns.upsert(
        Campaign(dyn_id="changed", up_uid="7", title="抽奖", body_text="键盘")
    )

    def chat(*args, **kwargs):
        repos.campaigns.upsert(
            Campaign(dyn_id="changed", up_uid="7", title="抽奖", body_text="鼠标")
        )
        return json.dumps({"items": [{"id": cid, "rewards": ["键盘"]}]})

    llm = SimpleNamespace(ready=lambda: True, chat=chat)
    box = SimpleNamespace(db=repos.db, repos=repos, llm=lambda: llm, clock=clock, bus=EventBus())
    review_batch(box)
    result = repos.campaigns.get(cid)
    assert result["reward_status"] == "pending"
    assert result["body_text"] == "鼠标"
    assert result["reward"] != "键盘"


def test_catalog_multiselect_time_search_and_publication_order(repos, clock, account_id):
    now = clock.now()
    for index, kind in enumerate([CampaignKind.SHARE, CampaignKind.COMMENT, CampaignKind.RESERVE]):
        repos.campaigns.upsert(
            Campaign(
                dyn_id=str(100 + index),
                up_uid="7",
                title="抽奖",
                reward="键盘",
                kind=kind,
                published_at=now + timedelta(hours=index),
                seen_at=now,
            )
        )
    rows = repos.campaigns.catalog(
        account_id, kind="share,comment", statuses="unjoined", keyword="键盘", after=now.isoformat()
    )
    assert rows["total"] == 2 and [r["dyn_id"] for r in rows["items"]] == ["101", "100"]
    assert (
        repos.campaigns.catalog(account_id, after=(now + timedelta(hours=3)).isoformat())["total"]
        == 0
    )
