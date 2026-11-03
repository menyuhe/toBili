// @ts-nocheck
import { fetchJSON } from '../api/client';
import { escapeHtml } from '../utils/text';
import { showToast } from '../shell/toast';
import { startJob } from '../jobs/index';
import { state } from '../state';

let pool = { items: [], running: false, pending: 0, paused: false };
let visibleItems = [];
let fetching = false;
let editing = false;
let busyJob = false;
let expanded = true;
let version = 0;
let lastJobKey = '';
const labels = { pending: '待参与', running: '正在参与', success: '已完成', failed: '失败', skipped: '已跳过' };

export function poolButton(item) {
  const selected = pool.items.some(row => row.dynamic_id === item.dynamic_id);
  return `<button type="button" class="btn btn-secondary btn-compact btn-pill pool-select${selected ? ' is-selected' : ''}" data-pool-add="${escapeHtml(item.dynamic_id)}" ${selected || pool.running ? 'disabled' : ''}>${selected ? '已预选' : '＋ 预选'}</button>`;
}

export function poolJobChanged(job) {
  busyJob = job?.state === 'running';
  const key = `${job?.id}:${job?.state}:${job?.progress_step}`;
  if (['participate', 'participate_triple', 'participate_pool'].includes(job?.action) && key !== lastJobKey) {
    lastJobKey = key;
    loadPool().catch(() => {});
  }
  render();
}

export function setPoolPage(items) {
  visibleItems = items;
  loadPool().catch(() => {});
}

export async function loadPool() {
  if (fetching) return;
  fetching = true;
  const requestVersion = ++version;
  try {
    const data = await fetchJSON('/api/participation-pool');
    if (requestVersion === version) {
      pool = data;
      // Pool membership only changes selection controls. Participation results
      // refresh the library through the normal job-completion handler.
    }
  } catch (error) {
    if (error.httpStatus === 401) pool = { items: [], pending: 0, running: false, paused: false };
    else throw error;
  } finally {
    fetching = false;
    render();
  }
}

function render() {
  const root = document.getElementById('participation-pool');
  if (!root) return;
  const rows = pool.items || [];
  const successes = Number(pool.last_run?.joined || 0);
  const failures = rows.filter(row => row.state === 'failed').length;
  const skipped = Number(pool.last_run?.skipped || 0);
  const locked = editing || pool.running || (busyJob && state.currentJob?.action === 'participate_pool');
  document.getElementById('pool-count').textContent = String(rows.length);
  const tabCount = document.getElementById('pool-tab-count');
  if (tabCount) tabCount.textContent = String(rows.length);
  document.getElementById('pool-summary').textContent = rows.length
    ? `待参与 ${pool.pending}${failures ? ` · 失败 ${failures}` : ''}${pool.running ? pool.paused ? ' · 正在暂停' : ' · 顺序执行中' : pool.paused ? ' · 已暂停' : ''}${pool.last_run ? ` · ${pool.running ? '本轮' : '上轮'}成功 ${successes} / 跳过 ${skipped}` : ''}`
    : pool.last_run ? `预选已处理完 · 上轮成功 ${successes} / 跳过 ${skipped} · 详情见任务日志`
      : '先加入活动，再按顺序逐个参与；完成后自动出池。';
  const toggle = document.getElementById('pool-toggle');
  toggle.textContent = expanded ? '收起' : '展开';
  toggle.setAttribute('aria-expanded', String(expanded));
  document.getElementById('pool-body').hidden = !expanded;
  const start = document.getElementById('pool-start');
  start.hidden = pool.running;
  start.disabled = busyJob || editing || !pool.pending;
  start.textContent = pool.paused ? `继续剩余 ${pool.pending} 条` : `开始参与${pool.pending ? ` · ${pool.pending} 条` : ''}`;
  const pause = document.getElementById('pool-pause');
  pause.hidden = !pool.running;
  pause.disabled = editing || pool.paused;
  pause.textContent = pool.paused ? '等待当前活动完成…' : '暂停';
  document.getElementById('pool-add-page').disabled = locked || !visibleItems.some(row => row.can_participate && !rows.some(item => item.dynamic_id === row.dynamic_id));
  document.getElementById('pool-retry').disabled = locked || !failures;
  document.getElementById('pool-clear').disabled = locked || !rows.some(row => ['success', 'skipped'].includes(row.state));
  const list = document.getElementById('pool-list');
  const html = rows.length ? rows.map((row, index) => `<li class="pool-row is-${row.state}">
    <span class="pool-position">${index + 1}</span>
    <div class="pool-copy"><span class="pool-title" title="${escapeHtml(row.title)}">${escapeHtml(row.title)}</span>
    <span class="pool-note">${escapeHtml(row.lottery_type)} · ${escapeHtml(row.message || labels[row.state])}</span></div>
    <span class="pool-state">${labels[row.state] || '待参与'}</span>
    <div class="pool-row-actions">
      <button type="button" class="pool-icon" data-pool-op="move" data-id="${row.dynamic_id}" data-direction="-1" aria-label="上移 ${escapeHtml(row.title)}" ${locked || index === 0 ? 'disabled' : ''}>↑</button>
      <button type="button" class="pool-icon" data-pool-op="move" data-id="${row.dynamic_id}" data-direction="1" aria-label="下移 ${escapeHtml(row.title)}" ${locked || index === rows.length - 1 ? 'disabled' : ''}>↓</button>
      <button type="button" class="pool-icon" data-pool-op="remove" data-id="${row.dynamic_id}" aria-label="移出 ${escapeHtml(row.title)}" ${locked ? 'disabled' : ''}>×</button>
    </div></li>`).join('') : '<li class="pool-empty">在活动旁点击「＋ 预选」，或将本页可参与活动一起加入。</li>';
  if (list.innerHTML !== html) list.innerHTML = html;
  document.querySelectorAll('[data-pool-add]').forEach(button => {
    const selected = rows.some(row => row.dynamic_id === button.dataset.poolAdd);
    button.textContent = selected ? '已预选' : '＋ 预选';
    button.disabled = selected || locked;
    button.classList.toggle('is-selected', selected);
  });
}

async function edit(operation, params = {}) {
  if (editing) return;
  editing = true;
  version += 1;
  render();
  try {
    pool = await fetchJSON('/api/participation-pool', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({operation, ...params}) });
    if (operation === 'add') showToast('已加入预选池', 'success');
  } catch (error) {
    showToast(error.message || '预选操作失败', 'error');
  } finally {
    editing = false;
    render();
  }
}

export function bindPool() {
  const placeProgress = () => {
    const banner = document.getElementById('progress-banner');
    const inPool = Boolean(document.querySelector('#section-activities.active[data-subview="pool"]'));
    if (inPool) document.getElementById('pool-body').before(banner);
    else document.body.prepend(banner);
    banner.classList.toggle('is-pool-progress', inPool);
  };
  window.addEventListener('binggo:activity-view', placeProgress);
  window.addEventListener('binggo:section', placeProgress);
  document.getElementById('pool-toggle')?.addEventListener('click', () => {expanded = !expanded; render();});
  document.getElementById('pool-add-page')?.addEventListener('click', () => edit('add', {ids: visibleItems.filter(row => row.can_participate).map(row => row.dynamic_id)}));
  document.getElementById('pool-pause')?.addEventListener('click', () => edit('pause'));
  document.getElementById('pool-retry')?.addEventListener('click', () => edit('retry'));
  document.getElementById('pool-clear')?.addEventListener('click', () => edit('clear_done'));
  document.getElementById('pool-start')?.addEventListener('click', async () => {
    try { await startJob('participate_pool'); await loadPool(); }
    catch (error) {showToast(error.message || '启动预选池失败', 'error');}
  });
  document.addEventListener('click', event => {
    const add = event.target.closest('[data-pool-add]');
    if (add && !add.disabled) edit('add', {ids: [add.dataset.poolAdd]});
    const button = event.target.closest('[data-pool-op]');
    if (button && !button.disabled) edit(button.dataset.poolOp, {dynamic_id: button.dataset.id, direction: Number(button.dataset.direction || 0)});
  });
  window.setInterval(() => {
    if (!editing && (pool.running || state.currentJob?.action === 'participate_pool' || document.querySelector('#section-activities.active'))) loadPool().catch(() => {});
  }, 2000);
}

export function resetPoolAccount() {
  version++; fetching = false; editing = false; busyJob = false; lastJobKey = '';
  pool = {items: [], running: false, pending: 0, paused: false}; visibleItems = []; render();
}
