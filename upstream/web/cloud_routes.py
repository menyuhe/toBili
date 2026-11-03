"""Local desktop bridge. These routes are never mounted on the relay."""
from fastapi import APIRouter
from pydantic import Field

from src.cloud_client import cloud_client
from src.cloud_protocol import StrictModel
from web.api_errors import AppError, ErrorCode

router = APIRouter(prefix="/api/cloud", tags=["cloud"])


class Connect(StrictModel):
    server: str = Field(max_length=300)
    username: str = Field(pattern=r"^[a-zA-Z0-9_\-]{3,64}$")
    password: str = Field(min_length=12, max_length=128)
    device_name: str = Field(min_length=1, max_length=80)
    register_account: bool = Field(default=False, alias="register")
    invite: str = Field(default="", max_length=256)


class Resolve(StrictModel):
    kind: str = Field(max_length=32)
    key: str = Field(max_length=64)
    choice: str = Field(pattern=r"^(local|remote)$")


def call(function, *args):
    try:
        from src.cloud_client import load_connection
        from src.account_context import account_scope
        cfg = load_connection()
        if cfg and cfg.get("bili_uid") and function.__name__ != "bind":
            with account_scope(cfg["bili_uid"]):
                return function(*args)
        return function(*args)
    except (ValueError, RuntimeError) as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc)) from None
    except Exception:
        raise AppError(ErrorCode.VALIDATION_ERROR, "无法连接中转服务器，请检查地址和网络") from None


@router.get("/status")
def status():
    return cloud_client.status()


@router.post("/connect")
def connect(body: Connect):
    return call(cloud_client.connect, body.model_dump(by_alias=True))


@router.post("/bind")
def bind():
    return call(cloud_client.bind)


@router.post("/disconnect")
def disconnect():
    return call(cloud_client.disconnect)


@router.post("/sync")
def sync():
    return call(cloud_client.sync)


@router.get("/conflicts")
def conflicts():
    return {"items": call(cloud_client.conflicts)}


@router.get("/execution")
def execution():
    return call(cloud_client.execution_details)


@router.post("/acknowledge")
def acknowledge():
    return call(cloud_client.acknowledge)


@router.post("/resolve")
def resolve(body: Resolve):
    return call(cloud_client.resolve, body.kind, body.key, body.choice)
