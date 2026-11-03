"""Durable notification outbox. Delivery never controls the business outcome."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlmodel import select

from src.app_paths import user_home
from src.db.models import NotificationDeliveryRow, NotificationCursorRow
from src.db.session import session_scope
from src.log_redact import redact_text
from src.secure_files import write_text_secret

KINDS = {"won": "中奖", "at": "@ 未读增加", "login": "登录失效", "failure": "任务失败", "cleanup": "清理汇总"}
DEFAULTS = {"audio": False, "dingtalk": False, "webhook": "", "sign_secret": "", "prefix": "toBili",
            "events": ["won", "login", "failure", "cleanup"]}
_lock = threading.RLock()
_delivery_lock = threading.Lock()


def settings_path():
    return user_home() / "config" / "notifications.json"


def load_settings() -> dict:
    with _lock:
        try:
            value = json.loads(settings_path().read_text(encoding="utf-8"))
            return {**DEFAULTS, **value}
        except FileNotFoundError:
            return dict(DEFAULTS)


def public_settings() -> dict:
    settings = load_settings()
    return {**{k: settings[k] for k in ("audio", "dingtalk", "prefix", "events")},
            "webhook_hint": "已配置 · ••••••••" if settings["webhook"] else "",
            "secret_hint": "已配置 · ••••••••" if settings["sign_secret"] else "",
            "audio_available": sys.platform == "win32"}


def save_settings(values: dict) -> dict:
    with _lock:
        settings = load_settings()
        for key in ("audio", "dingtalk", "prefix", "events"):
            if key in values:
                settings[key] = values[key]
        for key in ("webhook", "sign_secret"):
            # null means retain; empty string explicitly removes the credential.
            if values.get(key) is not None:
                settings[key] = values[key].strip()
        if set(settings["events"]) - KINDS.keys():
            raise ValueError("通知事件类型无效")
        if settings["webhook"]:
            validate_webhook(settings["webhook"])
        if settings["dingtalk"] and not settings["webhook"]:
            raise ValueError("请先配置钉钉 Webhook")
        write_text_secret(settings_path(), json.dumps(settings, ensure_ascii=False))
        return public_settings()


def validate_webhook(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "oapi.dingtalk.com"
            or parsed.path != "/robot/send" or parsed.fragment
            or not parse_qs(parsed.query).get("access_token")):
        raise ValueError("请填写 https://oapi.dingtalk.com/robot/send?access_token=… 格式的机器人地址")


def signed_params(secret: str, timestamp: int) -> dict:
    if not secret:
        return {}
    value = f"{timestamp}\n{secret}".encode()
    sign = base64.b64encode(hmac.new(secret.encode(), value, hashlib.sha256).digest()).decode()
    return {"timestamp": str(timestamp), "sign": sign}


def send_dingtalk(settings: dict, title: str, body: str) -> None:
    validate_webhook(settings["webhook"])
    # Never include raw exception messages: transport errors can contain the token URL.
    try:
        with httpx.Client(timeout=httpx.Timeout(10, connect=5), follow_redirects=False) as client:
            response = client.post("https://oapi.dingtalk.com/robot/send",
                params={**{k: v[-1] for k, v in parse_qs(urlsplit(settings["webhook"]).query).items()},
                        **signed_params(settings["sign_secret"], int(time.time() * 1000))},
                json={"msgtype": "text", "text": {"content": f"{settings['prefix']} · {title}\n{body}"}})
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get("errcode") != 0:
                raise ValueError("钉钉未接受消息，请检查机器人安全设置及发送频率")
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("钉钉发送失败，请检查网络、Webhook 和安全设置") from None


def play_audio() -> None:
    if sys.platform != "win32":
        raise RuntimeError("音频通知需要 Windows 桌面环境")
    import winsound
    import io
    import math
    import struct
    import wave
    # Built-in two-note PCM sound; independent of browser autoplay and Windows sound aliases.
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        samples = []
        for frequency in (660, 880):
            for i in range(4410):
                envelope = min(i / 300, (4410-i) / 700, 1)
                samples.append(struct.pack("<h", int(6500 * envelope * math.sin(2 * math.pi * frequency * i / 22050))))
        wav.writeframes(b"".join(samples))
    winsound.PlaySound(buffer.getvalue(), winsound.SND_MEMORY)


def observe_value(uid: str, kind: str, value: int, title: str, body: str, *, initial: int = 0):
    """Persist transitions so unchanged unread/expired state never re-notifies after restart."""
    try:
        with _lock:
            with session_scope() as session:
                row = session.get(NotificationCursorRow, (uid, kind))
                if row is None:
                    row = NotificationCursorRow(uid=uid, kind=kind, value=initial)
                    session.add(row)
                increased = value > row.value
                if value != row.value:
                    row.sequence += 1
                row.value = value
                key = f"{kind}:{row.sequence}"
            if increased:
                safe_enqueue(uid, kind, key, title, body)
    except Exception:
        from src.app_logging import get_logger
        get_logger("notifications").warning("通知状态保存失败，将稍后重试")


def enqueue(uid: str, kind: str, key: str, title: str, body: str, *, channel: str | None = None) -> None:
    if str(uid).isdigit():
        body = f"账号 UID {uid}\n{body}"
    settings = load_settings()
    if channel is None and kind not in settings["events"]:
        return
    channels = [channel] if channel else [c for c in ("audio", "dingtalk") if settings[c]]
    now = int(time.time())
    with _lock, session_scope() as session:
        for target in channels:
            if target not in ("audio", "dingtalk"):
                raise ValueError("通知渠道无效")
            if not session.get(NotificationDeliveryRow, (uid, key, target)):
                session.add(NotificationDeliveryRow(uid=uid, event_key=key, channel=target, kind=kind,
                    title=redact_text(title)[:200], body=redact_text(body)[:2000], created_at=now))


def safe_enqueue(uid: str, kind: str, key: str, title: str, body: str) -> None:
    try:
        enqueue(uid, kind, key, title, body)
    except Exception:
        # Notification storage/config errors must never fail the action that emitted them.
        from src.app_logging import get_logger
        get_logger("notifications").warning("通知入队失败，请检查通知配置及数据库")


def recover_deliveries() -> None:
    with session_scope() as session:
        for row in session.exec(select(NotificationDeliveryRow).where(NotificationDeliveryRow.state == "sending")).all():
            row.state = "unknown"
            row.message = "上次发送中断，结果待核实；为避免重复通知不自动重发"


def deliver_pending() -> None:
    if not _delivery_lock.acquire(blocking=False):
        return
    try:
        now = int(time.time())
        with session_scope() as session:
            rows = session.exec(select(NotificationDeliveryRow).where(
                NotificationDeliveryRow.state == "pending", NotificationDeliveryRow.next_attempt <= now
            ).order_by(NotificationDeliveryRow.created_at).limit(20)).all()
            keys = [(r.uid, r.event_key, r.channel) for r in rows]
        for key in keys:
            settings = load_settings()
            with session_scope() as session:
                row = session.get(NotificationDeliveryRow, key)
                if row is None or row.state != "pending":
                    continue
                if row.kind != "test" and not settings[row.channel]:
                    row.state, row.message = "skipped", "渠道已关闭"
                    continue
                row.state = "sending"
                row.attempts += 1
                row.updated_at = int(time.time())
                title, body, attempts = row.title, row.body, row.attempts
            try:
                if key[2] == "audio":
                    play_audio()
                else:
                    send_dingtalk(settings, title, body)
                state, message = "sent", "已播放" if key[2] == "audio" else "已发送"
            except Exception:
                state = "pending" if attempts < 3 and key[2] == "dingtalk" else "failed"
                message = "发送失败，等待重试" if state == "pending" else "发送失败，请检查渠道配置"
            with session_scope() as session:
                row = session.get(NotificationDeliveryRow, key)
                if row:
                    row.state, row.message = state, message
                    row.updated_at = int(time.time())
                    row.next_attempt = row.updated_at + 30 * (2 ** (attempts - 1))
    finally:
        _delivery_lock.release()


def test_notification(uid: str, channel: str) -> str:
    key = "test:" + uuid.uuid4().hex
    if channel == "dingtalk" and not load_settings()["webhook"]:
        raise ValueError("请先保存钉钉配置")
    enqueue(uid, "test", key, "通知测试", "这是一条 toBili 测试通知。", channel=channel)
    return key


def history(uid: str) -> list[dict]:
    with session_scope() as session:
        rows = session.exec(select(NotificationDeliveryRow).where(NotificationDeliveryRow.uid == uid)
            .order_by(NotificationDeliveryRow.created_at.desc()).limit(50)).all()
        return [r.model_dump() for r in rows]
