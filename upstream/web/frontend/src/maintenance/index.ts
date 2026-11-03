import { fetchJSON } from "../api/client";
import { showToast } from "../shell/toast";
import { escapeHtml } from "../utils/text";
import { startPolling, toggleLogDock } from "../jobs/index";
import { openAppConfirm } from "../shell/confirm";
import { activateSection } from "../shell/nav";
import { activeUid, accountGeneration } from "../account/identity";

type NoticeSettings = { audio: boolean; dingtalk: boolean; prefix: string; events: string[]; webhook_hint: string; secret_hint: string; audio_available: boolean };
type CleanSettings = { cleanup_batch_limit?: number; uid: string; enabled: boolean; delete_reposts: boolean; unfollow: boolean; protected_ups: string[] };
type Scan = { pages: number; scanned: number; oldest_at: number; updated_at: number; finished: boolean; can_continue: boolean };
type Association = { state: string; label: string; checked_at: number; candidates: { repost_id: string; up_uid: string }[] };
type Candidate = { association: Association; dynamic_id: string; title: string; outcome: string; source: string; checked_at: number; due_at: number; keep: boolean; reason: string; reasons: string[]; delete_judgement: string; unfollow_judgement: string; delete_state: string; unfollow_state: string; link: { up_uid: string; repost_id: string; repost_recorded: boolean; follow_added: boolean; origin: string } };
type History = { title?: string; channel?: string; state: string; message: string; created_at?: number; recorded_at?: number; action?: string; target_id?: string };
const outcomes: Record<string, string> = { pending: "待开奖", unknown: "待确认", won: "已中奖", lost: "未中奖" };
const states: Record<string, string> = { pending: "尚未执行", running: "执行中", done: "已完成", unknown: "待核实", blocked: "已保留", sent: "已发送", sending: "发送中", failed: "失败", skipped: "已跳过" };
const events = { won: "中奖", at: "@ 未读增加", login: "登录失效", failure: "任务失败", cleanup: "清理汇总", ticket: "会员购订单与验证" };
const el = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const input = (id: string) => el<HTMLInputElement>(id);
const date = (stamp: number) => stamp ? new Date(stamp * 1000).toLocaleString("zh-CN", { hour12: false }) : "—";
const esc = (value: unknown) => escapeHtml(String(value ?? ""));
const body = (value: unknown, method = "PUT"): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
const associationLabels: Record<string, string> = { candidate: "已匹配待核对", ambiguous: "多条候选／冲突", not_found: "扫描范围内未找到", unscanned: "尚未扫描", confirmed: "已核验关联" };
let associationFilter = "";
let page = 1;
let rows: Candidate[] = [];
let loadedCleanUid = "";
let settingsDirty = false;
let cleanupDirty = false;
let mounted = false;

async function action(button: HTMLButtonElement, fn: () => Promise<void>) {
  button.disabled = true;
  try { await fn(); } catch (error) { showToast(error instanceof Error ? error.message : String(error), "error"); }
  finally { button.disabled = false; }
}

async function start(url: string, values?: unknown) {
  await fetchJSON(url, values === undefined ? { method: "POST" } : body(values, "POST"));
  toggleLogDock(true);
  startPolling();
}

function renderHistory(items: History[]) {
  return items.length ? items.map(item => `<div class="maintenance-history-row"><div><strong>${esc(item.title || ({ delete: "删除转发", unfollow: "取关 UP", manual_result: "人工核验", draw_check: "开奖核验", keep: "保留标记", link_confirmed: "关联核对" } as Record<string, string>)[item.action || ""] || "关联记录")}</strong><span>${esc(item.channel === "audio" ? "音频" : item.channel === "dingtalk" ? "钉钉" : item.target_id || "")}</span></div><p>${esc(item.message)} <span class="maintenance-muted">${esc(states[item.state] || outcomes[item.state] || item.state)}</span></p><time>${esc(date(item.created_at || item.recorded_at || 0))}</time></div>`).join("") : '<p class="maintenance-empty">暂无记录</p>';
}

async function loadHistory() {
  const result = await fetchJSON<{ items: History[] }>("/api/notifications/history");
  el("notification-history").innerHTML = renderHistory(result.items);
}

let historyBefore: number | null = null;
async function loadCleanupHistory(more = false) {
  const result = await fetchJSON<{ items: History[]; next_before: number | null; deleted: number; unfollowed: number; needs_review: number; outcomes: Record<string, number>; settings: CleanSettings & { last_run: number } }>(`/api/maintenance/cleanup-history${more && historyBefore ? `?before=${historyBefore}` : ''}`);
  historyBefore = result.next_before;
  el('cleanup-statistics').innerHTML = `<div><strong>${result.deleted}</strong><span>累计删除转发</span></div><div><strong>${result.unfollowed}</strong><span>累计取关 UP</span></div><div><strong>${result.needs_review}</strong><span>清理结果待核实 / 已保留</span></div>`;
  el('cleanup-explanation').textContent = `自动清理${result.settings.enabled ? '已开启' : '未开启'} · 删除${result.settings.delete_reposts ? '已开启' : '未开启'} · 取关${result.settings.unfollow ? '已开启' : '未开启'}。上次维护：${date(result.settings.last_run)}。待开奖 ${result.outcomes.pending} 个，结果待确认 ${result.outcomes.unknown} 个，确认未中奖 ${result.outcomes.lost} 个。确认未中奖后等待 24 小时；每轮按开奖时间从旧到新，最多删除 ${result.settings.cleanup_batch_limit || 20} 条转发、取关 ${result.settings.cleanup_batch_limit || 20} 位 UP。原有关注、保留名单及仍有关联活动的 UP 会保留。具体原因见下方活动卡片。`;
  const list = el('cleanup-recent-list');
  if (more) list.insertAdjacentHTML('beforeend', renderHistory(result.items));
  else list.innerHTML = result.items.length ? renderHistory(result.items) : '<p class="maintenance-empty">尚无删除或取关执行记录。可在下方查看未满足的清理条件。</p>';
  el<HTMLButtonElement>('cleanup-history-more').disabled = !historyBefore;
}

async function loadSettings(force = false) {
  if (!settingsDirty || force) {
    const s = await fetchJSON<NoticeSettings>("/api/notifications/settings");
    input("notify-audio").checked = s.audio;
    input("notify-audio").disabled = !s.audio_available;
    el<HTMLButtonElement>("notify-audio-test").disabled = !s.audio_available;
    input("notify-dingtalk").checked = s.dingtalk;
    input("notify-prefix").value = s.prefix;
    input("notify-webhook").value = "";
    input("notify-secret").value = "";
    input("notify-webhook").placeholder = s.webhook_hint || "https://oapi.dingtalk.com/robot/send?access_token=…";
    input("notify-secret").placeholder = s.secret_hint || "可选：SEC 开头的加签密钥";
    for (const key of Object.keys(events)) input(`notify-event-${key}`).checked = s.events.includes(key);
    settingsDirty = false;
  }
  try {
    const s = await fetchJSON<CleanSettings>("/api/maintenance/settings");
    if (!cleanupDirty || force || loadedCleanUid !== s.uid) {
      input("cleanup-enabled").checked = s.enabled;
      input("cleanup-delete").checked = s.delete_reposts;
      input("cleanup-unfollow").checked = s.unfollow;
      el<HTMLTextAreaElement>("cleanup-protected").value = s.protected_ups.join("\n");
      cleanupDirty = false;
    }
    loadedCleanUid = s.uid;
    el("cleanup-account-hint").textContent = `当前账号 UID ${s.uid} · 仅在软件运行时每 30 分钟维护`;
    el<HTMLFieldSetElement>("cleanup-fields").disabled = false;
  } catch {
    loadedCleanUid = "";
    el<HTMLFieldSetElement>("cleanup-fields").disabled = true;
    el("cleanup-account-hint").textContent = "登录后可设置当前账号的自动清理";
  }
  await loadHistory();
}

async function loadCandidates() {
  void loadMessageReview().catch(() => {});
  const list = el("cleanup-list");
  list.setAttribute("aria-busy", "true");
  try {
    const query = new URLSearchParams({ page: String(page), outcome: input("cleanup-filter").value, q: input("cleanup-search").value, association: associationFilter });
    const result = await fetchJSON<{ items: Candidate[]; total: number; association_counts: Record<string, number>; scan: Scan }>(`/api/maintenance/activities?${query}`);
    rows = result.items;
    el("cleanup-association-filters").innerHTML = [["", "全部关联"], ...Object.entries(associationLabels)].map(([key, label]) => `<button class="btn ${associationFilter === key ? "btn-primary" : "btn-secondary"} btn-pill" data-association-filter="${key}" aria-pressed="${associationFilter === key}">${label}${key ? ` ${result.association_counts[key] || 0}` : ""}</button>`).join("");
    const scan = result.scan;
    el<HTMLButtonElement>("cleanup-discover-more").disabled = !scan.can_continue;
    el("cleanup-scan-status").textContent = scan.pages ? `本轮已扫描 ${scan.pages} 页、${scan.scanned} 条动态${scan.oldest_at ? `，最早看到 ${date(scan.oldest_at)}` : ""}。${scan.finished ? "已到接口返回的列表末尾。" : "可继续扫描更早动态；进度已保存。"} 未找到不代表未转发，候选累计保留，仍需人工核对。` : "每次扫描 5 页；首次从最近动态开始，之后可继续扫描更早动态。旧版已找到的候选仍保留。";
    el("cleanup-count").textContent = `${result.total} 个已参与活动 · 包含已结束与已归档记录`;
    el("cleanup-page").textContent = `第 ${page} / ${Math.max(1, Math.ceil(result.total / 20))} 页`;
    el<HTMLButtonElement>("cleanup-prev").disabled = page <= 1;
    el<HTMLButtonElement>("cleanup-next").disabled = page * 20 >= result.total;
    list.innerHTML = rows.length ? rows.map(row => `<article class="cleanup-card" data-did="${esc(row.dynamic_id)}">
      <div class="cleanup-card-head"><div><span class="draw-badge draw-${esc(row.outcome)}">${esc(outcomes[row.outcome] || "待确认")}</span>${row.keep ? '<span class="draw-badge">已保留</span>' : ""}<h3>${esc(row.title)}</h3></div><a class="btn btn-ghost" href="https://www.bilibili.com/opus/${encodeURIComponent(row.dynamic_id)}" target="_blank" rel="noopener noreferrer">原活动 ↗</a></div>
      <p class="association-state"><strong>${esc(row.association.label)}</strong>${row.association.checked_at ? ` · 最近扫描 ${esc(date(row.association.checked_at))}` : ""}</p><div class="cleanup-association"><a href="https://www.bilibili.com/opus/${encodeURIComponent(row.dynamic_id)}" target="_blank" rel="noopener">原活动 ${esc(row.dynamic_id)}</a><span>→</span>${row.link.repost_id ? `<a href="https://www.bilibili.com/opus/${encodeURIComponent(row.link.repost_id)}" target="_blank" rel="noopener">本人转发 ${esc(row.link.repost_id)}</a>` : '<span>本人转发尚未关联</span>'}<span>→</span><span>${row.link.repost_id && !row.link.repost_recorded ? "转发候选 · 待核对" : row.link.origin === "new" ? "参与时记录" : row.link.origin === "confirmed" ? "人工核验关联" : "历史记录待核对"}</span></div>
      <p class="maintenance-muted">${esc(row.reason)} · ${row.source === "manual" ? "人工确认" : row.source === "official" ? "官方结果" : "尚无结果"}</p>
      <dl class="cleanup-meta"><div><dt>最近核验</dt><dd>${esc(date(row.checked_at))}</dd></div><div><dt>最早清理时间</dt><dd>${esc(date(row.due_at))}</dd></div><div><dt>删除转发</dt><dd>${esc(states[row.delete_state] || row.delete_state)}<p class="caption">${esc(row.delete_judgement)}</p></dd></div><div><dt>取关 UP</dt><dd>${esc(states[row.unfollow_state] || row.unfollow_state)}<p class="caption">${esc(row.unfollow_judgement)}</p></dd></div></dl>
      <p class="caption">清理判断：${row.outcome === "lost" ? "已确认未中奖 → 24 小时等待 → 执行前复核 → 删除转发 → 按 UP 合并判断取关" : row.outcome === "won" ? "已确认中奖，保留转发及关联关注" : "等待可靠开奖结果，暂不清理"}</p>
      ${row.reasons.length ? `<p class="cleanup-reasons">${row.reasons.map(esc).join(" · ")}</p>` : ""}
      <div class="maintenance-actions"><button class="btn btn-ghost" data-clean="keep">${row.keep ? "取消保留" : "保留此活动"}</button><button class="btn btn-ghost" data-clean="retry">重新核验并重试</button><button class="btn btn-ghost" data-clean="history">执行记录</button></div>
      <details class="cleanup-review"><summary>核对关联与人工结果</summary><div class="maintenance-form-grid">
      <label>UP 主 UID<input name="up_uid" inputmode="numeric" value="${esc(row.link.up_uid)}" autocomplete="off"></label>
      <label>本人对应转发 ID<input name="repost_id" inputmode="numeric" value="${esc(row.link.repost_id)}" autocomplete="off" placeholder="非转发活动可留空"></label></div>
      <label class="maintenance-check"><input type="checkbox" name="follow_added" ${row.link.follow_added ? "checked" : ""}>我确认该关注由本软件新增，或明确纳入此次清理</label>
      <p class="maintenance-muted">${row.link.origin === "historical" ? "历史记录尚未获得清理资格。" : "已记录参与关联。"}保存时将核验转发归属与原活动；无法证明新增的关注请保留。</p>
      <div class="maintenance-actions"><button class="btn btn-ghost" data-clean="discover">查询最近 5 页候选</button><button class="btn btn-ghost" data-clean="link">核验并保存关联</button></div><div class="cleanup-discovery maintenance-muted" aria-live="polite">${row.association.candidates.length ? `候选转发（点击填入后核对保存）：<div class="maintenance-actions">${row.association.candidates.map(candidate => `<button type="button" class="btn btn-ghost" data-repost-choice="${esc(candidate.repost_id)}">${esc(candidate.repost_id)}</button>`).join("")}</div>` : ""}</div>
      <div class="cleanup-manual"><label>人工结果<select name="outcome"><option value="unknown">待确认</option><option value="won">已中奖</option><option value="lost">确认未中奖</option></select></label><button class="btn btn-ghost" data-clean="result">记录人工结果</button></div>
      <p class="maintenance-muted">确认未中奖会开始 24 小时等待。开奖时间已过或没有收到消息，不能作为未中奖证据。</p></details><div class="cleanup-audit" hidden></div>
    </article>`).join("") : '<div class="maintenance-empty"><strong>暂无符合条件的参与记录</strong><p>参与活动后会在此显示开奖结果；历史记录需要先核对关联。</p></div>';
  } catch (error) {
    if (error instanceof Error && error.name === "StaleAccountError") return;
    list.innerHTML = `<p class="maintenance-empty" role="alert">${esc(error instanceof Error ? error.message : error)}。可点击刷新重试。</p>`;
  } finally { list.setAttribute("aria-busy", "false"); }
}

async function rowAction(button: HTMLButtonElement) {
  const card = button.closest<HTMLElement>("[data-did]")!;
  const row = rows.find(item => item.dynamic_id === card.dataset.did)!;
  const path = `/api/maintenance/activities/${encodeURIComponent(row.dynamic_id)}`;
  const value = (name: string) => card.querySelector<HTMLInputElement>(`[name="${name}"]`)!;
  switch (button.dataset.clean) {
    case "keep": await fetchJSON(`${path}/keep`, body({ keep: !row.keep })); await loadCandidates(); break;
    case "retry": await start(`${path}/retry`); break;
    case "discover": await start(`${path}/discover`); break;
    case "link": await start(`${path}/link`, { up_uid: value("up_uid").value.trim(), repost_id: value("repost_id").value.trim(), follow_added: value("follow_added").checked }); break;
    case "result": {
      const outcome = value("outcome").value;
      if (outcome === "lost" && !await openAppConfirm({ title: "确认此活动未中奖？", desc: "请依据完整中奖名单或已核实的开奖结果。保存后开始 24 小时等待；已开启的清理将在再次核验后执行。", confirmLabel: "确认未中奖", cancelLabel: "返回核对", danger: true })) return;
      await fetchJSON(`${path}/result`, body({ outcome })); await loadCandidates(); break;
    }
    case "history": {
      const history = await fetchJSON<{ items: History[] }>(`/api/maintenance/history?dynamic_id=${encodeURIComponent(row.dynamic_id)}`);
      const area = card.querySelector<HTMLElement>(".cleanup-audit")!;
      area.hidden = false; area.innerHTML = renderHistory(history.items); break;
    }
  }
}

export function bindMaintenance() {
  if (mounted) return;
  mounted = true;
  el("section-settings").querySelector(".settings-stack")!.insertAdjacentHTML("beforeend", `
    <section class="panel llm-settings maintenance-settings" id="notification-settings">
    <div class="settings-panel-head"><div class="settings-panel-intro"><h2 class="section-title">通知中心</h2><p class="section-desc">桌面音频与钉钉独立发送，最小化后仍可提醒。</p></div></div>
    <form id="notification-form"><div class="llm-settings-body">
    <div class="settings-field-row"><div class="settings-field"><label class="field-label maintenance-check"><input id="notify-audio" type="checkbox">音频通知</label><p class="field-help">播放一次内置提示音，不循环响铃。</p><button id="notify-audio-test" class="btn btn-secondary btn-pill" type="button">试听提示音</button></div>
    <div class="settings-field"><label class="field-label maintenance-check"><input id="notify-dingtalk" type="checkbox">钉钉群机器人</label><p class="field-help">使用自定义机器人的 Webhook 与安全设置。</p><button id="notify-dingtalk-test" class="btn btn-secondary btn-pill" type="button">测试发送</button></div></div>
    <div class="settings-field"><label class="field-label" for="notify-webhook">Webhook</label><input class="form-input" id="notify-webhook" type="password" autocomplete="new-password" spellcheck="false"></div>
    <div class="settings-field-row"><div class="settings-field"><label class="field-label" for="notify-secret">加签密钥 <span class="field-tag">选填</span></label><input class="form-input" id="notify-secret" type="password" autocomplete="new-password" spellcheck="false"></div><div class="settings-field"><label class="field-label" for="notify-prefix">关键词前缀</label><input class="form-input" id="notify-prefix" maxlength="50" placeholder="toBili"></div></div>
    <p class="caption field-hint">凭据留空会保留现有配置。修改后先保存再测试；关键词应与机器人安全设置一致。</p>
    <fieldset class="maintenance-events"><legend class="field-label">提醒事件</legend>${Object.entries(events).map(([key, label]) => `<label class="maintenance-check"><input id="notify-event-${key}" type="checkbox">${label}</label>`).join("")}</fieldset></div>
    <div class="settings-panel-foot"><p class="caption">通知失败不会改变任务结果</p><div class="settings-panel-actions"><button class="btn btn-secondary btn-pill" type="button" id="notify-clear">移除钉钉凭据</button><button class="btn btn-primary btn-pill" type="submit">保存通知设置</button></div></div></form>
    <details class="notification-recent"><summary>最近发送结果</summary><p class="caption">“已发送”表示钉钉接口接受，不代表用户已读。</p><div id="notification-history" aria-live="polite"><p class="maintenance-empty">正在加载…</p></div><button class="btn btn-secondary btn-pill" type="button" id="notify-refresh">刷新发送结果</button></details></section>
    <section class="panel llm-settings maintenance-settings"><div class="settings-panel-head"><div class="settings-panel-intro"><h2 class="section-title">开奖与自动清理</h2><p class="section-desc" id="cleanup-account-hint">正在读取当前账号设置…</p></div></div><form id="cleanup-settings-form"><fieldset id="cleanup-fields" disabled><div class="llm-settings-body">
    <div class="settings-field"><label class="field-label maintenance-check"><input id="cleanup-enabled" type="checkbox">开启自动清理</label><p class="field-help">确认未中奖后等待 24 小时，执行前再次核验。默认关闭。</p></div>
    <div class="settings-field-row"><div class="settings-field"><label class="field-label maintenance-check"><input id="cleanup-delete" type="checkbox">删除本人对应转发</label><p class="field-help">仅删除已核验原活动和作者归属的转发，保留本地历史。</p></div><div class="settings-field"><label class="field-label maintenance-check"><input id="cleanup-unfollow" type="checkbox">取关符合条件的 UP</label><p class="field-help">原有关注、保留名单，以及关联中奖或结果未知活动的 UP 均保留。</p></div></div>
    <div class="settings-field"><label class="field-label" for="cleanup-protected">UP 保留名单</label><textarea class="form-input" id="cleanup-protected" rows="3" placeholder="填写数字 UID，每行一个；也可用逗号分隔"></textarea><p class="caption field-hint">新参与自动记录关联；历史记录请在活动 → 开奖与清理中核对。</p></div></div>
    <div class="settings-panel-foot"><p class="caption">设置按当前账号保存</p><div class="settings-panel-actions"><button class="btn btn-secondary btn-pill" type="button" id="cleanup-settings-refresh">重新读取</button><button class="btn btn-primary btn-pill" type="submit">保存清理设置</button></div></div></fieldset></form></section>`);
  const section = el("section-activities");
  section.addEventListener("click", event => {
    const button = (event.target as HTMLElement).closest<HTMLElement>("[data-review-cleanup]");
    if (!button) return;
    input("cleanup-search").value = button.dataset.reviewCleanup || "";
    input("cleanup-filter").value = "";
    associationFilter = "";
    page = 1;
    section.querySelector<HTMLButtonElement>('[data-maintenance-view="cleanup"]')!.click();
  });
  section.insertAdjacentHTML("afterbegin", '<div class="maintenance-view-tabs" role="group" aria-label="活动子视图"><button class="btn btn-ghost active" data-maintenance-view="library" aria-pressed="true">活动列表</button><button class="btn btn-ghost" data-maintenance-view="pool" aria-pressed="false">预选池 <span id="pool-tab-count">0</span></button><button class="btn btn-ghost" data-maintenance-view="cleanup" aria-pressed="false">开奖与清理</button></div>');
  const pool = el("participation-pool");
  section.append(pool);
  pool.hidden = true;
  section.insertAdjacentHTML("beforeend", `<div class="panel" id="maintenance-panel" hidden><div class="maintenance-panel-head"><div><h2>开奖与清理</h2><p id="cleanup-count">包含当前账号全部已参与活动</p></div><div class="maintenance-actions"><button class="btn btn-ghost" id="cleanup-refresh">刷新</button><button class="btn btn-ghost" id="cleanup-discover-all" title="从最近动态重新扫描 5 页">匹配本人转发</button><button class="btn btn-ghost" id="cleanup-discover-more" disabled>继续扫描更早动态</button><button class="btn btn-ghost" id="cleanup-check">检查开奖结果</button><button class="btn btn-primary" id="cleanup-run">执行到期清理</button></div></div><p class="maintenance-muted">结果无法确定时保留为待确认。任务运行中可在日志面板取消。</p><p id="cleanup-scan-status" class="maintenance-muted" role="status"></p><div id="cleanup-association-filters" class="association-filters" role="group" aria-label="转发关联状态"></div><div class="cleanup-toolbar"><label>开奖结果<select id="cleanup-filter"><option value="">全部结果</option>${Object.entries(outcomes).map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></label><label>搜索活动<input id="cleanup-search" type="search" placeholder="标题或活动 ID"></label><button class="btn btn-ghost" id="cleanup-search-btn">搜索</button></div><div id="cleanup-list" aria-live="polite"></div><div class="maintenance-pager"><button class="btn btn-ghost" id="cleanup-prev">上一页</button><span id="cleanup-page"></span><button class="btn btn-ghost" id="cleanup-next">下一页</button></div></div>`);

  el('maintenance-panel').querySelector('.maintenance-panel-head')!.insertAdjacentHTML('afterend', `<section class="cleanup-recent" aria-label="近期删除与取关"><div class="maintenance-panel-head"><h3>近期删除 / 取关</h3><button class="btn btn-secondary" id="cleanup-history-refresh">刷新记录</button></div><div id="cleanup-statistics" class="cleanup-stat-grid"></div><p id="cleanup-explanation" class="maintenance-muted" role="status">正在读取清理状态…</p><details><summary>查看近期执行明细</summary><div id="cleanup-recent-list" aria-live="polite"></div><button class="btn btn-ghost" id="cleanup-history-more">加载更早记录</button></details></section>`);
  section.querySelectorAll<HTMLButtonElement>("[data-maintenance-view]").forEach(button => button.addEventListener("click", () => {
    const view = button.dataset.maintenanceView || "library";
    const clean = view === "cleanup";
    section.dataset.subview = view;
    section.querySelector<HTMLElement>(".activities-panel")!.hidden = view !== "library";
    pool.hidden = view !== "pool";
    el("maintenance-panel").hidden = !clean;
    section.querySelectorAll<HTMLButtonElement>("[data-maintenance-view]").forEach(b => { b.classList.toggle("active", b === button); b.setAttribute("aria-pressed", String(b === button)); });
    window.dispatchEvent(new CustomEvent("binggo:activity-view"));
    if (clean) { void loadCandidates(); void loadCleanupHistory().catch(error => { el('cleanup-explanation').textContent = `记录加载失败：${error.message}，请刷新重试。`; }); }
  }));
  const click = (id: string, fn: () => Promise<void>) => el<HTMLButtonElement>(id).addEventListener("click", event => void action(event.currentTarget as HTMLButtonElement, fn));
  click('cleanup-history-refresh', () => loadCleanupHistory());
  click('cleanup-history-more', () => loadCleanupHistory(true));
  el("notification-form").addEventListener("input", () => { settingsDirty = true; });
  el("cleanup-settings-form").addEventListener("input", () => { cleanupDirty = true; });
  el("notification-form").addEventListener("submit", event => {
    event.preventDefault();
    void action((event as SubmitEvent).submitter as HTMLButtonElement, async () => {
      await fetchJSON("/api/notifications/settings", body({ audio: input("notify-audio").checked, dingtalk: input("notify-dingtalk").checked, webhook: input("notify-webhook").value.trim() || null, sign_secret: input("notify-secret").value.trim() || null, prefix: input("notify-prefix").value, events: Object.keys(events).filter(key => input(`notify-event-${key}`).checked) }));
      settingsDirty = false; await loadSettings(); showToast("通知设置已保存", "success");
    });
  });
  el("cleanup-settings-form").addEventListener("submit", event => {
    event.preventDefault();
    void action((event as SubmitEvent).submitter as HTMLButtonElement, async () => {
      const current = await fetchJSON<CleanSettings>("/api/maintenance/settings");
      if (current.uid !== loadedCleanUid) { await loadSettings(true); throw new Error("账号已切换，请核对当前账号设置后保存"); }
      await fetchJSON("/api/maintenance/settings", body({ enabled: input("cleanup-enabled").checked, delete_reposts: input("cleanup-delete").checked, unfollow: input("cleanup-unfollow").checked, protected_ups: el<HTMLTextAreaElement>("cleanup-protected").value.split(/[\s,，]+/).filter(Boolean) }));
      cleanupDirty = false; await loadSettings(); showToast("当前账号清理设置已保存", "success");
    });
  });
  click("notify-clear", async () => {
    const s = await fetchJSON<NoticeSettings>("/api/notifications/settings");
    await fetchJSON("/api/notifications/settings", body({ audio: s.audio, dingtalk: false, webhook: "", sign_secret: "", prefix: s.prefix, events: s.events }));
    settingsDirty = false; await loadSettings(); showToast("钉钉凭据已移除", "success");
  });
  for (const channel of ["audio", "dingtalk"]) click(`notify-${channel}-test`, async () => {
    if (settingsDirty) throw new Error("请先保存通知设置再测试");
    await fetchJSON("/api/notifications/test", body({ channel }, "POST"));
    showToast("测试通知已加入发送队列", "success"); await loadHistory();
  });
  click("notify-refresh", loadHistory);
  click("cleanup-settings-refresh", () => loadSettings(true));
  click("cleanup-refresh", loadCandidates);
  click("cleanup-discover-all", () => start("/api/maintenance/discover?mode=recent"));
  click("cleanup-discover-more", () => start("/api/maintenance/discover?mode=continue"));
  el("cleanup-association-filters").addEventListener("click", event => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-association-filter]");
    if (!button) return;
    associationFilter = button.dataset.associationFilter || "";
    page = 1; void loadCandidates();
  });
  click("cleanup-check", () => start("/api/maintenance/check"));
  click("cleanup-run", () => start("/api/maintenance/clean"));
  click("cleanup-search-btn", async () => { page = 1; await loadCandidates(); });
  input("cleanup-filter").addEventListener("change", () => { page = 1; void loadCandidates(); });
  input("cleanup-search").addEventListener("keydown", event => { if (event.key === "Enter") { page = 1; void loadCandidates(); } });
  click("cleanup-prev", async () => { page = Math.max(1, page - 1); await loadCandidates(); });
  click("cleanup-next", async () => { page++; await loadCandidates(); });
  el("cleanup-list").addEventListener("click", event => {
    const choose = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-repost-choice]");
    if (choose) {
      choose.closest(".cleanup-card")!.querySelector<HTMLInputElement>('[name="repost_id"]')!.value = choose.dataset.repostChoice || "";
      showToast("已填入候选，请核对后保存关联", "info");
      return;
    }
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-clean]");
    if (button) void action(button, () => rowAction(button));
  });
  window.addEventListener("binggo:section", () => {
    if (el("section-settings").classList.contains("active")) void loadSettings().catch(() => {});
    if (section.classList.contains("active") && section.dataset.subview === "cleanup") void loadCandidates();
  });
  window.addEventListener("binggo:job-completed", event => {
    const job = (event as CustomEvent).detail;
    if (job.action?.startsWith('maintenance_') && section.dataset.subview === 'cleanup') void loadCleanupHistory().catch(() => {});
    if (job.action === "maintenance_discover" && job.result?.repost_ids) {
      const ids: string[] = job.result.repost_ids;
      const card = Array.from(el("cleanup-list").querySelectorAll<HTMLElement>("[data-did]")).find(c => c.dataset.did === job.result.dynamic_id);
      const target = card?.querySelector<HTMLElement>(".cleanup-discovery");
      if (target) target.innerHTML = ids.length ? `点击填入，核对后再保存：<div class="maintenance-actions">${ids.map(id => `<button class="btn btn-ghost" data-repost-choice="${esc(id)}">${esc(id)}</button>`).join("")}</div>` : "没有找到转发候选，请保留待核对。";
      return;
    }
    if (section.classList.contains("active") && section.dataset.subview === "cleanup" && !(job.action === "maintenance_cycle" && el("cleanup-list").querySelector("details[open]"))) void loadCandidates();
    if (el("section-settings").classList.contains("active")) void loadSettings().catch(() => {});
  });
  window.setInterval(() => { if (el("section-settings").classList.contains("active")) void loadHistory().catch(() => {}); }, 5000);
  void loadSettings().catch(() => { el("notification-history").textContent = "设置读取失败，请重新进入设置页重试。"; });
}

export function maintenanceSnapshot() {
  return {page, associationFilter, view: document.getElementById("section-activities")?.dataset.subview || "library",
    q: input("cleanup-search")?.value || "", outcome: input("cleanup-filter")?.value || ""};
}
export function restoreMaintenance(saved?: ReturnType<typeof maintenanceSnapshot>) {
  page = saved?.page || 1; associationFilter = saved?.associationFilter || "";
  rows = []; historyBefore = null; loadedCleanUid = ""; cleanupDirty = false;
  input("cleanup-search").value = saved?.q || ""; input("cleanup-filter").value = saved?.outcome || "";
  el("cleanup-list").innerHTML = "";
  document.getElementById("message-review")?.remove();
  document.querySelector<HTMLButtonElement>(`[data-maintenance-view="${saved?.view || 'library'}"]`)?.click();
  void loadSettings(true).catch(() => {});
}

export async function openMessageReview() {
  const generation = accountGeneration;
  activateSection("activities");
  document.querySelector<HTMLButtonElement>('[data-maintenance-view="cleanup"]')?.click();
  await loadMessageReview();
  if (generation !== accountGeneration) return;
  const panel = document.getElementById("message-review");
  panel?.scrollIntoView({block: "start", behavior: "auto"});
  panel?.focus({preventScroll: true});
}

export async function loadMessageReview() {
  let panel = document.getElementById("message-review");
  if (!panel) {
    panel = document.createElement("section"); panel.id = "message-review"; panel.className = "message-review";
    panel.tabIndex = -1; panel.setAttribute("aria-label", "领奖消息核查");
    document.querySelector("#maintenance-panel .maintenance-panel-head")?.after(panel);
    panel.innerHTML = '<strong>领奖消息核查</strong><p role="status">正在读取当前账号的待核查消息…</p>';
    panel.addEventListener("click", async event => {
      const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-resolve-message]");
      if (!button) return;
      const key = button.dataset.messageKey!;
      const card = button.closest(".message-review-item")!;
      const dynamic_id = card.querySelector<HTMLInputElement>("input")?.value.trim() || "";
      button.disabled = true;
      try {
        await fetchJSON("/api/maintenance/messages/resolve", {method:"POST", headers:{"Content-Type":"application/json"},
          body:JSON.stringify({key, resolution:button.dataset.resolveMessage, dynamic_id})});
        await loadMessageReview(); await loadCandidates();
        window.dispatchEvent(new CustomEvent('tobili:messages-reviewed'));
      } catch (error) { showToast((error as Error).message, "error"); }
      finally { button.disabled = false; }
    });
  }
  try {
    const result = await fetchJSON<{blocked:boolean; pending:number; error:string; checked_at:number; items:Array<{key:string; type:string; talker_name:string; talker_id:number; text:string; timestamp:number; suggested_activity_ids?:string[]}>}>("/api/maintenance/messages");
    panel.innerHTML = `<strong>领奖消息核查${result.blocked ? ' · 清理已暂停' : ''}</strong><p class="caption">当前账号 UID ${esc(activeUid)} · 检查时间：${esc(date(result.checked_at))}</p><p>这里是私信和 @我的 消息，不是已确认的中奖数。请逐条判断是否与领奖有关：无关的标为“与中奖无关”；确实中奖的关联活动并保留；拿不准就先不处理。</p><p>${result.error ? esc(result.error) : result.pending ? `${result.pending} 条消息待核查。仅打开或阅读不会解除清理暂停。` : result.blocked ? '待核查消息已处理；请手动重新执行清理，系统会再次检查新消息。' : '未发现待核查消息。执行清理前仍会重新检查。'}</p>` + result.items.map(item => `<div class="message-review-item"><strong>${item.type === 'dm' ? '我的消息（私信）' : '@我的'} · ${esc(item.talker_name || String(item.talker_id || '未知发送者'))}</strong><small> · ${new Date(item.timestamp * 1000).toLocaleString('zh-CN')}</small><p>${esc(item.text)}</p>${item.suggested_activity_ids?.length ? `<p class="caption">可能关联的活动：${item.suggested_activity_ids.map(esc).join("、")}（请核对）</p>` : ""}<div class="action-row"><button class="btn btn-secondary" data-resolve-message="irrelevant" data-message-key="${esc(item.key)}">与中奖无关</button><input class="form-input" aria-label="中奖活动 ID" placeholder="关联已参与的活动 ID" /><button class="btn btn-primary" data-resolve-message="won" data-message-key="${esc(item.key)}">确认中奖并保留</button><a class="btn btn-ghost" href="https://message.bilibili.com/#/${item.type === 'dm' ? 'whisper' : 'notify/at'}" target="_blank" rel="noopener noreferrer">查看 B 站消息</a></div><small>外部浏览器可能登录其他账号，请核对 UID；未处理的消息继续保留。</small></div>`).join('');
  } catch (error) {
    if ((error as Error).name === 'StaleAccountError') return;
    panel.innerHTML = `<strong>领奖消息暂时未能加载</strong><p role="alert">${esc((error as Error).message)}。请点击侧栏消息入口或本页“刷新”重试；未处理的消息仍会阻止清理。</p>`;
    throw error;
  }
}
