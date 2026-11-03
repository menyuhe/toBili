"""Local account schedule settings, shared by Windows and macOS."""
from pydantic import BaseModel, ConfigDict, Field
from src.account_context import scoped_uid
from src.accounts import read_state, write_state

class Period(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    minutes: int = Field(default=180, ge=5, le=10080)

class ScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_discovery: Period = Field(default_factory=Period)
    refresh_all: Period = Field(default_factory=Period)
    refresh_watch: Period = Field(default_factory=Period)
    refresh_status: Period = Field(default_factory=Period)
    participate_pool: Period = Field(default_factory=lambda: Period(minutes=5))
    nonstop: bool = False
    nonstop_hourly_limit: int = Field(default=20, ge=1, le=500)
    nonstop_minutes: int = Field(default=180, ge=5, le=10080)
    maintenance_minutes: int = Field(default=30, ge=5, le=10080)


def load(uid=None):
    uid = uid if uid is not None else scoped_uid()
    return ScheduleConfig.model_validate(read_state(uid, "schedule_config", {}) if uid else {})


def save(config):
    uid = scoped_uid()
    if not uid or not uid.isdigit():
        raise ValueError("请先选择已登录账号")
    write_state(uid, "schedule_config", config.model_dump())
    return config.model_dump()


def plan(config):
    result = {}
    for action in ("refresh_discovery", "refresh_all", "refresh_watch", "refresh_status", "participate_pool"):
        period = getattr(config, action)
        if config.nonstop or period.enabled:
            result["participate_all" if config.nonstop and action == "participate_pool" else action] = (config.nonstop_minutes if config.nonstop and action != "participate_pool" else period.minutes) * 60
    if config.nonstop:
        result["maintenance_cycle"] = config.maintenance_minutes * 60
    return result
