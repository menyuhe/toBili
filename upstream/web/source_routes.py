from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from src import source_registry
from web.maintenance_routes import checked_call
from web.api_errors import AppError, ErrorCode

router = APIRouter(prefix="/api/collections", tags=["stable"])


class SourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    mid: str = Field(pattern=r"^[1-9][0-9]{0,15}$")
    backup: bool = False


def idle():
    from web.job_runner import runner
    if runner.get_status().state == "running":
        raise AppError(ErrorCode.JOB_BUSY, "请等待当前任务完成后修改合集")


@router.get("")
def list_sources():
    return {"items": source_registry.catalog()}


@router.post("")
def add_source(request: SourceRequest):
    idle()
    return checked_call(source_registry.save_source, request.model_dump())


class SourceOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ids: list[str]


@router.put("/order")
def reorder_sources(request: SourceOrderRequest):
    idle()
    return {"items": checked_call(source_registry.reorder_sources, request.source_ids)}


@router.put("/{source_id}")
def update_source(source_id: str, request: SourceRequest):
    idle()
    return checked_call(source_registry.save_source, request.model_dump(), source_id)


@router.delete("/{source_id}")
def delete_source(source_id: str):
    idle()
    checked_call(source_registry.remove_source, source_id)
    return {"ok": True}
