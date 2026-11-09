import pytest

from tobili.domain.enrollment import Stage
from tobili.runtime.journal import Journal
from tobili.usecases.enroll import Enroll


@pytest.mark.parametrize("text,reason", [
    ("关注并打卡，每日签到送礼", "打卡活动需要手动"),
    ("关注预约抽奖，送出耳机", "未找到可执行的预约入口"),
    ("关注我们，送出耳机", "未识别到可自动执行"),
])
def test_unsupported_primary_action_cannot_succeed_by_only_following(
    repos, clock, gateway, account_id, text, reason
):
    gateway.detail_text = text
    enroll = Enroll(
        gateway=gateway, campaigns=repos.campaigns, enrollments=repos.enrollments,
        journal=Journal(gateway=gateway, intents=repos.intents, clock=clock), clock=clock,
    )
    report = enroll.run(account_id, "unsupported")
    assert report.stage is Stage.BLOCKED and reason in report.reason
    assert not gateway.writes and not gateway.probes
    assert repos.enrollments.get(report.enrollment_id).reason == report.reason
