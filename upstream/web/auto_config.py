"""定时任务配置。"""

from __future__ import annotations

REFRESH_HOURS = frozenset({0, 3, 6, 9, 12, 15, 18, 21})
POOL_MINUTES = frozenset(range(5, 60, 5))

ALLOWED_CLICK_ACTIONS = frozenset(
    {
        "refresh_all",
        "refresh_discovery",
        "refresh_watch",
        "refresh_status",
        "participate_pool",
        "maintenance_cycle",
        "participate_all",
    }
)

ACTION_LABELS = {
    "refresh_discovery": "B站话题发现",
    "refresh_all": "一键更新活动链接",
    "refresh_watch": "更新监控用户动态",
    "refresh_status": "刷新任务状态",
    "participate_pool": "预选池参与",
    "participate_all": "活动列表挂机参与",
    "maintenance_cycle": "开奖核查与安全清理",
}

JOB_POLL_INTERVAL_SEC = 2.0
JOB_POLL_TIMEOUT_SEC = 6 * 60 * 60
