import { expect, it } from 'vitest';
import { accountTaskText } from './status';

it('shows a short current step for a pool task with its progress', () => {
  expect(accountTaskText({state:'running', action:'participate_pool', progress_message:'2/15 · 奖品 · 正在关注（1/6）', progress_step:1, progress_total:15}, true))
    .toBe('关注呢，别催～ 1/15');
});
it('does not mistake a prize title for the current action', () => {
  expect(accountTaskText({state:'running', action:'participate_pool', progress_message:'2/15 · 关注送好礼'}, true)).toBe('预选池，挨个抽！');
});
it('keeps the task type visible for result checks and cleanup', () => {
  expect(accountTaskText({state:'running', action:'maintenance_check'}, true)).toContain('开奖');
  expect(accountTaskText({state:'running', action:'maintenance_clean'}, true)).toContain('清理');
});
it('prioritizes cancellation and does not show finished steps as running', () => {
  expect(accountTaskText({state:'running', action:'participate', cancel_requested:true}, true)).toContain('准备停下');
  expect(accountTaskText({state:'success', progress_message:'正在关注'}, true)).toBe('已保存登录');
  expect(accountTaskText({state:'idle'}, false)).toBe('需重新登录');
});
