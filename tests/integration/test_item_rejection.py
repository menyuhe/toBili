from types import SimpleNamespace

import pytest
from test_queue_review import seed
from test_taskbook_api import config
from test_taskbook_api import setup as setup

from tobili.domain.errors import Fault, RemoteError
from tobili.domain.pool import PoolState
from tobili.domain.receipt import Receipt
from tobili.runtime.journal import Journal
from tobili.usecases.enroll import Enroll


@pytest.mark.parametrize(
    "fault,message",
    [
        (Fault.REMOTE_BUSINESS, "活动已结束"),
        (Fault.REMOTE_BUSINESS, "评论区已关闭"),
        (Fault.PARSE, "预约入口结构无效"),
    ],
)
def test_item_rejection_does_not_disable_schedule_or_block_following_item(
    setup, gateway, fault, message
):
    client, box = setup
    seed(box, 2)
    gateway.detail_text = "评论抽奖，送出一台电脑"
    real = Enroll(
        gateway=gateway,
        campaigns=box.repos.campaigns,
        enrollments=box.repos.enrollments,
        journal=Journal(gateway=gateway, intents=box.repos.intents, clock=box.clock),
        clock=box.clock,
    )

    def run(owner, dyn, **kw):
        gateway.receipt = (
            Receipt.rejected(fault, message) if dyn == "99000" else Receipt.success("reply")
        )
        return real.run(owner, dyn, **kw)

    box._enroll = lambda _: SimpleNamespace(run=run)
    body = config(client, running=True, apply=True, confirmed=True, selection="queue", limit=2)
    assert client.put("/api/tasks", json=body).is_success
    before = box.tasks.config(1)
    report = box.tasks.run(1, "participate", before, apply=True, scheduled=True)
    assert report["ok"] and report["written"] == 1
    assert box.tasks.config(1) == before and not box.repos.pool.paused(1)[0]
    assert box.repos.pool.states(1)[0].state is PoolState.REVIEW
    assert message in box.repos.pool.states(1)[0].message
    assert len(gateway.writes) == 2


def test_missing_detail_is_reviewed_and_not_a_global_failure(setup, gateway):
    client, box = setup
    seed(box, 2)
    gateway.detail_text = "评论抽奖，送出一台电脑"
    original = gateway.detail

    def detail(dyn):
        if dyn == "99000":
            raise RemoteError(Fault.REMOTE_BUSINESS, "动态不存在", code=4101105)
        return original(dyn)

    gateway.detail = detail
    real = Enroll(
        gateway=gateway,
        campaigns=box.repos.campaigns,
        enrollments=box.repos.enrollments,
        journal=Journal(gateway=gateway, intents=box.repos.intents, clock=box.clock),
        clock=box.clock,
    )
    box._enroll = lambda _: real
    result = box.tasks.run(1, "participate", config(client, selection="queue", limit=2), apply=True)
    assert result["ok"] and result["written"] == 1
    assert box.repos.pool.states(1)[0].state is PoolState.REVIEW


@pytest.mark.parametrize("fault", [Fault.AUTH, Fault.RISK])
def test_account_failure_still_stops_before_next_item(setup, gateway, fault):
    client, box = setup
    seed(box, 2)
    gateway.detail_text = "评论抽奖，送出一台电脑"
    gateway.receipt = Receipt.rejected(fault, "需要处理账号状态")
    real = Enroll(
        gateway=gateway,
        campaigns=box.repos.campaigns,
        enrollments=box.repos.enrollments,
        journal=Journal(gateway=gateway, intents=box.repos.intents, clock=box.clock),
        clock=box.clock,
    )
    box._enroll = lambda _: real
    result = box.tasks.run(1, "participate", config(client, selection="queue", limit=2), apply=True)
    assert not result["ok"] and len(gateway.writes) == 1
