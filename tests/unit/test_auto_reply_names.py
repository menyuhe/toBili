import json

from tobili.adapters.bili import endpoints
from tobili.adapters.bili.reader import DynamicReader
from tobili.domain.inbox import annotate
from tobili.usecases.inbox import Inbox


def test_platform_auto_reply_and_cached_names_do_not_trigger_prize(repos, clock, account_id):
    calls = []

    class Wire:
        def fetch(self, url, **kw):
            calls.append(url)
            if url == endpoints.MSG_SESSIONS:
                assert kw["query"]["unfollow_fold"] == 0
                return {
                    "code": 0,
                    "data": {
                        "session_list": [
                            {
                                "talker_id": 647359920,
                                "last_msg": {
                                    "content": json.dumps(
                                        {"content": "欢迎你，抽奖奖品，联系回复"}
                                    ),
                                    "msg_source": 8,
                                },
                            },
                            {
                                "talker_id": 2,
                                "account_info": {"name": "真实开奖UP"},
                                "last_msg": {
                                    "content": json.dumps(
                                        {"content": "恭喜你中奖，请提供收货地址"}
                                    ),
                                    "msg_source": 1,
                                },
                            },
                        ]
                    },
                }
            return {"code": 0, "data": {"card": {"name": "maono闪克"}}}

    reader = DynamicReader(Wire())
    rows = reader._chats(1)
    assert rows[0].who == "maono闪克" and rows[0].automatic and not annotate(rows[0]).prize
    assert rows[1].who == "真实开奖UP" and annotate(rows[1]).prize
    reader._chats(1)
    assert sum("/card" in c for c in calls) == 1

    class Gateway:
        def inbox(self, **kw):
            return rows

        def unread(self):
            raise RuntimeError("unread unavailable")

    view = Inbox(gateway=Gateway(), seen=repos.inbox, clock=clock).fetch(account_id)
    assert view.counts["prize"] == 1
    assert next(r for r in view.items if r.sender_uid == "647359920").automatic
