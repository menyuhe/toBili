"""Account-local activity-list participation with a durable rolling-hour budget."""
import time
from src.accounts import lock, read_state, write_state
from src.account_context import scoped_uid
from src.task_control import check_cancelled, TaskCancelled
from src.participate_preflight import ActivityNotParticipatable

KEY = "nonstop_participation"


def status(uid, now=None):
    now = time.time() if now is None else now
    data = read_state(uid, KEY, {}) if uid else {}
    attempts = [float(t) for t in data.get("attempts", []) if float(t) > now - 3600]
    return {**data, "attempts": attempts, "used": len(attempts), "paused": bool(data.get("paused")),
            "message": str(data.get("message", ""))}


def resume(uid):
    with lock:
        data = status(uid)
        data.update(paused=False, message="用户已恢复挂机参与")
        write_state(uid, KEY, data)


def reserve(uid, limit):
    # Persist before calling the platform; failures/timeouts count too, including after restart.
    with lock:
        data = status(uid)
        if data["paused"] or data["used"] >= limit:
            return False
        data["attempts"].append(time.time())
        data["used"] += 1
        write_state(uid, KEY, data)
        return True


def pause(uid, message):
    with lock:
        data = status(uid)
        data.update(paused=True, message=message)
        write_state(uid, KEY, data)


def run(uid, *, candidates, participate, progress):
    from web.schedule_config import load
    if uid != scoped_uid():
        raise ValueError("挂机账号与任务上下文不一致")
    if not load(uid).nonstop or status(uid)["paused"]:
        return {"ok": True, "message": "挂机参与已关闭或暂停", "result": {"skipped": True}}
    ids = [did for did, item in candidates().items() if item.get("can_participate")]
    joined = skipped = attempted = 0
    for did in ids:
        try:
            check_cancelled()
            cfg = load(uid)
            if not cfg.nonstop or status(uid)["paused"]:
                break
            # Recheck local status immediately before each action; archived or joined items disappear.
            item = candidates().get(did)
            if not item or not item.get("can_participate"):
                skipped += 1
                continue
            if not reserve(uid, cfg.nonstop_hourly_limit):
                break
            attempted += 1
            result = participate(did, progress)
            if not result.get("ok"):
                raise RuntimeError(result.get("message") or "参与失败")
            joined += 1
            progress(step=joined, total=len(ids), message=f"挂机已参与 {joined} 个活动")
        except ActivityNotParticipatable:
            skipped += 1
        except TaskCancelled:
            pause(uid, "用户取消任务，等待手动恢复挂机参与")
            raise
        except Exception:
            pause(uid, "参与异常，已暂停挂机参与，请查看任务记录后手动恢复")
            raise
    usage = status(uid)
    return {"ok": True, "message": f"活动列表挂机：参与 {joined}，跳过 {skipped}；最近一小时已尝试 {usage['used']} 个",
            "result": {"joined": joined, "skipped_count": skipped, "attempted": attempted,
                       "hourly_used": usage["used"], "hourly_limit": load(uid).nonstop_hourly_limit}}
