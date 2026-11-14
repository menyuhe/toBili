"""Public platform facts, independent of any account's participation result."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DrawFacts:
    business_id: str = ""
    business_type: int = 0
    reward: str = ""
    deadline: datetime | None = None
    state: str = "unverified"
    reason: str = ""
    # 官方依据从哪来，决定这条能不能进"可写进稿子"的池子：
    #   component  动态自带抽奖组件（互动/预约/充电），组件本身即官方登记
    #   notice     没有组件，但官方公告接口确认过
    #   ""         两者都没有，是正文推出来的线索
    origin: str = ""

    @property
    def verified(self):
        return self.state in ("active", "ended")
