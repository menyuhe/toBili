import { fetchJSON } from "../api/client";
import { escapeHtml } from "../utils/text";
import { showToast } from "../shell/toast";
import { openAppConfirm } from "../shell/confirm";
import { startPolling } from "../jobs/index";

type Option = { screen_id: string; sku_id: string; label: string; price: number; express_fee: number; sale_start: number; supported: boolean };
type Context = { uid: string; project_id: string; name: string; url: string; options: Option[]; buyers: { id: string; label: string }[]; addresses: {id: string; label: string}[] };
type Task = { id: string; uid: string; state: string; message: string; order_id: string; order_url: string; start_at: number; attempts: number; config: { name: string; label: string; amount: number; url: string; buyer_ids: string[] }; history: {state: string; message: string; at: number}[] };
const labels: Record<string,string> = {draft:"待开始", waiting:"等待开售", paused:"已暂停", preparing:"准备订单", ordering:"正在下单", payment:"待支付", handoff:"需手动验证", unknown:"结果待核实", order_review:"订单待核实", completed:"已支付 · 人工核对", closed:"已关闭 · 人工核对", failed:"已停止", cancelled:"已取消"};
const el = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const value = (id: string) => el<HTMLInputElement>(id).value;
const esc = (v: unknown) => escapeHtml(String(v ?? ""));
const money = (c: number) => `¥${(c / 100).toFixed(2)}`;
const post = (v?: unknown): RequestInit => ({method:"POST", headers:{"Content-Type":"application/json"}, ...(v === undefined ? {} : {body:JSON.stringify(v)})});
let context: Context | null = null;
let tasks: Task[] = [];
let clockOffset = 0;
let loading = false;
let taskSignature = "";

const buyers = () => Array.from(document.querySelectorAll<HTMLInputElement>("[name=ticket-buyer]:checked")).map(e => e.value);
function option() { return context?.options[Number(value("ticket-option"))]; }
function total() { const o = option(); return o ? o.price * buyers().length + o.express_fee : 0; }
function updatePrice() { el("ticket-price").textContent = `${buyers().length} 张 · 合计 ${money(total())}（含运费）`; }

function populate(c: Context) {
  context = c;
  el("ticket-project-title").textContent = c.name;
  el<HTMLAnchorElement>("ticket-official").href = c.url;
  el("ticket-option").innerHTML = c.options.map((o,i) => `<option value="${i}" ${o.supported ? "" : "disabled"}>${esc(o.label)} · ${money(o.price)}${o.supported ? "" : " · 请在官方页面选座"}</option>`).join("");
  el("ticket-buyers").innerHTML = c.buyers.length ? c.buyers.map(b => `<label class="maintenance-check"><input type="checkbox" name="ticket-buyer" value="${esc(b.id)}">${esc(b.label)}</label>`).join("") : '<p class="caption">尚无购票人，请先在官方会员购添加实名购票人，再重新读取。</p>';
  el("ticket-address").innerHTML = '<option value="">不配送／未选择</option>' + c.addresses.map(a => `<option value="${esc(a.id)}">${esc(a.label)}</option>`).join("");
  el("ticket-config").hidden = false;
  setSaleTime(); updatePrice();
}

function setSaleTime() {
  const ts = option()?.sale_start || 0;
  const d = new Date((ts || Date.now()/1000) * 1000);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  el<HTMLInputElement>("ticket-start").value = d.toISOString().slice(0,19);
}

function countdowns() {
  document.querySelectorAll<HTMLElement>("[data-ticket-countdown]").forEach(node => {
    const seconds = Math.max(0, Math.ceil(Number(node.dataset.ticketCountdown) - (Date.now()/1000 + clockOffset)));
    node.textContent = seconds ? `${Math.floor(seconds/86400)} 天 ${Math.floor(seconds%86400/3600)} 时 ${Math.floor(seconds%3600/60)} 分 ${seconds%60} 秒` : "已到开售时间，等待任务空闲";
  });
}

async function loadTasks() {
  if (loading) return;
  loading = true;
  try {
    const result = await fetchJSON<{uid: string; server_time: number; items: Task[]}>("/api/tickets/tasks");
    if (context && context.uid !== result.uid) { context = null; el("ticket-config").hidden = true; }
    clockOffset = result.server_time - Date.now()/1000;
    const signature = JSON.stringify([result.uid, result.items]);
    if (signature === taskSignature) { countdowns(); return; }
    taskSignature = signature;
    // Keep open audit disclosures when background data refreshes.
    const expanded = new Set(Array.from(document.querySelectorAll<HTMLDetailsElement>("[data-ticket-history][open]")).map(e => e.dataset.ticketHistory));
    tasks = result.items;
    el("ticket-tasks").innerHTML = tasks.length ? tasks.map(t => `<article class="cleanup-card ticket-task"><div class="cleanup-card-head"><div><span class="draw-badge">${esc(labels[t.state] || t.state)}</span><h3>${esc(t.config.name)}</h3><p class="caption">${esc(t.config.label)} · ${t.config.buyer_ids.length} 张 · ${money(t.config.amount)}</p></div><a class="btn btn-secondary btn-pill" href="${esc(t.order_url || t.config.url)}" target="_blank" rel="noopener">${t.order_id ? "打开订单／支付" : "打开官方购票页"} ↗</a></div><p>${esc(t.message)}</p>${t.state === "waiting" ? `<p class="ticket-countdown" data-ticket-countdown="${t.start_at}"></p>` : ""}<p class="caption">尝试 ${t.attempts} 次${t.order_id ? ` · 订单 ${esc(t.order_id)}` : ""}</p><div class="maintenance-actions">${["draft","paused","handoff","failed","cancelled"].includes(t.state) && !t.history.some(h=>["ordering","unknown","payment"].includes(h.state)) ? `<button class="btn btn-primary btn-pill" data-ticket-action="start" data-id="${t.id}">${t.state === "draft" ? "确认并开始" : "核对后继续"}</button>` : ""}${["draft","waiting","paused","preparing","ordering","handoff"].includes(t.state) ? `<button class="btn btn-secondary btn-pill" data-ticket-action="cancel" data-id="${t.id}">取消任务</button>` : ""}${t.order_id && ["payment","unknown"].includes(t.state) ? `<button class="btn btn-secondary btn-pill" data-ticket-action="check" data-id="${t.id}">核实是否可支付</button>` : ""}${["payment","unknown","order_review"].includes(t.state) ? `<button class="btn btn-secondary btn-pill" data-ticket-action="completed" data-id="${t.id}">我已支付</button><button class="btn btn-secondary btn-pill" data-ticket-action="closed" data-id="${t.id}">订单已关闭／未生成</button>` : ""}</div><details data-ticket-history="${t.id}" ${expanded.has(t.id) ? "open" : ""}><summary>任务记录</summary>${t.history.map(h=>`<p class="caption">${new Date(h.at*1000).toLocaleString()} · ${esc(h.message)}</p>`).join("") || '<p class="caption">已保存配置，尚未开始。</p>'}</details></article>`).join("") : '<p class="maintenance-empty">暂无会员购任务。读取票务链接后配置场次和购票人。</p>';
    countdowns();
  } catch(e) { taskSignature = ""; el("ticket-tasks").innerHTML = `<p class="maintenance-empty" role="alert">${esc(e instanceof Error ? e.message : e)}</p>`; }
  finally { loading = false; }
}

async function guarded(button: HTMLButtonElement, fn: () => Promise<void>) {
  button.disabled = true;
  try { await fn(); } catch(e) { showToast(e instanceof Error ? e.message : String(e), "error"); }
  finally { button.disabled = false; }
}

export function bindTickets() {
  const section = el("section-tickets");
  section.innerHTML = `<div class="settings-stack"><article class="panel llm-settings"><div class="settings-panel-head"><div class="settings-panel-intro"><h2 class="section-title">配置购票任务</h2><p class="section-desc">当前支持常规场次票档。选座、验证码或风控验证请在官方页面完成；不会自动支付。</p></div></div><form id="ticket-project-form" class="ticket-project-form"><label class="settings-field"><span class="field-label">会员购票务链接或项目 ID</span><input class="form-input" id="ticket-url" required placeholder="https://show.bilibili.com/platform/detail.html?id=…"></label><button class="btn btn-primary btn-pill" type="submit">读取项目</button></form><form id="ticket-config" hidden><div class="llm-settings-body"><div class="settings-field"><h3 id="ticket-project-title"></h3><a id="ticket-official" target="_blank" rel="noopener">打开官方项目 ↗</a></div><label class="settings-field"><span class="field-label">场次与票档</span><select class="form-input" id="ticket-option" required></select></label><div class="settings-field"><span class="field-label">实名购票人 · 选择人数即购票张数</span><div id="ticket-buyers" class="maintenance-switches"></div></div><label class="settings-field"><span class="field-label">配送地址</span><select class="form-input" id="ticket-address"></select></label><div class="settings-field-row"><label class="settings-field"><span class="field-label">联系人</span><input class="form-input" id="ticket-contact" maxlength="80" required autocomplete="off"></label><label class="settings-field"><span class="field-label">联系电话</span><input class="form-input" id="ticket-phone" type="tel" maxlength="20" required autocomplete="off"></label></div><label class="settings-field"><span class="field-label">开始时间（本机时区）</span><input class="form-input" id="ticket-start" type="datetime-local" step="1" required></label><div class="settings-field-row"><label class="settings-field"><span class="field-label">库存不足时重试间隔（秒）</span><input class="form-input" id="ticket-interval" type="number" min="2" max="60" value="2" required></label><label class="settings-field"><span class="field-label">最多尝试时长（秒）</span><input class="form-input" id="ticket-duration" type="number" min="10" max="600" value="60" required></label></div><p class="caption">账号资料仅在本地使用。保存时复核金额，开始后遇到涨价会停止并要求重新核对。</p></div><div class="settings-panel-foot"><strong id="ticket-price"></strong><div class="settings-panel-actions"><button class="btn btn-primary btn-pill" type="submit">保存任务</button></div></div></form></article><article class="panel"><div class="settings-panel-head"><div class="settings-panel-intro"><h2 class="section-title">购票任务与订单</h2><p class="section-desc">等待开售不占用抽奖任务。开售时共用执行锁；忙碌则延后。关闭软件后不运行，重启后需要核对并继续。</p></div><button class="btn btn-secondary btn-pill" id="ticket-refresh">刷新</button></div><p class="caption">在设置 → 通知中心勾选“会员购订单与验证”，可使用音频或钉钉提醒。</p><div id="ticket-tasks"><p class="maintenance-empty">登录后显示当前账号任务</p></div></article></div>`;
  el("ticket-project-form").addEventListener("submit", e => {
    e.preventDefault();
    void guarded((e as SubmitEvent).submitter as HTMLButtonElement, async () => {
      const c = await fetchJSON<Context>("/api/tickets/project", post({url:value("ticket-url")})); populate(c);
    });
  });
  el("ticket-option").addEventListener("change", () => { setSaleTime(); updatePrice(); });
  el("ticket-buyers").addEventListener("change", updatePrice);
  el("ticket-config").addEventListener("submit", e => {
    e.preventDefault();
    void guarded((e as SubmitEvent).submitter as HTMLButtonElement, async () => {
      const o = option(); if (!context || !o || !buyers().length) throw new Error("请先选择有效票档与购票人");
      const current = await fetchJSON<{uid: string}>("/api/tickets/tasks");
      if (current.uid !== context.uid) throw new Error("账号已切换，请重新读取项目和购票人");
      await fetchJSON("/api/tickets/tasks", post({project_id:context.project_id, screen_id:o.screen_id, sku_id:o.sku_id, buyer_ids:buyers(), address_id:value("ticket-address"), amount:total(), start_at:Math.floor(new Date(value("ticket-start")).getTime()/1000), interval:Number(value("ticket-interval")), duration:Number(value("ticket-duration")), contact_name:value("ticket-contact"), contact_phone:value("ticket-phone")}));
      el<HTMLInputElement>("ticket-phone").value = ""; el<HTMLInputElement>("ticket-contact").value = "";
      showToast("任务已保存，请在下方核对并开始", "success"); await loadTasks();
    });
  });
  el("ticket-refresh").addEventListener("click", () => void loadTasks());
  el("ticket-tasks").addEventListener("click", e => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-ticket-action]"); if (!b) return;
    void guarded(b, async () => {
      const t = tasks.find(t=>t.id === b.dataset.id); if (!t) return;
      const a = b.dataset.ticketAction;
      if (a === "start" && !await openAppConfirm({title:"开始会员购任务？", desc:`${t.config.name} · ${t.config.label}，${t.config.buyer_ids.length} 张，合计 ${money(t.config.amount)}。${new Date(t.start_at*1000).toLocaleString()} 开始。将尝试提交订单，支付需您自行完成。`,confirmLabel:"确认并开始",cancelLabel:"返回核对"})) return;
      if ((a === "completed" || a === "closed") && !await openAppConfirm({title:"已在官方订单页核实？", desc:a === "completed" ? "仅在确认已支付后记录。此状态来自您的人工核对。" : "请确认不存在待支付或已支付订单。保存后允许为同一项目创建新任务。",confirmLabel:"确认已核实",cancelLabel:"继续核对"})) return;
      await fetchJSON(`/api/tickets/tasks/${t.id}/${["completed","closed"].includes(a || "") ? "resolve" : a}`, post(["completed","closed"].includes(a || "") ? {outcome:a} : undefined));
      startPolling(); await loadTasks();
    });
  });
  window.addEventListener("binggo:section", () => { if (section.classList.contains("active")) void loadTasks(); });
  window.setInterval(() => { if (section.classList.contains("active")) void loadTasks(); }, 4000);
  window.setInterval(countdowns, 1000);
}
