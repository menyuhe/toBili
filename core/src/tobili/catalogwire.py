"""活动资料交换的显式字段清单。不能序列化本机库行或账号资料。"""

from datetime import UTC
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Listing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dyn_id: str = Field(pattern=r"^[0-9]{1,24}$")
    up_uid: str = Field(default="", pattern=r"^[0-9]{0,24}$")
    title: str = Field(max_length=500)
    up_name: str = Field(default="", max_length=120)
    reward: str = Field(default="", max_length=500)
    kind: Literal["share", "comment", "reserve", "check_in", "unknown"] = "unknown"
    deadline_at: AwareDatetime | None = None

    def values(self) -> dict:
        fields = self.model_dump()
        if self.deadline_at:
            fields["deadline_at"] = self.deadline_at.astimezone(UTC).isoformat(timespec="seconds")
        return fields
