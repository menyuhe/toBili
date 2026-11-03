// @vitest-environment jsdom
import { expect, it, vi } from 'vitest';
import { RichField, richText, safeHTML, BILI_COLORS } from './rich';
import emotes from '../../../article_emotes.json';
vi.mock('../shell/toast', () => ({ showToast: vi.fn() }));

it('roundtrips color, background, small text, lists, divider and inline Bili emotes', () => {
  document.body.innerHTML = '<div id="editor"></div>';
  const field = new RichField(document.getElementById('editor')!, '正文', vi.fn(), vi.fn());
  const html = `<p><span style="color:#ff7f24;background-color:#ffeec9;font-size:14px"><s>测试</s></span><img src="${emotes[0].local}" width="24" height="24" alt="${emotes[0].name}"></p><hr><ul><li>清单</li></ul><blockquote>引用</blockquote>`;
  field.set('', html);
  const saved = field.html();
  expect(saved).toContain('font-size: 14px'); expect(saved).toContain('background-color:');
  expect(saved).toMatch(/<s[ >]/); expect(saved).toContain('<hr'); expect(saved).toContain('<ul>');
  expect(saved).toContain('width="24"'); expect(saved).toContain(emotes[0].local);
  expect(richText(saved)).toContain(emotes[0].name);
  const delta = field.quill.getContents(); field.set('', saved); expect(field.quill.getContents()).toEqual(delta);
  expect(BILI_COLORS).toContain('#ff7f24'); expect(BILI_COLORS.length).toBeGreaterThan(25);
});

it('rejects unknown emoji paths and external image tracking', () => {
  expect(safeHTML('<img src="/api/article/emotes/unknown.webp"><img src="https://example.com/a.png">')).toBe('');
});


it('preserves the activity ID and clickable link through rich editing and plain text copy', () => {
  document.body.innerHTML = '<div id="editor"></div>';
  const field = new RichField(document.getElementById('editor')!, '正文', vi.fn(), vi.fn());
  const url = 'https://t.bilibili.com/1265663298777382912';
  field.set('', `<p><a href="${url}">查看原动态</a></p>`);
  expect(field.html()).toContain(`href="${url}"`);
  expect(richText(field.html())).toContain(url);
});
