// @ts-nocheck
/* eslint-disable */
/** Migrated from web/static/app.js — logic preserved. */

import { state } from "../state";
import { fetchJSON } from "../api/client";
import { isLoggedIn } from "../account/index";
import { sourceGrid, watchAddBtn, watchAddForm, watchAddMidError, watchAddMidInput, watchLastSynced, watchMetricCount, watchMetricLinks, watchNextWindow, watchUserGrid, watchUsersBadge, watchWindowCap } from "../dom";
import { bindActionButtons } from "../jobs/index";
import { showToast } from "../shell/toast";
import { formatUnixTimestamp, formatWatchWindow, formatWindowDays } from "../utils/format";
import { highlightWatchUserChip, setButtonLoading } from "../utils/motion";
import { escapeHtml } from "../utils/text";
import { openAppConfirm } from "../shell/confirm";
import { loadSummary } from "../activities/index";

let collectionRows = [];
let editingSource = "";
let draggedSource = "";
let savingOrder = false;

async function saveCollectionOrder(ids, focusId = "") {
  if (savingOrder || ids.every((id, i) => id === collectionRows[i]?.id)) return;
  savingOrder = true;
  sourceGrid.setAttribute("aria-busy", "true");
  try {
    await fetchJSON("/api/collections/order", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source_ids: ids }) });
    const rows = new Map(collectionRows.map(row => [row.id, row]));
    collectionRows = ids.map(id => rows.get(id));
    showToast("合集顺序已保存", "success");
  } catch (error) {
    showToast(error.message || "排序保存失败，请重试", "error");
  } finally {
    savingOrder = false;
    sourceGrid.removeAttribute("aria-busy");
    renderSources(collectionRows);
    if (focusId) [...sourceGrid.querySelectorAll("[data-source-drag]")].find(el => el.dataset.sourceDrag === focusId)?.focus();
  }
}

function bindCollectionOrder() {
  const clearMarkers = () => sourceGrid.querySelectorAll(".drop-before,.drop-after,.is-dragging").forEach(row => row.classList.remove("drop-before", "drop-after", "is-dragging"));
  let pointer = null;
  let scrollFrame = 0;
  const dropRow = (x, y) => {
    const row = document.elementFromPoint(x, y)?.closest(".source-row");
    return row && sourceGrid.contains(row) ? row : null;
  };
  const markTarget = () => {
    sourceGrid.querySelectorAll(".drop-before,.drop-after").forEach(row => row.classList.remove("drop-before", "drop-after"));
    if (!pointer?.moved) return;
    const row = dropRow(pointer.x, pointer.y);
    if (row && row.dataset.sourceId !== draggedSource) {
      const rect = row.getBoundingClientRect();
      row.classList.add(pointer.y < rect.top + rect.height / 2 ? "drop-before" : "drop-after");
    }
  };
  const autoScroll = () => {
    if (!pointer) return;
    if (pointer.moved && pointer.scroller) {
      const rect = pointer.scroller.getBoundingClientRect();
      const top = Math.max(0, rect.top), bottom = Math.min(innerHeight, rect.bottom);
      const delta = pointer.y < top + 48 ? -10 : pointer.y > bottom - 48 ? 10 : 0;
      if (delta) { pointer.scroller.scrollTop += delta; markTarget(); }
    }
    scrollFrame = requestAnimationFrame(autoScroll);
  };
  const finish = (commit = false) => {
    if (!pointer) return;
    const { handle, id, x, y, moved } = pointer;
    const moving = draggedSource;
    const row = commit && moved ? dropRow(x, y) : null;
    pointer = null;
    draggedSource = "";
    cancelAnimationFrame(scrollFrame);
    if (handle.hasPointerCapture(id)) handle.releasePointerCapture(id);
    clearMarkers();
    if (!row || row.dataset.sourceId === moving) return;
    const ids = collectionRows.map(source => source.id).filter(id => id !== moving);
    const rect = row.getBoundingClientRect();
    const target = ids.indexOf(row.dataset.sourceId);
    if (target < 0) return;
    ids.splice(target + (y >= rect.top + rect.height / 2 ? 1 : 0), 0, moving);
    void saveCollectionOrder(ids, moving);
  };
  sourceGrid.addEventListener("pointerdown", e => {
    const handle = e.target.closest("[data-source-drag]");
    if (!handle || savingOrder || pointer || e.button !== 0) return;
    e.preventDefault();
    handle.focus();
    let scroller = sourceGrid.parentElement;
    while (scroller && !(scroller.scrollHeight > scroller.clientHeight && /auto|scroll/.test(getComputedStyle(scroller).overflowY))) scroller = scroller.parentElement;
    pointer = { handle, id:e.pointerId, x:e.clientX, y:e.clientY, startX:e.clientX, startY:e.clientY, moved:false, scroller };
    draggedSource = handle.dataset.sourceDrag;
    handle.setPointerCapture(e.pointerId);
    scrollFrame = requestAnimationFrame(autoScroll);
  });
  sourceGrid.addEventListener("pointermove", e => {
    if (!pointer || pointer.id !== e.pointerId) return;
    pointer.x = e.clientX; pointer.y = e.clientY;
    if (!pointer.moved && Math.hypot(e.clientX - pointer.startX, e.clientY - pointer.startY) > 5) {
      pointer.moved = true;
      pointer.handle.closest(".source-row").classList.add("is-dragging");
    }
    markTarget();
  });
  sourceGrid.addEventListener("pointerup", e => { if (pointer?.id === e.pointerId) finish(true); });
  sourceGrid.addEventListener("pointercancel", () => finish());
  sourceGrid.addEventListener("lostpointercapture", () => finish());
  document.addEventListener("keydown", e => { if (e.key === "Escape") finish(); });
  sourceGrid.addEventListener("keydown", e => {
    const handle = e.target.closest("[data-source-drag]");
    if (!handle || !e.altKey || !["ArrowUp", "ArrowDown"].includes(e.key)) return;
    e.preventDefault();
    if (savingOrder) return;
    const ids = collectionRows.map(source => source.id);
    const from = ids.indexOf(handle.dataset.sourceDrag);
    const to = from + (e.key === "ArrowUp" ? -1 : 1);
    if (to < 0 || to >= ids.length) return;
    [ids[from], ids[to]] = [ids[to], ids[from]];
    void saveCollectionOrder(ids, handle.dataset.sourceDrag);
  });
}

function bindCollections() {
  bindCollectionOrder();
  sourceGrid.insertAdjacentHTML("beforebegin", `<form id="collection-editor" class="llm-settings-body collection-editor" hidden><div class="settings-field-row"><label class="settings-field"><span class="field-label">UP 名称</span><input class="form-input" id="collection-name" maxlength="80" required></label><label class="settings-field"><span class="field-label">UP UID</span><input class="form-input" id="collection-mid" inputmode="numeric" pattern="[1-9][0-9]{0,15}" required></label></div><label class="maintenance-check"><input id="collection-backup" type="checkbox">仅手动更新（不加入一键更新）</label><p class="caption">读取该 UP 最新专栏中的抽奖链接。修改 UID 后重新建立检查点，已入库活动仍保留。</p><div class="settings-panel-actions"><button class="btn btn-primary btn-pill" type="submit">保存合集</button><button class="btn btn-secondary btn-pill" type="button" id="collection-cancel">取消</button><span class="caption" id="collection-error" role="alert"></span></div></form>`);
  const form = document.getElementById("collection-editor");
  const field = name => document.getElementById(`collection-${name}`);
  const open = row => {
    editingSource = row?.id || "";
    field("name").value = row?.name || "";
    field("mid").value = row?.mid || "";
    field("backup").checked = !!row?.backup;
    field("error").textContent = "";
    form.hidden = false; field("name").focus();
  };
  field("add").addEventListener("click", () => open(null));
  field("cancel").addEventListener("click", () => { form.hidden = true; });
  form.addEventListener("submit", async e => {
    e.preventDefault(); const button = e.submitter; button.disabled = true;
    try {
      await fetchJSON(`/api/collections${editingSource ? `/${encodeURIComponent(editingSource)}` : ""}`, { method: editingSource ? "PUT" : "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({name: field("name").value.trim(), mid: field("mid").value.trim(), backup: field("backup").checked}) });
      form.hidden = true; await loadSummary(); showToast("合集已保存", "success");
    } catch(error) { field("error").textContent = error.message || String(error); }
    finally { button.disabled = false; }
  });
  sourceGrid.addEventListener("click", async e => {
    const button = e.target.closest("[data-collection]"); if (!button) return;
    const row = collectionRows.find(r => r.id === button.dataset.id); if (!row) return;
    if (button.dataset.collection === "edit") { open(row); return; }
    if (!await openAppConfirm({title:`移除 ${row.name}？`, desc:"停止从此 UP 合集获取新活动，保留已经入库的活动与参与记录。", confirmLabel:"移除合集", cancelLabel:"取消", danger:true})) return;
    button.disabled = true;
    try { await fetchJSON(`/api/collections/${encodeURIComponent(row.id)}`, {method:"DELETE"}); await loadSummary(); }
    catch(error) { showToast(String(error.message || error), "error"); }
    finally { button.disabled = false; }
  });
}

export function clearWatchMidError() {
  if (!watchAddMidInput || !watchAddMidError) return;
  watchAddMidInput.classList.remove("is-invalid");
  watchAddMidError.hidden = true;
  watchAddMidError.textContent = "";
}

export function showWatchMidError(message) {
  if (!watchAddMidInput || !watchAddMidError) return;
  watchAddMidInput.classList.add("is-invalid");
  watchAddMidError.hidden = false;
  watchAddMidError.textContent = message;
}

export function closeWatchUserConfirm(exceptChip = null) {
  document.querySelectorAll(".watch-user-chip.is-confirming").forEach((chip) => {
    if (chip !== exceptChip) chip.classList.remove("is-confirming");
  });
}

export function renderWatchUsersPanel(data) {
  if (!data) return;
  state.watchUsers = data;
  const count = Number(data.count) || (data.users || []).length;
  if (watchMetricCount) watchMetricCount.textContent = String(count);
  if (watchMetricLinks) watchMetricLinks.textContent = String(data.last_scan_link_count ?? "—");
  if (watchUsersBadge) watchUsersBadge.textContent = `${count} 人`;
  if (watchLastSynced) watchLastSynced.textContent = formatUnixTimestamp(data.last_synced_at);
  if (watchNextWindow) {
    const window = data.next_window || {};
    watchNextWindow.textContent = formatWatchWindow(window.start, window.end);
  }
  if (watchWindowCap) {
    watchWindowCap.textContent = formatWindowDays(data.max_window_seconds);
  }
  updateWatchUserFormState();

  if (!watchUserGrid) return;
  const users = data.users || [];
  const canManage = isLoggedIn();
  if (!users.length) {
    watchUserGrid.innerHTML = `<p class="caption watch-user-empty">${canManage ? "暂无监控用户，可在上方添加" : "暂无监控用户"}</p>`;
    return;
  }
  watchUserGrid.innerHTML = users
    .map(
      (user) => `
      <article class="watch-user-chip" data-watch-mid="${escapeHtml(user.mid)}">
        <a class="watch-user-link" href="https://space.bilibili.com/${encodeURIComponent(user.mid)}/dynamic" target="_blank" rel="noopener" title="${escapeHtml(user.name)}">${escapeHtml(user.name)}</a>
        <span class="watch-user-mid">MID ${escapeHtml(user.mid)}</span>
        <div class="watch-user-actions">
          <button type="button" class="watch-user-remove" data-watch-remove="${escapeHtml(user.mid)}" aria-label="移除 ${escapeHtml(user.name)}" ${canManage ? "" : "disabled"} title="${canManage ? "移出监控名单" : "登录后可管理"}">×</button>
          <div class="watch-user-confirm-actions">
            <button type="button" class="btn btn-secondary btn-compact watch-user-confirm-yes" data-watch-confirm="${escapeHtml(user.mid)}">确认</button>
            <button type="button" class="watch-user-confirm-no" data-watch-cancel>取消</button>
          </div>
        </div>
      </article>`
    )
    .join("");
}

export function updateWatchUserFormState() {
  const canManage = isLoggedIn();
  if (watchAddMidInput) watchAddMidInput.disabled = !canManage;
  if (watchAddBtn) {
    watchAddBtn.disabled = !canManage;
    watchAddBtn.title = canManage ? "" : "登录后可添加监控用户";
  }
}

export async function loadWatchUsers() {
  try {
    const data = await fetchJSON("/api/watch-users");
    renderWatchUsersPanel(data);
    return data;
  } catch (error) {
    if (watchUserGrid) {
      watchUserGrid.innerHTML = `<p class="caption watch-user-empty">加载失败：${escapeHtml(String(error.message || error))}</p>`;
    }
    throw error;
  }
}

export function parseWatchMidInput(raw) {
  const text = String(raw || "").trim();
  if (!/^\d+$/.test(text)) return null;
  if (text.length > 16) return null;
  try {
    const mid = BigInt(text);
    if (mid <= 0n) return null;
    if (mid <= BigInt(Number.MAX_SAFE_INTEGER)) return Number(text);
    return text;
  } catch {
    return null;
  }
}

export async function submitWatchUser(event) {
  event.preventDefault();
  clearWatchMidError();
  if (!isLoggedIn()) {
    showToast("请先扫码登录", "info", "登录后才能管理监控用户");
    return;
  }
  const rawMid = String(watchAddMidInput?.value || "").trim();
  if (!rawMid) {
    showWatchMidError("请输入用户 MID");
    watchAddMidInput?.focus();
    watchAddMidInput?.classList.add("is-shake");
    window.setTimeout(() => watchAddMidInput?.classList.remove("is-shake"), 420);
    return;
  }
  const mid = parseWatchMidInput(rawMid);
  if (mid === null) {
    showWatchMidError("请输入有效的 B 站用户 MID");
    watchAddMidInput?.focus();
    watchAddMidInput?.classList.add("is-shake");
    window.setTimeout(() => watchAddMidInput?.classList.remove("is-shake"), 420);
    return;
  }
  setButtonLoading(watchAddBtn, true, { label: "添加中…" });
  try {
    const result = await fetchJSON("/api/watch-users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mid }),
    });
    if (watchAddForm) watchAddForm.reset();
    clearWatchMidError();
    await loadWatchUsers();
    highlightWatchUserChip(mid);
    const addedName = result?.user?.name || String(mid);
    if (result?.name_fallback) {
      showToast("已添加监控用户（昵称暂用 MID）", "info", addedName);
    } else {
      showToast("已添加监控用户", "success", addedName);
    }
  } catch (error) {
    const message = String(error.message || error);
    if (message.includes("已在监控列表")) {
      showWatchMidError(message);
      watchAddMidInput?.focus();
    } else {
      showWatchMidError(message);
    }
  } finally {
    setButtonLoading(watchAddBtn, false);
    updateWatchUserFormState();
  }
}

export async function removeWatchUser(mid) {
  if (!isLoggedIn()) {
    showToast("请先扫码登录", "info", "登录后才能管理监控用户");
    return;
  }
  try {
    await fetchJSON(`/api/watch-users/${encodeURIComponent(mid)}`, { method: "DELETE" });
    await loadWatchUsers();
    showToast("已移出监控名单", "success");
  } catch (error) {
    showToast(String(error.message || error), "error");
  }
}

export function bindWatchUsers() {
  bindCollections();
  watchAddForm?.addEventListener("submit", submitWatchUser);
  watchAddMidInput?.addEventListener("input", clearWatchMidError);
  watchUserGrid?.addEventListener("click", async (event) => {
    const cancelBtn = event.target.closest("[data-watch-cancel]");
    if (cancelBtn) {
      cancelBtn.closest(".watch-user-chip")?.classList.remove("is-confirming");
      return;
    }

    const confirmBtn = event.target.closest("[data-watch-confirm]");
    if (confirmBtn && !confirmBtn.disabled) {
      const mid = Number(confirmBtn.dataset.watchConfirm || 0);
      const chip = confirmBtn.closest(".watch-user-chip");
      if (!mid) return;
      setButtonLoading(confirmBtn, true);
      try {
        await removeWatchUser(mid);
        chip?.classList.remove("is-confirming");
      } finally {
        setButtonLoading(confirmBtn, false);
      }
      return;
    }

    const removeBtn = event.target.closest("[data-watch-remove]");
    if (!removeBtn || removeBtn.disabled) return;
    const chip = removeBtn.closest(".watch-user-chip");
    if (!chip) return;
    closeWatchUserConfirm(chip);
    chip.classList.add("is-confirming");
  });
}

export function renderSources(sources) {
  if (draggedSource || savingOrder) return;
  collectionRows = sources || [];
  const note = document.getElementById("collection-summary");
  if (note) note.textContent = `按列表顺序一键更新 ${collectionRows.filter(s => !s.backup).length} 个常用源，${collectionRows.filter(s => s.backup).length} 个备用源仅手动更新。每源最多提取最新 20 条链接，历史活动保留。`;
  sourceGrid.innerHTML = (sources || [])
    .map((source, index) => {
      const links = [];
      if (source.space_url) {
        links.push(`<a class="source-link" href="${escapeHtml(source.space_url)}" target="_blank" rel="noopener">UP 主页</a>`);
      }
      if (source.container_url) {
        links.push(`<a class="source-link" href="${escapeHtml(source.container_url)}" target="_blank" rel="noopener">当前合集</a>`);
      }
      const statusClass = source.updated ? "fresh" : "cached";
      const checkStatus = source.updated ? "本次有更新" : source.checked_at ? "已检查 · 无更新" : "尚未检查";
      const statusText = source.backup ? `备用 · 仅手动更新 · ${checkStatus}` : checkStatus;
      return `
      <article class="source-row" data-source-id="${escapeHtml(source.id)}" style="--row-delay:${index * 40}ms">
        <button type="button" class="source-row-index source-drag-handle" draggable="false" data-source-drag="${escapeHtml(source.id)}" aria-label="拖动排序：${escapeHtml(source.name)}；也可按 Alt 加上下方向键移动" title="拖动调整顺序 · Alt + ↑/↓"><span aria-hidden="true">⠿</span><span>${String(index + 1).padStart(2, "0")}</span></button>
        <div class="source-row-body">
          <div class="source-row-head">
            <h3>${escapeHtml(source.name)}</h3>
            <div class="source-row-actions">
              <span class="source-status ${statusClass}">${statusText}</span>
              <button
                type="button"
                class="btn btn-secondary btn-compact btn-pill source-refresh-btn"
                data-action="refresh_source"
                data-source-id="${escapeHtml(source.id)}"
              >更新此源</button>
              <button class="btn btn-secondary btn-compact btn-pill" data-collection="edit" data-id="${escapeHtml(source.id)}">修改</button>
              <button class="btn btn-secondary btn-compact btn-pill" data-collection="delete" data-id="${escapeHtml(source.id)}">移除</button>
            </div>
          </div>
          <p class="source-row-meta"><span class="source-link-count">${source.link_count}</span> 条链接 · ${escapeHtml(source.title || "暂无标题")}</p>
            <p class="source-row-time">合集发布：${escapeHtml(source.published_at_text || "尚未获取")} · 最近检查：${escapeHtml(source.checked_at_text || "尚未更新")}</p>
          <div class="source-links">${links.join("") || '<span class="caption">暂无外链</span>'}</div>
        </div>
      </article>`;
    })
    .join("");
  bindActionButtons();
}
