from pydantic import BaseModel, ConfigDict, Field


class CatalogActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dynamic_id: str = Field(pattern=r"^[1-9][0-9]{0,24}$")
    title: str = Field(default="", max_length=1000)
    lottery_type: str = Field(default="", max_length=30)
    lottery_time: int | None = Field(default=None, ge=0)
    sender_uid: str = Field(default="", max_length=32)
    business_id: str = Field(default="", max_length=32)
    business_type: str = Field(default="", max_length=10)
    draw_status: str = Field(default="active", max_length=30)


class CatalogBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[CatalogActivity] = Field(max_length=100)


class CatalogIds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(max_length=500)


class RepostReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uid: str = Field(pattern=r"^[1-9][0-9]{0,24}$")
    dynamic_id: str = Field(pattern=r"^[1-9][0-9]{0,24}$")
    repost_id: str = Field(pattern=r"^[1-9][0-9]{0,24}$")


class RepostBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[RepostReference] = Field(max_length=100)
