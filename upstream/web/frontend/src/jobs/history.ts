// @ts-nocheck
import { fetchJSON } from "../api/client";
import { sanitizeUserText } from "../utils/text";
import { showToast } from "../shell/toast";

let latest = null;
let selected = null;
let nextBefore = null;
let requestVersion = 0;
const labels = { success: "成功", error: "失败", cancelled: "已取消", interrupted: "已中断", running: "运行中" };
const date = (value) => value ? new Date(value * 1000).toLocaleString("zh-CN", { hour12: false }) : "—";

export function viewingHistory() { return selected !== null; }

function render() {
  const job = selected || latest;
  const message = sanitizeUserText(job?.message) || "暂无任务";
  for (const id of ["job-message", "log-dock-summary"]) {
    const element = document.getElementById(id);
    if (element) element.textContent = message;
  }
  const tone = ["running", "success", "error"].includes(job?.state) ? job.state
    : ["cancelled", "interrupted"].includes(job?.state) ? "cancelled" : "idle";
  const dock = document.getElementById("log-dock");
  if (dock) {
    dock.dataset.tone = tone;
    for (const value of ["idle", "running", "success", "error"]) dock.classList.toggle(`is-${value}`, tone === value);
  }
  const status = document.getElementById("log-dock-status");
  if (status) {
    status.textContent = labels[job?.state] || "空闲";
    status.className = `log-dock-status is-${tone}`;
  }
  const log = document.getElementById("job-log");
  const meta = document.getElementById("job-history-meta");
  if (log) log.textContent = sanitizeUserText(job?.log) || (job?.state === "running" ? "等待执行日志…" : "暂无执行日志");
  if (meta) {
    const elapsed = job?.finished_at && job?.started_at ? ` · 用时 ${Math.max(0, job.finished_at - job.started_at)} 秒` : "";
    meta.textContent = job?.id ? `#${job.id} · ${job.label || job.action} · ${labels[job.state] || job.state} · ${date(job.started_at)}${elapsed}` : "历史保留 30 天；每条最多 256 KB，超出时标注截断。";
  }
  const follow = document.getElementById("job-log-live");
  if (follow) follow.hidden = !selected;
}

export function renderJobLog(job) {
  if (job?.id || !latest) latest = job;
  render();
}

export async function loadJobHistory(older = false) {
  const select = document.getElementById("job-history-select");
  if (!select) return;
  const response = await fetchJSON(`/api/jobs/history?limit=50${older && nextBefore ? `&before_id=${nextBefore}` : ""}`);
  if (!older) select.replaceChildren(new Option("当前 / 最近任务", ""));
  const existing = new Set([...select.options].map(option => option.value));
  for (const job of response.items || []) {
    if (!existing.has(String(job.id))) select.add(new Option(`#${job.id} ${labels[job.state] || job.state} · ${job.label || job.action} · ${date(job.started_at)}`, String(job.id)));
  }
  if (selected && ![...select.options].some(option => option.value === String(selected.id))) {
    select.add(new Option(`#${selected.id} · ${selected.label || selected.action}`, String(selected.id)));
  }
  select.value = selected ? String(selected.id) : "";
  nextBefore = response.next_before_id;
  document.getElementById("job-history-more").hidden = !nextBefore;
}

export function bindJobHistory() {
  document.getElementById("job-history-select")?.addEventListener("change", async (event) => {
    const version = ++requestVersion;
    const id = event.target.value;
    if (!id) { selected = null; render(); return; }
    try {
      const job = await fetchJSON(`/api/jobs/history/${id}`);
      if (version !== requestVersion) return;
      selected = job;
      render();
    } catch (error) { showToast(String(error.message || error), "error"); }
  });
  document.getElementById("job-log-live")?.addEventListener("click", () => {
    ++requestVersion;
    selected = null;
    document.getElementById("job-history-select").value = "";
    render();
  });
  document.getElementById("job-history-more")?.addEventListener("click", () => loadJobHistory(true).catch(error => showToast(error.message, "error")));
  document.getElementById("job-log-copy")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(document.getElementById("job-history-meta").textContent + "\n" + document.getElementById("job-log").textContent);
      showToast("日志已复制", "success");
    } catch { showToast("复制失败，可以使用下载日志", "error"); }
  });
  document.getElementById("job-log-download")?.addEventListener("click", () => {
    const text = document.getElementById("job-history-meta").textContent + "\n\n" + document.getElementById("job-log").textContent;
    const url = URL.createObjectURL(new Blob(["\uFEFF", text], {type: "text/plain;charset=utf-8"}));
    const a = document.createElement("a");
    a.href = url;
    a.download = `toBili-任务-${(selected || latest)?.id || "日志"}.txt`;
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

export function resetJobHistoryAccount() {
  selected = null; latest = null; nextBefore = null; requestVersion++;
  const select = document.getElementById('job-history-select');
  if (select) select.innerHTML = '<option value="">当前任务</option>';
  render();
  loadJobHistory().catch(() => {});
}
