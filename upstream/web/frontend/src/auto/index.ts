// @ts-nocheck
/* eslint-disable */
/** Migrated from web/static/app.js — logic preserved. */

import { state } from "../state";
import { fetchJSON } from "../api/client";
import { autoDock, autoDockBadge, autoDockCountdown, autoDockFatal, autoDockFatalText, autoDockHint, autoDockJob, autoDockPanel, autoDockPhase, autoDockPipeline, autoDockScheduler, autoDockStartBtn, autoDockStatus, autoDockStopBtn, autoDockToggle, autoDockToggleMeta } from "../dom";
import { openAppConfirm } from "../shell/confirm";
import { showToast } from "../shell/toast";
import { formatAutoCountdown } from "../utils/format";
import { prefersReducedMotion } from "../utils/motion";
import { sanitizeUserText, escapeHtml } from "../utils/text";

const scheduleLabels = {refresh_discovery: "B站话题发现", refresh_all: "一键更新活动链接", refresh_watch: "更新监控用户动态", refresh_status: "刷新任务状态", participate_pool: "预选池参与"};
let scheduleUid = null;
let scheduleDirty = false;
export function renderScheduleConfig(status) {
  const form = document.getElementById("auto-config-form");
  if (!form || !status.config) return;
  if (scheduleUid === status.uid && (scheduleDirty || form.contains(document.activeElement))) return;
  scheduleUid = status.uid;
  scheduleDirty = false;
  const cfg = status.config;
  form.innerHTML = Object.entries(scheduleLabels).map(([key, label]) => `<div class="auto-config-row"><label><input type="checkbox" name="${key}" ${cfg[key].enabled ? "checked" : ""}>${label}</label><label>每 <input type="number" name="${key}_minutes" value="${cfg[key].minutes}" min="5" max="10080" required aria-label="${label}周期（分钟）"> 分钟</label></div>`).join("") +
    `<label class="auto-config-mode"><input type="checkbox" name="nonstop" ${cfg.nonstop ? "checked" : ""}>7×24 挂机模式</label><p class="muted">自动更新来源 → 参与活动列表所有可参与活动（不受预选池限制）→ 开奖核查与安全清理。跳过已参加、已移出和不可参与项；失败或人工暂停后不自动继续。</p><div class="auto-config-row"><label>挂机刷新周期（分钟）<input type="number" name="nonstop_minutes" value="${cfg.nonstop_minutes}" min="5" max="10080" required></label><label>开奖清理周期（分钟）<input type="number" name="maintenance_minutes" value="${cfg.maintenance_minutes}" min="5" max="10080" required></label></div><label>每小时参与上限（当前账号）<input type="number" name="nonstop_hourly_limit" value="${cfg.nonstop_hourly_limit ?? 20}" min="1" max="500" required></label><p class="muted">按最近 60 分钟的尝试次数计数，失败也计入；重启不清零，达到上限后自动等待。</p><div id="nonstop-usage"></div><button type="button" id="nonstop-resume" class="btn" ${status.nonstop_usage?.paused ? "" : "hidden"}>恢复挂机参与（不解除清理阻断）</button><p class="muted">挂机开启时启用全部刷新功能；参与检查频率沿用上方参与周期。各周期从配置生效后计时。</p><button type="submit" class="btn">保存当前账号计划</button>`;
  const usage = document.getElementById("nonstop-usage");
  if (usage) usage.textContent = `最近一小时已尝试 ${status.nonstop_usage?.used ?? 0} 个${status.nonstop_usage?.paused ? " · 挂机参与已暂停" : ""}`;
  document.getElementById("nonstop-resume")?.addEventListener("click", async () => {
    const ok = await openAppConfirm({title: "恢复挂机参与？", desc: "仅恢复当前账号的活动列表参与，仍受每小时上限限制；不会解除消息核查产生的清理阻断。", confirmLabel: "恢复参与", cancelLabel: "取消"});
    if (!ok) return;
    try { await fetchJSON("/api/auto/nonstop/resume", {method: "POST"}); await fetchAutoStatus(); } catch(e) {showToast(e.message,"error");}
  });
  form.oninput = () => { scheduleDirty = true; };
  form.onsubmit = async event => { event.preventDefault(); try { await saveScheduleConfig(); showToast("计划已保存，未启动新任务", "success"); } catch(e) { showToast(e.message, "error"); } };
}
export async function saveScheduleConfig() {
  const form = document.getElementById("auto-config-form");
  if (!form || !form.reportValidity()) throw Error("周期须为 5 至 10080 分钟，每小时上限须为 1 至 500 个");
  const cfg = {};
  for (const key of Object.keys(scheduleLabels)) cfg[key] = {enabled: form.elements.namedItem(key).checked, minutes: Number(form.elements.namedItem(key + "_minutes").value)};
  cfg.nonstop = form.elements.namedItem("nonstop").checked;
  cfg.nonstop_hourly_limit = Number(form.elements.namedItem("nonstop_hourly_limit").value);
  cfg.nonstop_minutes = Number(form.elements.namedItem("nonstop_minutes").value);
  cfg.maintenance_minutes = Number(form.elements.namedItem("maintenance_minutes").value);
  if (cfg.nonstop && !state.autoScheduler?.config?.nonstop) {
    const ok = await openAppConfirm({title: "启用当前账号的挂机计划？", desc: "启动调度后会自动参与活动库内所有可参与活动，并按现有清理设置核查开奖及清理。消息待确认时仍会暂停清理。", confirmLabel: "保存挂机计划", cancelLabel: "取消"});
    if (!ok) throw Error("未启用挂机计划");
  }
  await fetchJSON("/api/auto/config", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(cfg)});
  scheduleDirty = false;
  await fetchAutoStatus();
}

export function setAutoDockOpen(open) {
  state.autoDockOpen = open;
  if (!autoDockPanel || !autoDockToggle) return;
  autoDockPanel.classList.toggle("is-open", open);
  autoDockPanel.setAttribute("aria-hidden", String(!open));
  autoDockToggle.setAttribute("aria-expanded", String(open));
  autoDock?.classList.toggle("open", open);
  if (open) {
    if (!prefersReducedMotion()) {
      autoDockPanel.classList.remove("is-entering");
      void autoDockPanel.offsetWidth;
      autoDockPanel.classList.add("is-entering");
    }
    fetchAutoStatus().catch(() => {});
    ensureAutoPolling();
    ensureAutoCountdown();
    tickAutoCountdown();
  } else {
    autoDockPanel.classList.remove("is-entering");
    if (!(state.autoScheduler && state.autoScheduler.state === "running")) {
      stopAutoPolling();
    }
  }
}

export function getAutoCountdownSeconds(targetUnix) {
  if (!targetUnix) return null;
  const nowSec = Math.floor((Date.now() + state.autoServerSkewMs) / 1000);
  return Math.max(0, Number(targetUnix) - nowSec);
}

export function toggleAutoDock(forceOpen) {
  const next = typeof forceOpen === "boolean" ? forceOpen : !state.autoDockOpen;
  setAutoDockOpen(next);
}

export function resolveAutoSchedulerText(status) {
  const schedulerState = String(status?.state || "idle");
  if (schedulerState === "fatal") {
    return sanitizeUserText(status.fatal_error || status.message || "已停机");
  }
  if (schedulerState !== "running") {
    return sanitizeUserText(status.state_label || status.message || "尚未启动");
  }
  const phase = sanitizeUserText(status.current_phase || "");
  if (phase && phase !== "—") return phase;
  return sanitizeUserText(status.message || "调度运行中");
}

export function resolveAutoJobText(status) {
  const jobProbe = status?.job_probe || {};
  const jobState = String(jobProbe.job_state || "idle");
  const jobLabel = sanitizeUserText(jobProbe.job_label || jobProbe.job_action || "");
  if (jobState === "running") {
    return jobLabel ? `运行中 · ${jobLabel}` : "运行中";
  }
  if (jobState === "idle") return "空闲";
  if (jobState === "success") return jobLabel ? `刚完成 · ${jobLabel}` : "刚完成";
  if (jobState === "error") return jobLabel ? `出错 · ${jobLabel}` : "出错";
  return jobLabel || jobState || "—";
}

export function resolveAutoJobTone(status) {
  const jobState = String(status?.job_probe?.job_state || "idle");
  if (jobState === "running") return "running";
  if (jobState === "error") return "error";
  if (jobState === "success") return "success";
  return "idle";
}

export function renderAutoPipeline(pipeline) {
  if (!autoDockPipeline) return;
  const steps = Array.isArray(pipeline?.steps) ? pipeline.steps : [];
  if (!steps.length) {
    autoDockPipeline.innerHTML = "";
    return;
  }
  autoDockPipeline.innerHTML = steps
    .map((step, index) => {
      const label = escapeHtml(sanitizeUserText(step.label || step.action || "") || "");
      const rawStatus = String(step.status || "pending");
      const status = ["pending", "running", "success", "error", "skipped", "waiting"].includes(rawStatus)
        ? rawStatus
        : "pending";
      const stepIndex = Number.isFinite(step.index) ? Number(step.index) + 1 : index + 1;
      return `<div class="auto-dock-step" data-status="${escapeHtml(status)}" data-index="${stepIndex}"><span class="auto-dock-step-label">${label}</span></div>`;
    })
    .join("");
}

export function updateAutoCollapsedMeta(status) {
  if (!autoDockToggleMeta) return;
  const schedulerState = String(status?.state || "idle");
  if (schedulerState === "running") {
    const countdown = formatAutoCountdown(status?.next_slot?.at_unix);
    autoDockToggleMeta.hidden = false;
    autoDockToggleMeta.textContent = countdown === "—" ? "…" : countdown;
    return;
  }
  // fatal / idle：角标或默认文案已够，折叠态不再重复「已停机」
  autoDockToggleMeta.hidden = true;
  autoDockToggleMeta.textContent = "";
}

export function renderAutoDock(status) {
  if (!status) return;
  state.autoScheduler = status;
  renderScheduleConfig(status);
  if (status.server_now_unix) {
    state.autoServerSkewMs = status.server_now_unix * 1000 - Date.now();
  }

  const schedulerState = String(status.state || "idle");
  const stateLabel = sanitizeUserText(status.state_label || status.message || "尚未启动");
  const message = sanitizeUserText(status.message || "");
  const phase = sanitizeUserText(status.current_phase || "—");
  const hint = sanitizeUserText(status.next_hint || status.next_slot?.hint || "—");

  if (autoDockStatus) {
    // 头部只保留短状态，长进度细节交给「调度 / 抽奖任务」行
    autoDockStatus.textContent =
      schedulerState === "running"
        ? sanitizeUserText(status.state_label || "调度器运行中")
        : message || stateLabel;
  }
  if (autoDockPhase) {
    autoDockPhase.textContent = phase;
    autoDockPhase.classList.toggle("is-live", schedulerState === "running");
  }
  if (autoDockHint) {
    autoDockHint.textContent = schedulerState === "running" ? hint : "启动后按下方计划自动执行";
  }
  const hero = document.querySelector(".auto-dock-hero");
  const pipelineBlock = document.querySelector(".auto-dock-pipeline-block");
  const countdownSec = getAutoCountdownSeconds(status.next_slot?.at_unix);
  const urgent = schedulerState === "running" && countdownSec !== null && countdownSec > 0 && countdownSec < 60;
  if (hero) {
    hero.classList.toggle("is-live", schedulerState === "running");
    hero.classList.toggle("is-urgent", urgent);
  }
  if (pipelineBlock) {
    pipelineBlock.classList.toggle("is-active", Boolean(status.refresh_pipeline?.active));
  }
  if (autoDockScheduler) {
    autoDockScheduler.textContent = resolveAutoSchedulerText(status);
    autoDockScheduler.dataset.tone = schedulerState === "fatal" ? "error" : schedulerState === "running" ? "running" : "idle";
  }
  if (autoDockJob) {
    autoDockJob.textContent = resolveAutoJobText(status);
    autoDockJob.dataset.tone = resolveAutoJobTone(status);
  }
  tickAutoCountdown();
  renderAutoPipeline(status.refresh_pipeline);
  updateAutoCollapsedMeta(status);

  const running = schedulerState === "running";
  const fatal = schedulerState === "fatal";

  autoDock?.classList.toggle("fatal", fatal);
  autoDock?.classList.toggle("is-running", running);
  if (autoDockBadge) {
    autoDockBadge.hidden = !running && !fatal;
    autoDockBadge.textContent = fatal ? "已停机" : "运行中";
    autoDockBadge.classList.toggle("fatal", fatal);
  }
  if (autoDockFatal) {
    autoDockFatal.hidden = !fatal;
  }
  if (autoDockFatalText) {
    autoDockFatalText.textContent = sanitizeUserText(status.fatal_error || "");
  }
  if (autoDockStartBtn) {
    autoDockStartBtn.hidden = running;
  }
  if (autoDockStopBtn) {
    autoDockStopBtn.hidden = !running;
  }

  if (running || state.autoDockOpen) {
    if (state.sseHealthy) {
      ensureAutoCountdown();
      // SSE 健康时停 2s 轮询，倒计时仍本地跑
      if (state.autoPollTimer) {
        window.clearInterval(state.autoPollTimer);
        state.autoPollTimer = null;
      }
    } else {
      ensureAutoPolling();
      ensureAutoCountdown();
    }
  } else if (!state.sseHealthy) {
    stopAutoPolling();
  } else {
    ensureAutoCountdown();
  }
}

export function tickAutoCountdown() {
  const status = state.autoScheduler;
  if (!autoDockCountdown) return;
  const targetUnix = status?.next_slot?.at_unix;
  autoDockCountdown.textContent = status?.state === "running" ? formatAutoCountdown(targetUnix) : "—";
  const countdownSec = getAutoCountdownSeconds(targetUnix);
  const running = String(status?.state || "") === "running";
  const urgent = running && countdownSec !== null && countdownSec > 0 && countdownSec < 60;
  autoDockCountdown.classList.toggle("is-urgent", urgent);
  document.querySelector(".auto-dock-hero")?.classList.toggle("is-urgent", urgent);
  updateAutoCollapsedMeta(status);
}

export function ensureAutoCountdown() {
  if (state.autoCountdownTimer) return;
  state.autoCountdownTimer = window.setInterval(tickAutoCountdown, 1000);
}

export function ensureAutoPolling() {
  if (state.sseHealthy && state.eventSource) {
    ensureAutoCountdown();
    return;
  }
  if (!state.autoPollTimer) {
    state.autoPollTimer = window.setInterval(() => {
      if (state.sseHealthy && state.eventSource) return;
      fetchAutoStatus().catch(() => {});
    }, 2000);
  }
  ensureAutoCountdown();
}

export function stopAutoPolling() {
  if (state.autoPollTimer) {
    window.clearInterval(state.autoPollTimer);
    state.autoPollTimer = null;
  }
  if (state.autoCountdownTimer) {
    window.clearInterval(state.autoCountdownTimer);
    state.autoCountdownTimer = null;
  }
}

export async function fetchAutoStatus() {
  const status = await fetchJSON("/api/auto/status");
  renderAutoDock(status);
  return status;
}

export async function startAutoScheduler() {
  if (scheduleDirty) await saveScheduleConfig();
  await fetchJSON("/api/auto/start", { method: "POST" });
  await fetchAutoStatus();
  ensureAutoPolling();
  showToast("定时调度已启动", "success");
}

export async function stopAutoScheduler() {
  const ok = await openAppConfirm({
    eyebrow: "定时点击",
    title: "确定停止调度？",
    desc: "停止调度只会停下定时点击监视器，不会取消正在运行的抽奖任务。",
    confirmLabel: "停止调度",
    cancelLabel: "继续运行",
  });
  if (!ok) return;
  await fetchJSON("/api/auto/stop", { method: "POST" });
  await fetchAutoStatus();
  showToast("定时调度已停止", "info");
}

export function bindAutoDock() {
  autoDockToggle?.addEventListener("click", () => toggleAutoDock());
  document.getElementById("auto-dock-collapse")?.addEventListener("click", () => toggleAutoDock(false));
  autoDockStartBtn?.addEventListener("click", () => {
    autoDockStartBtn.disabled = true;
    startAutoScheduler()
      .catch((error) => showToast(sanitizeUserText(error.message || error) || "启动失败", "error"))
      .finally(() => {
        autoDockStartBtn.disabled = false;
      });
  });
  autoDockStopBtn?.addEventListener("click", () => {
    autoDockStopBtn.disabled = true;
    stopAutoScheduler()
      .catch((error) => showToast(sanitizeUserText(error.message || error) || "停止失败", "error"))
      .finally(() => {
        autoDockStopBtn.disabled = false;
      });
  });
}

export function autoLogKey(row) {
  return `${row?.ts || ""}|${row?.level || ""}|${row?.message || ""}`;
}

export function mergeAutoLogs(existing, incoming) {
  const map = new Map();
  for (const row of existing || []) {
    if (!row) continue;
    map.set(autoLogKey(row), row);
  }
  for (const row of incoming || []) {
    if (!row) continue;
    map.set(autoLogKey(row), row);
  }
  return Array.from(map.values()).slice(-80);
}
