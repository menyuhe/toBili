"""Desktop relay session, sync loop and single-executor job boundary."""
from __future__ import annotations

import json
import os
import platform
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from src.cloud_protocol import canonical, task_params
from src.cloud_replica import Ledger, apply_record, snapshot, synchronize
from src.task_control import check_cancelled

_config_lock = threading.RLock()


class CloudExecutionBlocked(RuntimeError):
    """Known execution pause, not an unexpected worker crash."""


def connection_path():
    from src.app_paths import user_home
    return user_home() / "config" / "cloud_session.json"


def load_connection():
    with _config_lock:
        path = connection_path()
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_connection(value):
    with _config_lock:
        path = connection_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(canonical(value), encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(path)


def validated_origin(url):
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if (parts.scheme not in {"http", "https"} or not parts.netloc or parts.path or parts.query or parts.fragment
            or parts.username or parts.password):
        raise ValueError("请填写无路径和参数的服务器地址")
    if parts.scheme != "https" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("公网服务器必须使用 HTTPS")
    return url


def relay_request(config, method, path, body=None):
    origin = validated_origin(config["server"])
    headers = {"Origin": origin}
    if config.get("device_token"):
        headers["Authorization"] = "Bearer " + config["device_token"]
    with httpx.Client(timeout=12, trust_env=False, follow_redirects=False) as client:
        response = client.request(method, origin + "/api/v1" + path, headers=headers, json=body)
    if not response.is_success:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        # No raw server response, request headers, passwords or traceback in UI.
        raise RuntimeError(str(detail)[:300] if isinstance(detail, str) else f"中转服务请求失败（HTTP {response.status_code}）")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("中转服务响应格式无效")
    return payload


class CloudClient:
    def __init__(self):
        self.gate = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.last_sync = 0
        self.message = "尚未连接云端"
        self.conflict_count = 0
        self.execution = {}

    def ledger(self, config, uid):
        from src.app_paths import user_home
        return Ledger(user_home() / "data" / "cloud_replica.db",
                      canonical([config["server"], config["user_id"], uid]))

    def request(self, config, method, path, body=None):
        return relay_request(config, method, path, body)

    def status(self):
        cfg = load_connection()
        return {"connected": bool(cfg), "server": cfg["server"] if cfg else "", "username": cfg["username"] if cfg else "",
                "device_id": cfg["device_id"] if cfg else "", "bili_uid": cfg.get("bili_uid", "") if cfg else "",
                "last_sync": self.last_sync, "message": self.blocked_reason() or self.message,
                "conflicts": self.conflict_count, "execution": self.execution if cfg else {}}

    def blocked_reason(self):
        return ""

    def refresh_execution(self, cfg=None):
        self.execution = {}
        return {}

    def execution_details(self):
        return {"account": {}, "tasks": []}

    def acknowledge(self):
        return self.status()

    def assert_idle(self):
        from web.job_runner import runner
        if runner.is_running():
            raise ValueError("请先结束当前任务")

    def connect(self, values):
        self.assert_idle()
        with self.gate:
            self.assert_idle()
            if load_connection():
                raise ValueError("请先断开当前应用账号")
            server = validated_origin(values["server"])
            from src.app_paths import user_home
            owner_path = user_home() / "config" / "cloud_owner.json"
            # Validate account identity before issuing a new device session on reconnect.
            body = {"username": values["username"], "password": values["password"],
                    "device_name": values["device_name"], "kind": "mac" if platform.system() == "Darwin" else "windows",
                    "invite": values.get("invite", "")}
            result = self.request({"server": server}, "POST", "/auth/register" if values.get("register") else "/auth/login", body)
            cfg = {**result, "server": server, "bili_uid": ""}
            owner = {"server": server, "user_id": result["user_id"]}
            if owner_path.exists() and json.loads(owner_path.read_text(encoding="utf-8")) != owner:
                self.request(cfg, "POST", "/auth/logout")
                raise ValueError("此本地数据目录属于另一应用账号；请使用独立 BINGGO_HOME，避免混用参与记录")
            owner_path.parent.mkdir(parents=True, exist_ok=True)
            owner_path.write_text(canonical(owner), encoding="utf-8")
            save_connection(cfg)
            self.message = "已连接活动资料库，点击立即同步；无需绑定 B 站账号"
            return self.status()

    def bind(self):
        self.message = "活动资料库无需绑定 B 站账号"
        return self.status()

    def disconnect(self):
        self.assert_idle()
        with self.gate:
            self.assert_idle()
            cfg = load_connection()
            if cfg:
                self.request(cfg, "POST", "/devices/" + cfg["device_id"] + "/revoke")
                connection_path().unlink(missing_ok=True)
            self.message, self.last_sync, self.conflict_count = "已断开云端连接", 0, 0
            self.execution = {}
            return self.status()

    def sync(self, config=None):
        from src.catalog_sync import synchronize as sync_catalog
        with self.gate:
            cfg = config or load_connection()
            if not cfg: raise ValueError("请先连接活动资料库")
            result = sync_catalog(lambda method,path,body=None:self.request(cfg,method,path,body))
            from src.repost_sync import synchronize as sync_reposts
            result.update(sync_reposts(lambda method,path,body=None:self.request(cfg,method,path,body),
                                       cfg["server"] + ":" + cfg["user_id"]))
            self.last_sync = int(time.time())
            self.conflict_count = 0
            self.message = f"活动资料已同步：上传 {result['uploaded']}，新增 {result['downloaded']}；转发关联上传 {result['reposts_uploaded']}、新增 {result['reposts_downloaded']}"
            return result

    def conflicts(self):
        return []

    def resolve(self, kind, key, choice):
        raise ValueError("活动资料库不再同步账号状态，无需处理旧同步冲突")

    def execute(self, job_id, action, params, function):
        # Task execution, cancellation and account queues are strictly local.
        check_cancelled()
        return function()

    def tick(self):
        if load_connection(): return self._tick_bound()

    def _tick_bound(self):
        if not self.gate.acquire(blocking=False): return
        try:
            cfg = load_connection()
            if cfg: self.sync(cfg)
        finally:
            self.gate.release()

    def start(self):
        if os.environ.get("BINGGO_E2E") == "1" or os.environ.get("BINGGO_DISABLE_BACKGROUND") == "1":
            return
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()

        def loop():
            while not self.stop_event.is_set():
                try:
                    self.tick()
                except Exception as exc:
                    from src.log_redact import redact_text
                    self.message = redact_text(str(exc))[:200]
                self.stop_event.wait(60)
        self.thread = threading.Thread(target=loop, daemon=True, name="cloud-sync")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)


cloud_client = CloudClient()
