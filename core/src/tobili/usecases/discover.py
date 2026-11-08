"""按关键词找订阅源。

能力边界写在名字里：这里找的是"人"，不是"活动"。搜索接口不返回动态，所以不能
拿搜索结果直接去参与 —— 那会去做一套根本不存在的动作（给视频点赞、给专栏转发）。

产出默认只是候选列表。加进订阅源要显式 `apply`：自动订阅十个关键词搜出来的号，
明天就会有一堆无关动态涌进库里，而且没有人记得它们是怎么来的。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.discovery import RankedUser, rank
from ..ports.bili import Reader
from ..runtime.redact import safe_exception
from ..usecases.manage_feeds import ManageFeeds


@dataclass(frozen=True, slots=True)
class DiscoverReport:
    keyword: str
    picked: tuple[RankedUser, ...] = field(default=())
    added: tuple[int, ...] = field(default=())
    problems: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.problems


class DiscoverSources:
    def __init__(self, gateway: Reader, feeds: ManageFeeds) -> None:
        self._gateway = gateway
        self._feeds = feeds

    def run(
        self,
        account_id: int,
        keyword: str,
        *,
        limit: int = 10,
        apply: bool = False,
    ) -> DiscoverReport:
        word = keyword.strip()
        if not word:
            return DiscoverReport(keyword="", problems=("关键词不能为空",))

        try:
            found = self._gateway.search_users(word, limit=max(limit, 20))
        except Exception as exc:
            return DiscoverReport(keyword=word, problems=(safe_exception(exc),))

        picked = rank(found, word, limit=limit)
        if not apply:
            return DiscoverReport(keyword=word, picked=tuple(picked))

        added: list[int] = []
        problems: list[str] = []
        for item in picked:
            try:
                added.append(
                    self._feeds.add(
                        account_id,
                        kind="space",
                        target=item.uid,
                        label=item.user.name,
                    )
                )
            except Exception as exc:  # 一个加不进去不该中断其余
                problems.append(f"{item.user.name}：{safe_exception(exc)}")
        return DiscoverReport(
            keyword=word, picked=tuple(picked), added=tuple(added), problems=tuple(problems)
        )
