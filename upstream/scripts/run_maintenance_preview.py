"""Isolated UI fixture. Network disabled; no real credentials or account operations."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["BINGGO_HOME"] = tempfile.mkdtemp(prefix="binggo-maintenance-preview-")
os.environ["BINGGO_E2E"] = "1"
os.environ["BINGGO_DISABLE_BACKGROUND"] = "1"
os.environ["BILI_COOKIE"] = "DedeUserID=999001; bili_jct=preview; SESSDATA=preview-not-real"

from web.e2e_seed import seed_e2e_home
seed_e2e_home(Path(os.environ["BINGGO_HOME"]))
from web.app import app
from web.e2e_hooks import set_e2e_state
set_e2e_state(account="logged_in", llm="ready")
from src.db.session import session_scope
from src.db.models import ActivityRow, ParticipationRow, ParticipationLinkRow, DrawOutcomeRow

now = int(time.time())
with session_scope() as session:
    for index, (outcome,title) in enumerate([
        ("unknown","桌面好物抽奖 · 开奖公告尚待核对"),
        ("lost","周末限定礼盒 · 官方完整名单已核验"),
        ("won","夏日音乐会门票 · 已确认中奖"),
        ("pending","新品体验活动 · 等待官方开奖"),
    ]):
        did = str(777001 + index)
        session.add(ActivityRow(dynamic_id=did, lottery_type="互动抽奖", activity_status="已结束", payload_json=json.dumps({"title":title,"sender_uid":str(88001+index)},ensure_ascii=False)))
        session.add(ParticipationRow(uid="999001",dynamic_id=did,user_status="已参加"))
        session.add(ParticipationLinkRow(uid="999001",dynamic_id=did,up_uid=str(88001+index),origin="historical" if index==0 else "new",repost_id=str(888001+index),repost_recorded=index!=0,follow_added=index!=0))
        session.add(DrawOutcomeRow(uid="999001",dynamic_id=did,outcome=outcome,source="official" if index in (1,2) else "none",checked_at=now-300,lost_at=now-4000 if index==1 else 0,reason="官方完整名单不包含当前账号" if index==1 else "示例记录，仅用于界面验收"))

# A full 20-card page must keep intrinsic row height.
with session_scope() as session:
    for i in range(30):
        did=str(4444444444444444400+i)
        session.add(ActivityRow(dynamic_id=did, source_url=f"https://www.bilibili.com/opus/{did}",
            lottery_type="互动抽奖", activity_status="未参加", draw_status="active", status_classified=True,
            lottery_time=now+86400, payload_json=json.dumps({"prizes":[{"description":f"桌面好物体验礼盒 {i+1}","winner_count":2}]},ensure_ascii=False)))

import httpx
def deny_external(*args, **kwargs):
    raise RuntimeError("Preview fixture blocks all outbound HTTP calls")
httpx.Client.send = deny_external

# Demonstration-only ticket adapter: no prepare/create methods and no external HTTP.
class PreviewTicketApi:
    def __init__(self, *args): pass
    def verify_login(self): pass
    def project(self, pid):
        return dict(project_id=pid, name="Binggo 示例音乐会（模拟数据）", url=f"https://show.bilibili.com/platform/detail.html?id={pid}", options=[dict(screen_id="2",sku_id="3",label="周六晚场 · 标准票",price=9900,express_fee=0,sale_start=int(time.time())+3600,supported=True)])
    def context(self,pid):
        return {**self.project(pid), "buyers":[dict(id="4",label="演** · 模拟购票人")], "addresses":[dict(id="5",label="示** · 演示城市 · 手机尾号 0000")]}
    def payload(self, config, contact): return {}
import src.tickets, web.ticket_routes
src.tickets.TicketApi = web.ticket_routes.TicketApi = PreviewTicketApi

import uvicorn
import src.cleanup_api
class PreviewDiscoveryApi:
    def __init__(self, client, uid): self.uid = uid
    def assert_account(self): pass
    def verify_login(self): pass
    def repost_page(self, offset):
        index = int(offset or 0)
        original = "777002" if index == 5 else "777004" if index in (0, 6) else "999999"
        return {"items":[{"type":"DYNAMIC_TYPE_FORWARD","id_str":str(990000 + index),
            "modules":{"module_author":{"mid":self.uid,"pub_ts":now-index*86400}},
            "orig":{"id_str":original,"modules":{"module_author":{"mid":"88002" if index==5 else "88004"}}}}],
            "offset":str(index+1),"has_more":index<6}
src.cleanup_api.CleanupApi = PreviewDiscoveryApi
with session_scope() as session:
    for did in ("777002", "777004"):
        row = session.get(ParticipationLinkRow, ("999001", did))
        row.repost_id = ""
        row.repost_recorded = False
        row.origin = "historical"
        row.follow_added = False
uvicorn.run(app, host="127.0.0.1", port=8792, log_level="warning")
