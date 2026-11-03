// @vitest-environment jsdom
import { expect, it, vi } from 'vitest';
vi.mock('../api/client', () => ({ fetchJSON: vi.fn() }));
vi.mock('../shell/toast', () => ({ showToast: vi.fn() }));
vi.mock('../shell/confirm', () => ({ openAppConfirm: vi.fn().mockResolvedValue(true) }));

it('keeps cross-page editorial selection separate from pool and saves edited text and template', async () => {
  const { fetchJSON } = await import('../api/client');
  const { bindArticle } = await import('./index');
  document.body.innerHTML = '<section id="section-article"></section>';
  const rows = Array.from({ length: 24 }, (_, i) => ({ dynamic_id: String(100 + i), activity_title: `奖品 ${i}`, lottery_type: ['互动抽奖', '转发抽奖', '预约抽奖'][i % 3], lottery_time: '未公布', lottery_timestamp: i === 23 ? null : Date.parse(`2026-10-${i === 0 ? '21' : '22'}T${i === 0 ? '00:00:00' : '23:59:59'}+08:00`) / 1000, activity_status: '未参加' }));
  let saved: any;
  vi.mocked(fetchJSON).mockImplementation(async (url, opts) => {
    if (url.endsWith('candidates')) return { items: rows };
    if (url.endsWith('generate')) return { title: '整理标题', body: '分类正文', warnings: [] };
    if (opts?.method === 'PUT') { saved = JSON.parse(String(opts.body)); return { ok: true }; }
    return { uid: '123', ids: [], title: '', body: '', template: { title: '{count} 个活动', intro: '本期', outro: '祝好运' } };
  });
  const click = (id: string) => (document.getElementById(`article-${id}`) as HTMLButtonElement).click();
  bindArticle(); window.dispatchEvent(new CustomEvent('binggo:section', { detail: 'article' }));
  await vi.waitFor(() => expect(document.querySelectorAll('[data-article-id]').length).toBe(20));
  for (const tone of ['interactive', 'repost', 'reservation']) expect(document.querySelector(`.article-tone-${tone}`)).not.toBeNull();
  (document.querySelector('[data-article-id="100"]') as HTMLInputElement).click();
  click('next'); (document.querySelector('[data-article-id="120"]') as HTMLInputElement).click();
  click('generate');
  await vi.waitFor(() => expect(document.querySelector('#article-body .ql-editor')?.textContent).toBe('分类正文'));
  const { default: Quill } = await import('quill');
  const body = Quill.find(document.getElementById('article-body')!) as InstanceType<typeof Quill>;
  body.setText('我手动修改后的正文', 'user');
  body.formatText(0, 3, { bold: true, color: '#c44383' }, 'user');
  click('save');
  await vi.waitFor(() => expect(saved?.body).toBe('我手动修改后的正文'));
  await vi.waitFor(() => expect((document.getElementById('article-save') as HTMLButtonElement).disabled).toBe(false));
  expect(saved.ids).toEqual(['100', '120']); expect(saved.uid).toBe('123');
  expect(saved.body_html).toContain('<strong'); expect(saved.body_html).toContain('color:');
  expect(vi.mocked(fetchJSON).mock.calls.every(([url]) => !url.includes('participation-pool'))).toBe(true);
  expect(saved.template.title).toBe('{count} 个活动');
  const input = (id: string, value: string) => {
    const element = document.getElementById(`article-${id}`) as HTMLInputElement;
    element.value = value; element.dispatchEvent(new Event('input'));
  };
  input('draw-start', '2026-10-21'); input('draw-end', '2026-10-21');
  expect(document.querySelectorAll('[data-article-id]').length).toBe(1);
  expect(document.querySelectorAll('[data-article-remove]').length).toBe(2); // Never discard hidden selection.
  expect((document.getElementById('article-generate') as HTMLButtonElement).disabled).toBe(false);
  expect(document.getElementById('article-range-warning')?.textContent).toContain('1 个');
  click('generate');
  await vi.waitFor(() => expect(document.getElementById('article-warning')?.textContent).toContain('跳过 1 个'));
  const request = vi.mocked(fetchJSON).mock.calls.filter(([url]) => url.endsWith('generate')).at(-1)!;
  expect(JSON.parse(String(request[1]?.body)).ids).toEqual(['100']);
  expect(document.querySelectorAll('[data-article-remove]').length).toBe(2);
  input('draw-start', '2026-10-20'); input('draw-end', '2026-10-20');
  expect((document.getElementById('article-generate') as HTMLButtonElement).disabled).toBe(true);
  input('draw-start', '2026-10-21');
  input('draw-end', '2026-10-22');
  expect((document.getElementById('article-generate') as HTMLButtonElement).disabled).toBe(false);
  click('next'); expect(document.querySelectorAll('[data-article-id]').length).toBe(3); // Unknown date excluded.
  input('draw-start', '2026-10-23');
  expect(document.getElementById('article-range-warning')?.textContent).toContain('开始日期不能晚于结束日期');
  expect((document.getElementById('article-save') as HTMLButtonElement).disabled).toBe(true);
  input('draw-start', '2026-10-21'); click('save');
  await vi.waitFor(() => expect(saved.draw_end).toBe('2026-10-22'));
  expect(saved.draw_start).toBe('2026-10-21'); expect(saved.ids).toEqual(['100', '120']);
  await vi.waitFor(() => expect((document.getElementById('article-reset') as HTMLButtonElement).disabled).toBe(false));
  const { openAppConfirm } = await import('../shell/confirm');
  vi.mocked(openAppConfirm).mockResolvedValueOnce(false);
  click('reset');
  await vi.waitFor(() => expect((document.getElementById('article-reset') as HTMLButtonElement).disabled).toBe(false));
  expect(body.getText().trim()).toBe('分类正文');
  expect(document.querySelectorAll('[data-article-remove]').length).toBe(2);
  click('preview-toggle'); click('reset');
  await vi.waitFor(() => expect(body.getText().trim()).toBe(''));
  expect((document.getElementById('article-title') as HTMLInputElement).value).toBe('');
  expect(document.querySelectorAll('[data-article-remove]').length).toBe(0);
  expect(document.getElementById('article-reading-preview')?.textContent).toBe('');
  expect(document.getElementById('article-length')?.textContent).toBe('0 字');
  expect(document.getElementById('article-save-state')?.textContent).toBe('有未保存修改');
  click('save');
  await vi.waitFor(() => expect(saved.ids).toEqual([]));
  expect(saved.title).toBe(''); expect(saved.body).toBe('');
  expect(saved.template.title).toBe('{count} 个活动');
  expect(saved.draw_start).toBe('2026-10-21');
});
