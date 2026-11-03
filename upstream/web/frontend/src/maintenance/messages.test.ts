// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
vi.mock('../api/client', () => ({fetchJSON: vi.fn()}));
vi.mock('../jobs/index', () => ({startPolling: vi.fn(), toggleLogDock: vi.fn()}));
vi.mock('../shell/nav', () => ({activateSection: vi.fn()}));
vi.mock('../shell/toast', () => ({showToast: vi.fn()}));
vi.mock('../shell/confirm', () => ({openAppConfirm: vi.fn()}));
import { fetchJSON } from '../api/client';
import { activateSection } from '../shell/nav';
import { selectIdentity } from '../account/identity';
import { loadMessageReview, openMessageReview } from './index';

beforeEach(() => {
  vi.clearAllMocks(); selectIdentity('202');
  document.body.innerHTML = '<button data-maintenance-view="cleanup"></button><div id="maintenance-panel"><div class="maintenance-panel-head"></div><div id="cleanup-list"></div></div>';
  HTMLElement.prototype.scrollIntoView = vi.fn();
});
it('opens the selected account messages without resolving or starting cleanup', async () => {
  vi.mocked(fetchJSON).mockResolvedValue({blocked:true,pending:1,error:'',checked_at:123,items:[{key:'dm:7:9',type:'dm',talker_name:'UP',text:'请核对领奖信息',timestamp:123}]});
  await openMessageReview();
  expect(activateSection).toHaveBeenCalledWith('activities');
  expect(fetchJSON).toHaveBeenCalledTimes(1);
  expect(fetchJSON).toHaveBeenCalledWith('/api/maintenance/messages');
  const panel = document.getElementById('message-review')!;
  expect(panel.textContent).toContain('UID 202');
  expect(panel.textContent).toContain('不是已确认的中奖数');
  expect(panel.textContent).toContain('请核对领奖信息');
  expect(panel.querySelector('[data-resolve-message="irrelevant"]')?.getAttribute('data-message-key')).toBe('dm:7:9');
  expect(document.activeElement).toBe(panel);
});
it('makes message loading failures visible instead of displaying an empty success', async () => {
  vi.mocked(fetchJSON).mockRejectedValue(new Error('网络暂不可用'));
  await expect(loadMessageReview()).rejects.toThrow('网络暂不可用');
  expect(document.querySelector('[role="alert"]')?.textContent).toContain('仍会阻止清理');
});
it('asks for a manual restart when the pending messages were resolved', async () => {
  vi.mocked(fetchJSON).mockResolvedValue({blocked:true,pending:0,error:'',checked_at:123,items:[]});
  await loadMessageReview();
  expect(document.getElementById('message-review')?.textContent).toContain('请手动重新执行清理');
  expect(fetchJSON).toHaveBeenCalledTimes(1);
});
