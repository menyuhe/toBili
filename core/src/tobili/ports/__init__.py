"""端口层：对外部世界的形状描述。

规则：这里只出现 `Protocol` 与值对象，不出现任何具体实现、重试策略或库名。
"""

from .bili import Authenticator, Gateway, Prober, Reader, Writer
from .collab import CatalogFeed, Clock, Composer, Events, Notifier, SecretStore
from .models import (
    AccountProfile,
    CampaignDetail,
    FeedItem,
    FeedSlice,
    QrProgress,
    QrTicket,
)
from .repo import (
    AccountRepo,
    CampaignRepo,
    EnrollmentRepo,
    FeedRepo,
    IntentRepo,
    SettingRepo,
    TidyRepo,
    VerdictRepo,
)

__all__ = [
    "AccountProfile",
    "AccountRepo",
    "Authenticator",
    "CampaignDetail",
    "CampaignRepo",
    "CatalogFeed",
    "Clock",
    "Composer",
    "EnrollmentRepo",
    "Events",
    "FeedItem",
    "FeedRepo",
    "FeedSlice",
    "Gateway",
    "IntentRepo",
    "Notifier",
    "Prober",
    "QrProgress",
    "QrTicket",
    "Reader",
    "SecretStore",
    "SettingRepo",
    "TidyRepo",
    "VerdictRepo",
    "Writer",
]
