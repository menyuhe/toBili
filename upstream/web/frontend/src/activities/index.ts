// @ts-nocheck
/* eslint-disable */
/** Migrated from web/static/app.js — logic preserved. */

import { state } from "../state";
import { fetchJSON } from "../api/client";
import { isSetupComplete } from "../account/index";
import { activitiesBody, activitiesCards, filterDrawWindowHint, filterResultSummary, pagination, statsGrid } from "../dom";
import { bindActionButtons, updateJobUI } from "../jobs/index";
import { showToast } from "../shell/toast";
import { activityStatusTone, badgeClass, formatFilterSummary, formatHeat, formatLastParticipation, formatLotteryTime, isLotterySoon, lotteryTypeTone } from "../utils/format";
import { animateStatValue, flashFilterPill, playActivityListEnter, pulseFilterSummary } from "../utils/motion";
import { escapeHtml, truncateText } from "../utils/text";
import { renderSources } from "../watch/index";
import { poolButton, setPoolPage } from './pool';

export function renderStats(summary) {
  if (!statsGrid) return;
  const counts = summary.user_status_counts || {};
  const drawCounts = summary.counts || {};
  const cards = [
    { key: "total", label: "活动总数", value: summary.total_count || 0 },
    { key: "pending", label: "未参加", value: counts["未参加"] || 0 },
    { key: "joined", label: "已参加", value: counts["已参加"] || 0 },
    { key: "ended", label: "已结束", value: counts["已结束"] || 0 },
    { key: "active", label: "进行中", value: drawCounts.active || 0 },
    { key: "available", label: "现在可参与", value: summary.participatable_count || 0 },
  ];
  const previous = state.statValues || {};
  statsGrid.innerHTML = cards
    .map(
      (card, index) => `
      <article class="stat-card is-entering" style="--card-delay:${index * 55}ms" data-stat-key="${card.key}">
        <p class="stat-label">${card.label}</p>
        <p class="stat-value">${previous[card.key] ?? card.value}</p>
      </article>`
    )
    .join("");
  cards.forEach((card) => {
    const valueEl = statsGrid.querySelector(`[data-stat-key="${card.key}"] .stat-value`);
    animateStatValue(valueEl, previous[card.key] ?? card.value, card.value);
  });
  state.statValues = Object.fromEntries(cards.map((card) => [card.key, card.value]));
}

export function buildActivityParticipateBtn(item) {
  if (item.archived) {
    return `<button type="button" class="btn btn-secondary btn-compact" data-restore-activity="${escapeHtml(item.dynamic_id)}">恢复</button>`;
  }
  const remove = `<button type="button" class="btn btn-ghost btn-compact" data-archive-activity="${escapeHtml(item.dynamic_id)}" title="从活动库移出，可在已归档中恢复">移出</button>`;
  if (item.has_participated) {
    return `<span class="pool-activity-actions"><button class="btn btn-secondary btn-compact btn-pill" data-review-cleanup="${escapeHtml(item.dynamic_id)}">关联与清理</button>${remove}</span>`;
  }
  if (item.can_participate) {
    return `<span class="pool-activity-actions">${poolButton(item)}<button class="btn btn-primary btn-compact btn-pill" data-action="participate" data-dynamic-id="${escapeHtml(item.dynamic_id)}">参与</button>${remove}</span>`;
  }
  return remove;
}

export function buildActivityLastNote(item) {
  if (!item.last_participation) return "";
  return `<div class="last-result ${escapeHtml(item.last_participation.status || "")}">上次：${escapeHtml(formatLastParticipation(item.last_participation))}</div>`;
}

export function buildActivityLink(item) {
  // A dynamic is not necessarily an Opus. Let Bilibili route its own dynamic ID.
  if (/^\d{18,19}$/.test(String(item.dynamic_id || ""))) {
    return `<a class="activity-link" href="https://t.bilibili.com/${escapeHtml(item.dynamic_id)}" target="_blank" rel="noopener">打开动态</a>`;
  }
  if (item.source_url) {
    return `<a class="activity-link" href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener">打开动态</a>`;
  }
  return `<span class="caption">—</span>`;
}

export function renderActivityTableRow(item, index = 0) {
  const title = escapeHtml(item.activity_title || item.prize || "未知活动");
  const statusTone = activityStatusTone(item.activity_status);
  const soon = isLotterySoon(item.lottery_time);
  const typeTone = lotteryTypeTone(item.lottery_type);
  return `
    <tr class="activity-row is-${statusTone}${soon ? " is-soon" : ""}" data-dynamic-id="${escapeHtml(item.dynamic_id || "")}" style="--row-delay:${Math.min(index, 12) * 28}ms">
      <td class="activity-cell">
        <div class="activity-title" title="${title}">${title}</div>
        ${buildActivityLastNote(item)}
      </td>
      <td class="link-cell">${buildActivityLink(item)}</td>
      <td class="chip-cell"><span class="type-chip type-chip--${typeTone}">${escapeHtml(item.lottery_type)}</span></td>
      <td class="heat-cell"><span class="heat-pill${item.heat_missing ? " heat-pill-missing" : ""}">${formatHeat(item)}</span></td>
      <td class="chip-cell"><span class="${badgeClass(item.activity_status)}">${escapeHtml(item.activity_status)}</span></td>
      <td class="time-cell"><span class="time-pill${soon ? " is-soon" : ""}">${escapeHtml(formatLotteryTime(item.lottery_time))}</span></td>
      <td class="chip-cell action-cell">${buildActivityParticipateBtn(item)}</td>
    </tr>`;
}

export function renderActivityCard(item, index = 0) {
  const title = escapeHtml(item.activity_title || item.prize || "未知活动");
  const statusTone = activityStatusTone(item.activity_status);
  const ended = statusTone === "ended" ? " is-ended" : "";
  const soon = isLotterySoon(item.lottery_time);
  const typeTone = lotteryTypeTone(item.lottery_type);
  return `
    <article class="activity-card is-${statusTone}${ended}${soon ? " is-soon" : ""}" data-dynamic-id="${escapeHtml(item.dynamic_id || "")}" style="--row-delay:${Math.min(index, 12) * 28}ms">
      <div class="activity-card-head">
        <h3 class="activity-card-title" title="${title}">${title}</h3>
        <span class="${badgeClass(item.activity_status)}">${escapeHtml(item.activity_status)}</span>
      </div>
      ${buildActivityLastNote(item)}
      <div class="activity-card-meta">
        <span class="type-chip type-chip--${typeTone}">${escapeHtml(item.lottery_type)}</span>
        <span class="heat-pill${item.heat_missing ? " heat-pill-missing" : ""}">${formatHeat(item)}</span>
        <span class="time-pill${soon ? " is-soon" : ""}">${escapeHtml(formatLotteryTime(item.lottery_time))}</span>
      </div>
      <div class="activity-card-actions">
        ${buildActivityLink(item)}
        ${buildActivityParticipateBtn(item)}
      </div>
    </article>`;
}

export function renderActivities(payload) {
  state.activityPayload = payload;
  const items = payload.items || [];
  setPoolPage(items);

  if (filterResultSummary) {
    filterResultSummary.innerHTML = formatFilterSummary(payload);
    filterResultSummary.hidden = false;
    pulseFilterSummary();
  }

  if (!items.length) {
    activitiesBody.innerHTML = `<tr class="empty-row"><td colspan="7"><div class="activity-empty">没有匹配的活动<span class="activity-empty-hint">试试调整筛选或换个关键词</span></div></td></tr>`;
    if (activitiesCards) {
      activitiesCards.innerHTML = `<div class="activity-empty">没有匹配的活动<span class="activity-empty-hint">试试调整筛选或换个关键词</span></div>`;
    }
  } else {
    activitiesBody.innerHTML = items.map((item, index) => renderActivityTableRow(item, index)).join("");
    if (activitiesCards) {
      activitiesCards.innerHTML = items.map((item, index) => renderActivityCard(item, index)).join("");
    }
    playActivityListEnter();
  }

  const page = Number(payload.page) || 1;
  const pages = Math.max(1, Number(payload.pages) || 1);
  const total = Number(payload.total) || 0;
  pagination.innerHTML = `
    <span class="caption pagination-summary">第 ${page} / ${pages} 页 · 本页 ${items.length} 条 · 共 ${total} 条</span>
    <div class="action-row pagination-actions">
      <button class="btn btn-secondary btn-compact btn-pill" id="page-prev" ${page <= 1 ? "disabled" : ""}>上一页</button>
      <button class="btn btn-secondary btn-compact btn-pill" id="page-next" ${page >= pages ? "disabled" : ""}>下一页</button>
    </div>`;

  document.getElementById("page-prev")?.addEventListener("click", () => {
    state.page = Math.max(1, state.page - 1);
    loadActivities();
  });
  document.getElementById("page-next")?.addEventListener("click", () => {
    state.page += 1;
    loadActivities();
  });
  bindActionButtons();
  document.querySelectorAll("[data-archive-activity]").forEach(button => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await fetchJSON(`/api/activities/${encodeURIComponent(button.dataset.archiveActivity)}/archive`, {method: "POST"});
        await Promise.all([loadSummary(), loadActivities()]);
        showToast("已移出活动库", "success", "可在「已归档」中恢复；更新数据不会重新显示");
      } catch (error) {
        button.disabled = false;
        showToast(String(error.message || error), "error");
      }
    });
  });
  document.querySelectorAll("[data-restore-activity]").forEach(button => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await fetchJSON(`/api/activities/${encodeURIComponent(button.dataset.restoreActivity)}/restore`, { method: "POST" });
        await Promise.all([loadSummary(), loadActivities()]);
        showToast("已恢复到活动库", "success");
      } catch (error) {
        button.disabled = false;
        showToast(String(error.message || error), "error");
      }
    });
  });
}

export async function loadSummary() {
  const summary = await fetchJSON("/api/summary");
  renderStats(summary);
  renderSources(summary.sources);
  const discovery = document.getElementById("discovery-status");
  if (discovery) {
    const data = summary.discovery || {};
    const run = data.last_run;
    const timestamp = run?.finished_at || run?.started_at;
    const time = timestamp ? new Date(timestamp * 1000).toLocaleString("zh-CN", { hour12: false }) : "尚无记录";
    const pending = Number(data.pending_count || 0);
    const status = run?.state === "success" && pending > 0 ? `完成 · 待重试 ${pending} 条`
      : { success: "已完成", error: "中途失败", cancelled: "已取消", running: "进行中" }[run?.state] || "未开始";
    discovery.innerHTML = `<div><span>上轮更新</span><strong>${escapeHtml(time)}</strong></div>
      <div><span>上轮入库</span><strong>${run?.persisted_count != null ? Number(run.persisted_count) + " 条" : "—"}</strong></div>
      <div><span>上轮结果</span><strong>${status}</strong></div>
      <div><span>近 ${Number(data.history_days || 7)} 天已检查</span><strong>${Number(data.history_done || 0)} / ${Number(data.topic_count || 4)} 个话题</strong></div>`;
    const detail = document.getElementById("discovery-history-detail");
    if (detail) {
      const remaining = (data.topics || []).filter(topic => !topic.history_done);
      detail.textContent = data.history_complete ? `最近 ${Number(data.history_days || 7)} 天已检查完成，后续只更新新动态。`
        : remaining.map(topic => `${topic.name}：已扫描 ${Number(topic.checked_count || 0)} 条${topic.oldest_pub_ts ? `，最早发布 ${new Date(topic.oldest_pub_ts * 1000).toLocaleDateString("zh-CN")}` : ''}`).join('；')
          + `\n只收录近 ${Number(data.history_days || 7)} 天发布的原活动；更早或发布时间未知的动态跳过。可随时取消并续接。`;
    }
    document.querySelectorAll('[data-action="refresh_discovery_history"]').forEach(button => { button.hidden = Boolean(data.history_complete); });
  }
  const count = document.getElementById("archived-count");
  if (count) count.textContent = String(summary.archived_count || 0);
  updateJobUI(summary.job || { state: "idle" });
  return summary.job;
}

export function setFilterPillGroup(selector, activeButton) {
  document.querySelectorAll(selector).forEach((item) => {
    const active = item === activeButton;
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", active ? "true" : "false");
  });
  flashFilterPill(activeButton);
}

export function setStatusFilter(value) {
  state.filters.status = value || "";
  const buttons = document.querySelectorAll("[data-filter-status]");
  let matched = false;
  buttons.forEach((button) => {
    const isActive = (button.getAttribute("data-filter-status") || "") === state.filters.status;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
    if (isActive) matched = true;
  });
  if (!matched && buttons[0]) {
    buttons[0].classList.add("active");
    buttons[0].setAttribute("aria-pressed", "true");
  }
}

export function setDrawWindowFilter(value) {
  state.filters.drawWindow = value || "";
  document.querySelectorAll("[data-filter-draw-window]").forEach((button) => {
    const isActive = (button.getAttribute("data-filter-draw-window") || "") === state.filters.drawWindow;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
    if (isActive) flashFilterPill(button);
  });
  if (filterDrawWindowHint) {
    filterDrawWindowHint.hidden = !state.filters.drawWindow;
  }
}

export function updateDrawWindowHint() {
  if (!filterDrawWindowHint || !state.filters.drawWindow) return;
  filterDrawWindowHint.textContent = "仅筛选你已参加、且 3 天内即将开奖的活动";
}

export function buildActivityFilterQueryParams() {
  const params = new URLSearchParams();
  if (state.filters.archived) params.set("archived", "true");
  if (state.filters.draw) params.set("draw", state.filters.draw);
  if (state.filters.q) params.set("q", state.filters.q);
  if (state.filters.type) params.set("type", state.filters.type);
  if (state.filters.status) params.set("status", state.filters.status);
  if (state.filters.drawWindow) params.set("draw_window", state.filters.drawWindow);
  if (state.filters.sort) params.set("sort", state.filters.sort);
  if (state.filters.order) params.set("order", state.filters.order);
  return params;
}

export function buildActivityFilterJobParams() {
  const params = {};
  if (state.filters.draw) params.draw = state.filters.draw;
  if (state.filters.q) params.q = state.filters.q;
  if (state.filters.type) params.lottery_type = state.filters.type;
  if (state.filters.status) params.status = state.filters.status;
  if (state.filters.drawWindow) params.draw_window = state.filters.drawWindow;
  if (state.filters.sort) params.sort = state.filters.sort;
  if (state.filters.order) params.order = state.filters.order;
  return params;
}

export function getActiveFilterKey() {
  return JSON.stringify(state.filters);
}

let activitiesRequestId = 0;
export async function loadActivities() {
  const requestId = ++activitiesRequestId;
  const filterKey = getActiveFilterKey();
  const params = new URLSearchParams({
    page: String(state.page),
    page_size: String(state.pageSize),
  });
  buildActivityFilterQueryParams().forEach((value, key) => params.set(key, value));
  try {
    const payload = await fetchJSON(`/api/activities?${params.toString()}`);
    if (requestId !== activitiesRequestId) return;
    state.page = Number(payload.page) || 1;
    renderActivities(payload);
  } catch (error) {
    showToast(String(error.message || error), "error");
  }
}

export function bindFilterPills() {
  document.querySelectorAll("[data-activity-layout]").forEach(button => {
    button.addEventListener("click", () => {
      document.querySelector(".activities-panel").dataset.layout = button.dataset.activityLayout;
      setFilterPillGroup("[data-activity-layout]", button);
    });
  });
  document.querySelectorAll("[data-archive-view]").forEach(button => {
    button.addEventListener("click", () => {
      state.filters.archived = button.dataset.archiveView === "archived";
      setFilterPillGroup("[data-archive-view]", button);
      setStatusFilter("");
      setDrawWindowFilter("");
      state.page = 1;
      document.getElementById("archive-view-note").hidden = !state.filters.archived;
      document.getElementById("archive-ended").hidden = state.filters.archived;
      document.getElementById("filter-status-group").hidden = state.filters.archived;
      document.getElementById("filter-draw-window-group").hidden = state.filters.archived;
      loadActivities();
    });
  });
  document.getElementById("archive-ended")?.addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "正在归档…";
    try {
      const result = await fetchJSON("/api/activities/archive-ended", { method: "POST" });
      state.page = 1;
      await Promise.all([loadSummary(), loadActivities()]);
      showToast(result.archived_count ? `已归档 ${result.archived_count} 个已结束活动` : "暂无需要归档的已结束活动", "success", "可在「已归档」中查看和恢复");
    } catch (error) {
      showToast(String(error.message || error), "error");
    } finally {
      button.disabled = false;
      button.textContent = "一键归档已结束";
    }
  });
  document.querySelectorAll("[data-filter-type]").forEach((button) => {
    button.addEventListener("click", () => {
      setFilterPillGroup("[data-filter-type]", button);
      state.filters.type = button.getAttribute("data-filter-type") || "";
      state.page = 1;
      loadActivities();
    });
  });
  document.querySelectorAll("[data-filter-status]").forEach((button) => {
    button.addEventListener("click", () => {
      if (state.filters.drawWindow) {
        setDrawWindowFilter("");
      }
      setFilterPillGroup("[data-filter-status]", button);
      state.filters.status = button.getAttribute("data-filter-status") || "";
      state.page = 1;
      loadActivities();
    });
  });
  document.querySelectorAll("[data-filter-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const sort = button.getAttribute("data-filter-sort") || "";
      const defaultOrder = sort === "lottery_time" ? "asc" : "desc";
      const order = sort && state.filters.sort === sort
        ? state.filters.order === "asc" ? "desc" : "asc"
        : sort ? defaultOrder : "";
      setFilterPillGroup("[data-filter-sort]", button);
      state.filters.sort = sort;
      state.filters.order = order;
      document.querySelectorAll("[data-filter-sort]").forEach((option) => {
        const mode = option.getAttribute("data-filter-sort") || "";
        const active = mode === sort;
        option.setAttribute("aria-pressed", String(active));
        if (mode) {
          option.textContent = `${mode === "heat" ? "热度" : "开奖时间"} ${active ? order === "asc" ? "↑" : "↓" : "↕"}`;
          option.setAttribute("data-filter-order", active ? order : mode === "lottery_time" ? "asc" : "desc");
        }
      });
      state.page = 1;
      loadActivities();
    });
  });
  document.querySelectorAll("[data-filter-draw-window]").forEach((button) => {
    button.addEventListener("click", () => {
      const value = button.getAttribute("data-filter-draw-window") || "";
      const isActive = button.classList.contains("active");
      if (isActive) {
        setDrawWindowFilter("");
      } else {
        setStatusFilter("");
        setDrawWindowFilter(value);
        updateDrawWindowHint();
      }
      state.page = 1;
      loadActivities();
    });
  });
  const filterQ = document.getElementById("filter-q");
  if (!filterQ) return;
  let searchTimer = null;
  filterQ.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      state.page = 1;
      state.filters.q = filterQ.value.trim();
      loadActivities();
    }, 320);
  });
}

export function restoreActivityFilters() {
  const groups = {'data-filter-type': state.filters.type, 'data-filter-status': state.filters.status,
    'data-filter-sort': state.filters.sort, 'data-filter-draw-window': state.filters.drawWindow,
    'data-archive-view': state.filters.archived ? 'archived' : 'current'};
  for (const [attribute, value] of Object.entries(groups)) {
    document.querySelectorAll(`[${attribute}]`).forEach(button => {
      const active = button.getAttribute(attribute) === value;
      button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
    });
  }
  document.getElementById('filter-q').value = state.filters.q;
  document.getElementById('archive-view-note').hidden = !state.filters.archived;
  document.getElementById('archive-ended').hidden = state.filters.archived;
  document.getElementById('filter-status-group').hidden = state.filters.archived;
  document.getElementById('filter-draw-window-group').hidden = state.filters.archived;
  updateDrawWindowHint();
}
