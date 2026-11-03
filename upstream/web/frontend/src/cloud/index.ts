import { fetchJSON } from "../api/client";
import { escapeHtml } from "../utils/text";
import { showToast } from "../shell/toast";
import { openAppConfirm } from "../shell/confirm";

type Status = { connected: boolean; server: string; username: string; device_id: string; bili_uid: string; last_sync: number; message: string; conflicts: number };
type Conflict = { kind: string; key: string; local: unknown; remote: unknown };
const esc = (value: unknown) => escapeHtml(String(value ?? ""));
const el = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const post = (value: unknown = {}): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });

export function bindCloud() {
  const stack = document.querySelector("#section-settings .settings-stack");
  if (!stack || document.getElementById("cloud-panel")) return;
  stack.insertAdjacentHTML("beforeend", `<section class="panel llm-settings" id="cloud-panel">
    <div class="settings-panel-head"><div class="settings-panel-intro"><h2 class="section-title">多端同步</h2><p class="section-desc">Win、Mac 和网站共享活动资料与各 UID 的转发关联。筛选、归档、任务及清理权限仍由本机管理。</p></div></div>
    <div class="llm-settings-body"><p id="cloud-state" role="status" aria-live="polite">正在读取连接状态…</p>
    <form id="cloud-login"><fieldset id="cloud-login-fields" class="cloud-fields">
      <label class="settings-field">中转服务器<input class="form-input" id="cloud-server" type="url" placeholder="https://bili.example.com" required autocomplete="url"></label>
      <div class="settings-field-row"><label class="settings-field">应用账号<input class="form-input" id="cloud-user" required minlength="3" maxlength="64" autocomplete="username"></label><label class="settings-field">应用密码<input class="form-input" id="cloud-password" type="password" required minlength="12" maxlength="128" autocomplete="current-password"></label></div>
      <label class="settings-field">本机名称<input class="form-input" id="cloud-device" required maxlength="80" value="我的桌面设备" autocomplete="off"></label>
      <label class="maintenance-check"><input id="cloud-register" type="checkbox">首次使用，注册应用账号</label>
      <label class="settings-field" id="cloud-invite-field" hidden>注册邀请码<input class="form-input" id="cloud-invite" type="password" maxlength="256" autocomplete="off"></label>
      <button class="btn btn-primary" type="submit">连接服务器</button>
    </fieldset></form>
    <div id="cloud-connected" hidden><p class="field-help" id="cloud-account-info"></p><div class="maintenance-actions cloud-actions">
      <button class="btn btn-primary" id="cloud-bind" hidden>绑定当前 B 站账号</button><button class="btn btn-secondary" id="cloud-sync">立即同步</button>
      <button class="btn btn-ghost" id="cloud-execution" hidden>查看待核实任务</button><button class="btn btn-ghost" id="cloud-conflicts-button" hidden>核对同步冲突</button><a class="btn btn-ghost" id="cloud-mobile" target="_blank" rel="noopener noreferrer">打开活动网站</a><button class="btn btn-ghost" id="cloud-disconnect">解绑本机并退出</button>
    </div><p class="field-help">仅上传服务端缺少的活动，按活动 ID 去重。开奖结束超过 7 天的资料由服务端清理；本机记录不受影响。转发关联长期保留，换机自动读取；参与前核验本人转发，避免重复。</p></div>
    <div id="cloud-execution-detail" role="status"></div><div id="cloud-conflict-list"></div></div></section>`);

  const input = (id: string) => el<HTMLInputElement>(id);
  let busy = false;
  const refresh = async () => {
    const s = await fetchJSON<Status>("/api/cloud/status");
    el("cloud-state").textContent = `${s.message}${s.last_sync ? ` · 上次同步 ${new Date(s.last_sync * 1000).toLocaleString("zh-CN")}` : ""}${s.conflicts ? ` · ${s.conflicts} 项冲突` : ""}`;
    el("cloud-login").hidden = s.connected;
    el("cloud-connected").hidden = !s.connected;
    el("cloud-account-info").textContent = s.connected ? `应用账号 ${s.username} · 活动资料库 · ${s.server}` : "";
    if (s.connected) el<HTMLAnchorElement>("cloud-mobile").href = s.server;
  };
  const act = async (button: HTMLButtonElement, fn: () => Promise<void>) => {
    if (busy) return;
    busy = true; button.disabled = true;
    try { await fn(); await refresh(); }
    catch (e) { showToast(e instanceof Error ? e.message : "云端操作失败", "error"); }
    finally { busy = false; button.disabled = false; }
  };
  input("cloud-register").addEventListener("change", () => {
    el("cloud-invite-field").hidden = !input("cloud-register").checked;
    input("cloud-password").autocomplete = input("cloud-register").checked ? "new-password" : "current-password";
  });
  el("cloud-login").addEventListener("submit", event => {
    event.preventDefault();
    const button = el<HTMLFormElement>("cloud-login").querySelector<HTMLButtonElement>('button[type="submit"]')!;
    void act(button, async () => {
      const values = { server: input("cloud-server").value, username: input("cloud-user").value.trim(), password: input("cloud-password").value,
        device_name: input("cloud-device").value.trim(), register: input("cloud-register").checked, invite: input("cloud-invite").value };
      try { await fetchJSON("/api/cloud/connect", post(values)); }
      finally { input("cloud-password").value = ""; input("cloud-invite").value = ""; }
      showToast("活动资料库已连接，可立即同步", "success");
    });
  });
  for (const [id, route, message] of [["cloud-bind", "bind", "已绑定当前 B 站账号"], ["cloud-sync", "sync", "同步完成"], ["cloud-disconnect", "disconnect", "本机已解绑"]]) {
    const button = el<HTMLButtonElement>(id);
    button.addEventListener("click", () => void act(button, async () => {
      await fetchJSON(`/api/cloud/${route}`, post());
      el("cloud-conflict-list").replaceChildren();
      showToast(message, "success");
    }));
  }
  const loadConflicts = async () => {
    const result = await fetchJSON<{ items: Conflict[] }>("/api/cloud/conflicts");
    el("cloud-conflict-list").innerHTML = result.items.length ? `<p class="field-help">两端修改了同一条记录，执行已暂停。核对后选择要保留的版本。</p>${result.items.map(c => `<article class="cloud-conflict"><strong>${esc(c.kind)} · ${esc(c.key)}</strong><div class="settings-field-row"><div><p>本机版本</p><pre>${esc(JSON.stringify(c.local, null, 2))}</pre></div><div><p>云端版本</p><pre>${esc(JSON.stringify(c.remote, null, 2))}</pre></div></div><div class="maintenance-actions"><button class="btn btn-secondary" data-cloud-choice="remote" data-kind="${esc(c.kind)}" data-key="${esc(c.key)}">保留云端版本</button><button class="btn btn-ghost" data-cloud-choice="local" data-kind="${esc(c.kind)}" data-key="${esc(c.key)}">使用本机版本</button></div></article>`).join("")}` : '<p class="field-help">没有待核对的同步冲突。</p>';
  };
  const executionButton = el<HTMLButtonElement>("cloud-execution");
  executionButton.addEventListener("click", () => void act(executionButton, async () => {
    const result = await fetchJSON<{ account: { quarantine?: boolean; executor?: string }; tasks: { id: string; action: string; message: string; updated_at: number }[] }>("/api/cloud/execution");
    el("cloud-execution-detail").textContent = result.tasks.map(t => `${t.action} · ${new Date(t.updated_at * 1000).toLocaleString("zh-CN")} · ${t.message}（${t.id}）`).join("\n") || "没有待核实任务。";
    if (!result.account.quarantine) return;
    const confirmed = await openAppConfirm({
      title: "核实上次执行结果后，解除暂停",
      desc: el("cloud-execution-detail").textContent || "上次任务结果待核实",
      bullets: ["确认原执行设备已停止任务。", "查看任务日志及 B 站的参与、转发、关注记录，确认实际结果。", "解除后原任务不会自动重跑；预选池保留已完成记录及剩余活动。"],
      confirmLabel: "已核查，解除暂停",
      cancelLabel: "稍后核对",
    });
    if (!confirmed) return;
    await fetchJSON("/api/cloud/acknowledge", post());
    el("cloud-execution-detail").textContent = "已解除暂停，原任务不会自动重跑。";
    showToast("已解除云端暂停", "success");
  }));
  const conflictButton = el<HTMLButtonElement>("cloud-conflicts-button");
  conflictButton.addEventListener("click", () => void act(conflictButton, loadConflicts));
  el("cloud-conflict-list").addEventListener("click", event => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-cloud-choice]");
    if (!button) return;
    void act(button, async () => {
      await fetchJSON("/api/cloud/resolve", post({ kind: button.dataset.kind, key: button.dataset.key, choice: button.dataset.cloudChoice }));
      await loadConflicts();
    });
  });
  void refresh().catch(() => { el("cloud-state").textContent = "无法读取同步状态，请刷新重试"; });
  window.setInterval(() => {
    if (!document.hidden && !busy) void refresh().catch(() => {});
  }, 10000);
}
