"""Bounded, account-scoped login checks. Network trouble never means logged out."""

import json
import threading
import time

from ..domain.errors import Fault, MissingCredential, RemoteError

KEY = "account.health"


class AccountHealth:
    def __init__(self, box):
        self.box = box
        self.guard = threading.RLock()
        self.workers = {}
        self.started = {}
        self.generations = {}
        self.closed = False

    def view(self, owner):
        value = json.loads(self.box.repos.settings.get(owner, KEY, "{}"))
        result = {"state": "unknown", "note": "尚未检查登录状态", "checked_at": 0, **value}
        with self.guard:
            worker = self.workers.get(owner)
            if worker and worker.is_alive():
                waiting = time.monotonic() - self.started[owner]
                result.update(
                    state="checking" if waiting < 20 else "unavailable",
                    note="正在检查登录状态"
                    if waiting < 20
                    else "账号检查尚未返回，请查看钥匙串提示或稍后重试",
                    checking=True,
                )
        return result

    def record(self, owner, state, note):
        value = {"state": state, "note": note, "checked_at": time.time()}
        self.box.repos.settings.put(owner, KEY, json.dumps(value))
        self.box.bus.publish("account.health", {"account_id": owner, "state": state, "title": note})
        return value

    def signed_in(self, owner):
        with self.guard:
            self.generations[owner] = self.generations.get(owner, 0) + 1
            self.workers.pop(owner, None)
            self.started.pop(owner, None)
            return self.record(owner, "valid", "登录正常")

    def request(self, owner, *, force=False):
        with self.guard:
            if self.closed:
                return self.view(owner)
            old = self.view(owner)
            if old.get("checking") or time.time() - old["checked_at"] < (10 if force else 300):
                return old
            self.started[owner] = time.monotonic()
            generation = self.generations.get(owner, 0)
            thread = threading.Thread(target=self.check, args=(owner, generation), daemon=True)
            self.workers[owner] = thread
            thread.start()
        return self.view(owner)

    def read_profile(self, owner):
        from ..adapters.bili import BiliGateway, Wire

        gateway = BiliGateway(Wire(self.box.cookie(owner), patience=8, attempts=0))
        try:
            return gateway.profile()
        finally:
            gateway.close()

    def check(self, owner, generation=None):
        def save(state, note):
            with self.guard:
                if not self.closed and (
                    generation is None or generation == self.generations.get(owner, 0)
                ):
                    self.record(owner, state, note)

        try:
            expected = self.box.repos.accounts.by_id(owner)
            actual = self.read_profile(owner)
            if not expected or actual.uid != expected.uid:
                raise MissingCredential("登录身份不匹配，请用此账号重新扫码")
            if self.closed or (
                generation is not None and generation != self.generations.get(owner, 0)
            ):
                return
            # Update only the existing profile, never switch accounts or change credentials.
            with self.box.db.write() as db:
                db.execute(
                    "UPDATE account SET nickname=?,avatar=?,level=? WHERE id=? AND bili_uid=? AND disabled=0",
                    (actual.nickname, actual.avatar, actual.level, owner, actual.uid),
                )
            save("valid", "登录正常")
        except MissingCredential:
            if not self.closed:
                save("login_required", "登录已失效或身份不符，请用此账号重新扫码；原资料与排期保留")
        except RemoteError as exc:
            if not self.closed:
                if exc.fault is Fault.AUTH:
                    save("login_required", "登录已失效，请重新扫码；原资料与排期保留")
                else:
                    save("unavailable", "暂时无法验证登录，请稍后重试；不会因此判定账号退出")
        except Exception:
            if not self.closed:
                save("unavailable", "账号资料暂不可读，请检查网络或钥匙串授权后重试")

    def close(self):
        with self.guard:
            self.closed = True
