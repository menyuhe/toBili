from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "relay_users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))


class Device(Base):
    __tablename__ = "relay_devices"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[int] = mapped_column(Integer)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen: Mapped[int] = mapped_column(Integer, default=0)
    bili_uid: Mapped[str] = mapped_column(String(32), default="")
    ready: Mapped[bool] = mapped_column(Boolean, default=False)


class Account(Base):
    __tablename__ = "relay_accounts"
    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    bili_uid: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    task_sequence: Mapped[int] = mapped_column(Integer, default=0)
    lease_device: Mapped[str] = mapped_column(String(40), default="")
    lease_hash: Mapped[str] = mapped_column(String(64), default="")
    lease_until: Mapped[int] = mapped_column(Integer, default=0)
    lease_task: Mapped[str] = mapped_column(String(40), default="")
    quarantine: Mapped[bool] = mapped_column(Boolean, default=False)


class Record(Base):
    __tablename__ = "relay_records"
    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    bili_uid: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, index=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    value_json: Mapped[str] = mapped_column(Text, default="{}")


class Receipt(Base):
    __tablename__ = "relay_receipts"
    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    bili_uid: Mapped[str] = mapped_column(String(32), primary_key=True)
    change_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[str] = mapped_column(Text)


class Task(Base):
    __tablename__ = "relay_tasks"
    __table_args__ = (UniqueConstraint("user_id", "bili_uid", "request_key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    bili_uid: Mapped[str] = mapped_column(String(32), index=True)
    request_key: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(40))
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    state: Mapped[str] = mapped_column(String(24), default="queued")
    executor: Mapped[str] = mapped_column(String(40), default="")
    target_device: Mapped[str] = mapped_column(String(40), default="")
    queue_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(String(500), default="等待执行设备")


class LoginAttempt(Base):
    __tablename__ = "relay_login_attempts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer, default=0)
