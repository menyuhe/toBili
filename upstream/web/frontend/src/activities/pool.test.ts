// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';

vi.mock('../api/client', () => ({ fetchJSON: vi.fn() }));
vi.mock('../jobs/index', () => ({ startJob: vi.fn() }));
vi.mock('../shell/toast', () => ({ showToast: vi.fn() }));
vi.mock('./index', () => ({ loadActivities: vi.fn() }));
vi.mock('../state', () => ({ state: { currentJob: null } }));

afterEach(() => { vi.useRealTimers(); document.body.innerHTML = ''; });

it('keeps activity rows, focus and scroll while adding and polling pool membership', async () => {
  vi.useFakeTimers();
  const { fetchJSON } = await import('../api/client');
  const { loadActivities } = await import('./index');
  const { bindPool, loadPool } = await import('./pool');
  let items: any[] = [];
  vi.mocked(fetchJSON).mockImplementation(async (_url, options) => {
    if (options?.method === 'POST') {
      items = [{ dynamic_id: '123', title: '礼盒', lottery_type: '转发抽奖', state: 'pending' }];
    }
    return { items, pending: items.length, running: false, paused: false };
  });
  document.body.innerHTML = `
    <div id="section-activities" class="active"><div class="table-wrap"><table><tbody>
      <tr id="original-row"><td><button data-pool-add="123">＋ 预选</button></td></tr>
      <tr><td><button id="next-button">下一条</button></td></tr>
    </tbody></table></div></div>
    <div id="participation-pool"><span id="pool-count"></span><span id="pool-tab-count"></span>
      <p id="pool-summary"></p><button id="pool-toggle"></button><div id="pool-body"></div>
      <button id="pool-start"></button><button id="pool-pause"></button>
      <button id="pool-add-page"></button><button id="pool-retry"></button>
      <button id="pool-clear"></button><ul id="pool-list"></ul></div>`;
  bindPool();
  await loadPool();
  const row = document.getElementById('original-row');
  const viewport = document.querySelector('.table-wrap')!;
  viewport.scrollTop = 180;
  (document.querySelector('[data-pool-add]') as HTMLButtonElement).click();
  await vi.advanceTimersByTimeAsync(0);
  const next = document.getElementById('next-button')!;
  next.focus();
  await vi.advanceTimersByTimeAsync(4000);
  expect(document.querySelector('[data-pool-add]')!.textContent).toBe('已预选');
  expect(document.getElementById('pool-tab-count')!.textContent).toBe('1');
  expect(document.getElementById('original-row')).toBe(row);
  expect(viewport.scrollTop).toBe(180);
  expect(document.activeElement).toBe(next);
  expect(loadActivities).not.toHaveBeenCalled();
});
