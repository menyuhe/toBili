// @vitest-environment jsdom
import { expect, it, vi } from 'vitest';
vi.mock('../dom', () => ({toastStack:document.createElement('div'),INLINE_FEEDBACK_MS:100,inlineFeedbackTimers:new Map()}));
vi.mock('../utils/motion', () => ({prefersReducedMotion:()=>true}));
import { toastStack } from '../dom';
import { showToast } from './toast';
import { STALE_ACCOUNT_MESSAGE } from '../account/identity';
it('silently discards an old-account response but still displays real errors', () => {
  vi.useFakeTimers();
  showToast(STALE_ACCOUNT_MESSAGE, 'error');
  expect(toastStack?.childElementCount).toBe(0);
  showToast('消息读取失败', 'error');
  expect(toastStack?.textContent).toContain('消息读取失败');
  vi.clearAllTimers(); vi.useRealTimers();
});
