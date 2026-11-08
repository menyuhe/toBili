"""Public metadata only. Never accept arbitrary database rows."""

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class PublicPrize(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dyn_id: str = Field(pattern=r"^[0-9]{1,24}$")
    up_uid: str = Field(default="", pattern=r"^[0-9]{0,24}$")
    up_name: str = Field(default="", max_length=120)
    reward: str = Field(min_length=1, max_length=2000)
    kind: Literal["share", "comment", "reserve", "check_in", "unknown"] = "unknown"
    deadline_at: AwareDatetime | None = None
    deadline_date: date | None = None
    published_at: AwareDatetime | None = None
