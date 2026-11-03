// @ts-nocheck
import { activeUid, selectIdentity, accountGeneration, accountUrl } from './identity';
import { fetchJSON } from '../api/client';
import { state } from '../state';
import { loadAccount, loadAccountExtras, renderAccountViews } from './index';
import { loadActivities, loadSummary, renderActivities, restoreActivityFilters } from '../activities/index';
import { resetPoolAccount, loadPool } from '../activities/pool';
import { maintenanceSnapshot, restoreMaintenance, openMessageReview } from '../maintenance/index';
import { accountTaskText } from './status';
import { loadSettings } from '../settings/index';
import { fetchAutoStatus } from '../auto/index';
import { closeEventSource, startRealtime } from '../realtime/sse';
import { stopJobPolling, startPolling, updateJobUI } from '../jobs/index';
import { resetJobHistoryAccount } from '../jobs/history';
import { escapeHtml } from '../utils/text';
import { showToast } from '../shell/toast';

const views = new Map();
let accounts = [];
const initialFilters = {...state.filters};
let loginTimer;
let refreshVersion = 0;

export async function refreshAccounts() {
  const version = ++refreshVersion;
  const result = await fetchJSON('/api/accounts');
  if (version !== refreshVersion) return;
  accounts = result.items;
  const list = document.getElementById('account-list');
  if (!list) return;
  list.innerHTML = accounts.map(a => `<div class="account-entry ${a.uid === activeUid ? 'selected' : ''}"><button class="account-choice" data-account-uid="${a.uid}" aria-pressed="${a.uid === activeUid}">
    ${a.face ? `<img src="${escapeHtml(a.face)}" referrerpolicy="no-referrer" alt="" />` : '<span class="account-initial">账</span>'}
    <span><strong>${escapeHtml(a.uname || `UID ${a.uid}`)}</strong><small title="${escapeHtml(a.job.progress_message || a.job.label || '')}">${escapeHtml(accountTaskText(a.job, a.logged_in))}</small></span></button>${a.messages.pending ? `<button class="account-message-link" data-account-messages="${a.uid}" title="查看 ${escapeHtml(a.uname || a.uid)} 的私信和 @ 消息，核查是否与领奖有关">${a.messages.pending} 条消息待核查 <span aria-hidden="true">→</span></button>` : ''}</div>`).join('');
}

export async function switchAccount(uid, force = false) {
  if (uid === activeUid && !force) return;
  const scroll = document.querySelector('.content-area') || document.scrollingElement;
  const inActivities = Boolean(document.querySelector("#section-activities.active"));
  const form = [...document.querySelectorAll('#section-activities input, #section-activities select')]
    .filter(e => e.id).map(e => [e.id, e.value, e.checked]);
  views.set(activeUid, {page: state.page, filters: {...state.filters}, account: state.account,
    payload: state.activityPayload, maintenance: maintenanceSnapshot(), scroll: inActivities ? scroll?.scrollTop || 0 : views.get(activeUid)?.scroll || 0, form});
  document.getElementById("app-confirm-cancel")?.click();
  closeEventSource();
  stopJobPolling();
  if (state.autoPollTimer) { clearInterval(state.autoPollTimer); state.autoPollTimer = null; }
  if (state.sseReconnectTimer) { clearTimeout(state.sseReconnectTimer); state.sseReconnectTimer = null; }
  if (state.jobResultTimer) { clearTimeout(state.jobResultTimer); state.jobResultTimer = null; }
  selectIdentity(uid);
  const generation = accountGeneration;
  const saved = views.get(uid);
  state.page = saved?.page || 1;
  state.filters = {...(saved?.filters || initialFilters)};
  state.currentJob = null; state.autoScheduler = null; state.autoLogs = [];
  state.lastFinishedJobKey = ''; state.activeJobKey = ''; state.atAlertShownKey = '';
  state.account = saved?.account || {...accounts.find(a => a.uid === uid), logged_in: false, expired: false};
  resetPoolAccount();
  resetJobHistoryAccount();
  renderAccountViews(state.account);
  renderActivities(saved?.payload || {items: [], page: 1, pages: 1, total: 0});
  updateJobUI({state: 'idle', uid});
  for (const field of document.querySelectorAll("#section-activities input, #section-activities select")) {
    if (field.tagName === "SELECT") field.selectedIndex = Math.max(0, [...field.options].findIndex(o => o.defaultSelected));
    else { field.value = field.defaultValue; if ("checked" in field) field.checked = field.defaultChecked; }
  }
  for (const [id, value, checked] of saved?.form || []) {
    const field = document.getElementById(id);
    if (field) { field.value = value; if ('checked' in field) field.checked = checked; }
  }
  // Never replace or navigate away from the independent article editor.
  restoreActivityFilters();
  restoreMaintenance(saved?.maintenance);
  if (scroll && inActivities) scroll.scrollTop = saved?.scroll || 0;
  await refreshAccounts();
  if (generation !== accountGeneration) return;
  await Promise.allSettled([loadAccount(), loadSettings(), loadSummary(), loadActivities(), loadPool(), fetchAutoStatus()]);
  if (generation !== accountGeneration) return;
  if (scroll && inActivities) scroll.scrollTop = saved?.scroll || 0;
  loadAccountExtras().catch(() => {});
  startRealtime();
  if (state.currentJob?.state === 'running') startPolling();
  window.dispatchEvent(new CustomEvent('tobili:account-switched'));
}

export async function beginAccountLogin(expectedUid = '') {
  const session = await fetchJSON(`/api/accounts/login?expected_uid=${encodeURIComponent(expectedUid)}`, {method: 'POST'});
  let dialog = document.getElementById('account-login-dialog');
  if (!dialog) {
    dialog = document.createElement('dialog'); dialog.id = 'account-login-dialog';
    dialog.innerHTML = '<h2>扫码添加账号</h2><p>其他账号保持登录，后台任务继续运行。</p><img width="220" height="220" alt="登录二维码" /><p role="status"></p><button class="btn btn-secondary">取消登录</button>';
    document.body.append(dialog);
  }
  dialog.querySelector('h2').textContent = expectedUid ? `重新登录 UID ${expectedUid}` : '扫码添加账号';
  dialog.querySelector('img').removeAttribute('src');
  dialog.querySelector('[role=status]').textContent = '正在生成二维码…';
  dialog.showModal();
  const cancel = () => {
    clearTimeout(loginTimer); dialog.close();
    fetchJSON(`/api/accounts/login/${session.session_id}/cancel`, {method: 'POST'}).catch(() => {});
  };
  dialog.querySelector('button').onclick = cancel;
  dialog.oncancel = cancel;
  let qrStamp = 0;
  const poll = async () => {
    try {
      const job = await fetchJSON(`/api/accounts/login/${session.session_id}`);
      dialog.querySelector('[role=status]').textContent = job.message;
      const stamp = job.result?.qrcode_refreshed_at;
      if (stamp && stamp !== qrStamp) {
        qrStamp = stamp;
        dialog.querySelector('img').src = `/api/accounts/login/${session.session_id}/qrcode?t=${stamp}`;
      }
      if (job.state === 'success') {
        dialog.close(); await refreshAccounts();
        await switchAccount(job.result.uid, true); return;
      }
      if (['error', 'cancelled', 'interrupted'].includes(job.state)) return;
      loginTimer = setTimeout(poll, 1200);
    } catch (error) { dialog.querySelector('[role=status]').textContent = error.message; }
  };
  void poll();
}

export async function initAccounts() {
  const host = document.getElementById('sidebar-account');
  host?.insertAdjacentHTML('afterbegin', '<div class="account-switcher"><div class="account-switcher-head"><strong>账号</strong><button class="btn btn-ghost btn-compact" id="account-add">＋ 添加</button></div><div id="account-list" aria-label="切换账号"></div></div>');
  await refreshAccounts();
  if (!accounts.some(a => a.uid === activeUid)) selectIdentity(accounts.find(a => a.logged_in)?.uid || accounts[0]?.uid || 'none');
  host?.addEventListener('click', event => {
    const messages = event.target.closest('[data-account-messages]');
    if (messages) {
      const uid = messages.dataset.accountMessages;
      void (async () => {
        await switchAccount(uid);
        if (activeUid === uid) await openMessageReview();
      })().catch(error => showToast(error.message, 'error'));
      return;
    }
    const button = event.target.closest('[data-account-uid]');
    if (button) switchAccount(button.dataset.accountUid).catch(error => showToast(error.message, 'error'));
  });
  document.getElementById('account-add')?.addEventListener('click', () => beginAccountLogin().catch(error => showToast(error.message, 'error')));
  document.addEventListener('click', event => {
    if (event.target.closest('[data-action="login"]')) {
      event.preventDefault(); event.stopImmediatePropagation();
      beginAccountLogin(activeUid === 'none' ? '' : activeUid).catch(error => showToast(error.message, 'error'));
    }
  }, true);
  window.addEventListener('tobili:messages-reviewed', () => void refreshAccounts().catch(() => {}));
  setInterval(() => refreshAccounts().catch(() => {}), 2500);
  await refreshAccounts();
}
