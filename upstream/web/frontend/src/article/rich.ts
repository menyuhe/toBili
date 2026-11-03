import Quill from 'quill';
import DOMPurify from 'dompurify';
import 'quill/dist/quill.snow.css';
import { showToast } from '../shell/toast';
import emotes from '../../../article_emotes.json';

Quill.register(Quill.import('attributors/style/align') as any, true);
const Size = Quill.import('attributors/style/size') as any;
Size.whitelist = ['14px', '17px']; Quill.register(Size, true);
const BlockEmbed = Quill.import('blots/block/embed') as any;
class Divider extends BlockEmbed { static blotName = 'divider'; static tagName = 'hr'; }
Quill.register(Divider);
const Delta = Quill.import('delta') as any;
const emoteByLocal = new Map(emotes.map(e => [e.local, e]));
export const BILI_COLORS = ['#2f3238','#61666d','#9499a0','#c9ccd0','#89e1e1','#95e4af','#ffdb93','#ffc18f','#fcafaa','#d8b6ff','#88a4ff','#4fd3d1','#2ac864','#ffcc00','#ff7f24','#f85a54','#ac6dff','#6188ff','#018488','#089043','#aa7d00','#bb4100','#c9272c','#723ecc','#3752c8','#00699d','#c44383'];
const BILI_BACKGROUNDS = ['#bfedfa','#ffeec9','#ffd9e4','#caf1d6','#ffe1c7','#d7dfff','#efe7e0','#ebeff4'];
const localImage = /^\/api\/article\/assets\/\d+\/[a-f0-9]{64}\.png$/;
const tags = ['p', 'br', 'strong', 'b', 'em', 'i', 'u', 's', 'span', 'h2', 'h3', 'blockquote', 'ol', 'ul', 'li', 'a', 'img', 'hr'];
export function safeHTML(html: string): string {
  const fragment = DOMPurify.sanitize(html, { ALLOWED_TAGS: tags, ALLOWED_ATTR: ['style', 'href', 'src', 'alt', 'width', 'height'], RETURN_DOM_FRAGMENT: true });
  fragment.querySelectorAll('img').forEach(img => {
    const src = img.getAttribute('src') || '', emote = emoteByLocal.get(src);
    if (emote) { img.width = 24; img.height = 24; img.alt = emote.name; }
    else if (!localImage.test(src)) img.remove();
  });
  fragment.querySelectorAll('[style]').forEach(node => {
    const style = (node as HTMLElement).style;
    for (const property of Array.from({ length: style.length }, (_, i) => style.item(i))) if (!['color', 'background-color', 'font-size', 'font-weight', 'text-align', 'text-decoration'].includes(property)) style.removeProperty(property);
  });
  const box = document.createElement('div'); box.append(fragment); return box.innerHTML;
}
export function plainHTML(text: string): string {
  return text.split('\n').map(line => { const p = document.createElement('p'); p.textContent = line; return p.outerHTML; }).join('');
}
export function richText(html: string): string {
  const box = document.createElement('div'); box.innerHTML = safeHTML(html);
  box.querySelectorAll('a').forEach(a => { if (a.href && a.textContent !== a.getAttribute('href')) a.append(`（${a.getAttribute('href')}）`); });
  box.querySelectorAll('img').forEach(img => img.replaceWith(document.createTextNode(emoteByLocal.get(img.getAttribute('src') || '')?.name || '[图片]')));
  box.querySelectorAll('br').forEach(br => br.replaceWith(document.createTextNode('\n')));
  box.querySelectorAll('p,h2,h3,blockquote,li').forEach(e => e.append('\n'));
  return (box.textContent || '').trim();
}

export class RichField {
  readonly quill: Quill;
  constructor(container: HTMLElement, label: string, changed: () => void, upload: (file: File) => Promise<string>) {
    this.quill = new Quill(container, {
      theme: 'snow', placeholder: label,
      formats: ['header', 'size', 'bold', 'italic', 'underline', 'strike', 'color', 'background', 'align', 'link', 'image', 'blockquote', 'list', 'divider'],
      modules: {
        toolbar: [[{ header: [2, 3, false] }, { size: ['14px', false] }], ['bold', 'italic', 'underline', 'strike'], [{ color: [...BILI_COLORS, false] }, { background: [...BILI_BACKGROUNDS, false] }], [{ align: [] }, { list: 'ordered' }, { list: 'bullet' }], ['link', 'image', 'blockquote'], ['clean']],
        history: { delay: 500, userOnly: true },
        uploader: { handler: (range: {index: number}, files: File[]) => { void this.insertFile(files[0], upload, range.index); } },
      },
    });
    const q = this.quill;
    q.root.setAttribute('role', 'textbox'); q.root.setAttribute('aria-label', label); q.root.setAttribute('aria-multiline', 'true');
    q.clipboard.addMatcher('IMG', (node, delta) => {
      const src = (node as Element).getAttribute('src') || '';
      return localImage.test(src) || emoteByLocal.has(src) ? delta : new Delta();
    });
    q.on('text-change', (_delta, _old, source) => { if (source !== 'silent') changed(); });
    const toolbar = q.getModule('toolbar') as { container: HTMLElement; addHandler: (name: string, fn: () => void) => void };
    toolbar.addHandler('image', () => {
      const range = q.getSelection(true), input = document.createElement('input'); input.type = 'file'; input.accept = 'image/png,image/jpeg,image/webp';
      input.addEventListener('change', () => { if (input.files?.[0]) void this.insertFile(input.files[0], upload, range?.index); }); input.click();
    });
    const labels: Record<string, string> = { bold: '加粗', italic: '斜体', underline: '下划线', strike: '删除线', background: '背景颜色', size: '字号', blockquote: '引用', list: '列表', color: '文字颜色', align: '段落对齐', link: '插入链接', image: '插入图片', clean: '清除格式', header: '标题级别' };
    for (const [key, name] of Object.entries(labels)) toolbar.container.querySelectorAll(`.ql-${key}`).forEach(e => { e.setAttribute('title', name); e.setAttribute('aria-label', name); });
    for (const [name, label] of [['undo', '撤销'], ['redo', '重做']] as const) {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = name === 'undo' ? '↶' : '↷'; button.title = label; button.setAttribute('aria-label', label);
      button.addEventListener('click', () => q.history[name]()); toolbar.container.append(button);
    }
    const extra = document.createElement('div'); extra.className = 'article-rich-extras';
    toolbar.container.after(extra);
    let range = { index: 0, length: 0 };
    q.on('selection-change', value => { if (value) range = value; });
    const action = (label: string, fn: () => void) => {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = label;
      b.addEventListener('mousedown', e => e.preventDefault()); b.addEventListener('click', () => { if (q.isEnabled()) fn(); }); extra.append(b); return b;
    };
    action('分隔线', () => { const i = range.index; q.insertEmbed(i, 'divider', true, 'user'); q.setSelection(i + 1, 0, 'silent'); range = {index:i + 1, length:0}; });
    for (const [format, label] of [['color', '自定义字色'], ['background', '自定义底色']]) {
      const input = document.createElement('input'); input.type = 'color'; input.value = format === 'color' ? '#ff7f24' : '#ffeec9'; input.setAttribute('aria-label', label);
      input.addEventListener('input', () => { if (!q.isEnabled()) return; q.setSelection(range, 'silent'); q.format(format, input.value, 'user'); });
      const wrapper = document.createElement('label'); wrapper.textContent = label; wrapper.append(input); extra.append(wrapper);
    }
    const panel = document.createElement('div'); panel.className = 'article-emote-panel'; panel.hidden = true;
    const toggle = action('表情', () => { panel.hidden = !panel.hidden; toggle.setAttribute('aria-expanded', String(!panel.hidden)); });
    toggle.setAttribute('aria-expanded', 'false');
    const search = document.createElement('input'); search.placeholder = '搜索 B站表情'; search.setAttribute('aria-label', '搜索 B站表情'); panel.append(search);
    const grid = document.createElement('div'); grid.className = 'article-emote-grid'; panel.append(grid);
    const renderEmotes = () => {
      grid.replaceChildren();
      for (const emote of emotes.filter(e => e.name.toLowerCase().includes(search.value.toLowerCase()))) {
        const b = document.createElement('button'); b.type = 'button'; b.title = emote.name; b.setAttribute('aria-label', emote.name);
        const img = document.createElement('img'); img.src = emote.local; img.alt = emote.name; img.width = img.height = 24; img.loading = 'lazy'; b.append(img);
        b.addEventListener('mousedown', e => e.preventDefault());
        b.addEventListener('click', () => { if (!q.isEnabled()) return; const i = range.index; q.insertEmbed(i, 'image', emote.local, 'user'); q.formatText(i, 1, { alt: emote.name, width: '24', height: '24' }, 'user'); q.setSelection(i + 1, 0, 'silent'); range = { index:i + 1, length:0 }; panel.hidden = true; toggle.setAttribute('aria-expanded','false'); }); grid.append(b);
      }
    };
    search.addEventListener('input', renderEmotes); renderEmotes();
    const unicode = document.createElement('div'); unicode.className = 'article-emote-grid';
    for (const emoji of ['😀','😂','🥰','🥳','🤔','😭','🎉','🎁','🎊','✨','❤️','🔥','👍','🙏','🍀','💰','🏆','📅','✅','⚠️']) {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = emoji; b.setAttribute('aria-label', emoji);
      b.addEventListener('mousedown', e => e.preventDefault()); b.addEventListener('click', () => { if (!q.isEnabled()) return; const i = range.index; q.insertText(i, emoji, 'user'); q.setSelection(i + emoji.length, 0, 'silent'); range = {index:i + emoji.length, length:0}; panel.hidden = true; toggle.setAttribute('aria-expanded','false'); }); unicode.append(b);
    }
    panel.append(unicode); extra.after(panel);
    panel.addEventListener('keydown', e => { if (e.key === 'Escape') { panel.hidden = true; toggle.setAttribute('aria-expanded','false'); toggle.focus(); } });
  }
  async insertFile(file: File | undefined, upload: (file: File) => Promise<string>, index?: number) {
    if (!file) return;
    try {
      const src = await upload(file);
      const position = Math.min(index ?? this.quill.getLength() - 1, this.quill.getLength() - 1);
      this.quill.insertEmbed(position, 'image', src, 'user');
      this.quill.insertText(position + 1, '\n', 'user');
      this.quill.setSelection(position + 2, 0, 'silent');
    } catch (error) { showToast(error instanceof Error ? error.message : '图片添加失败', 'error'); }
  }
  set(text: string, html = '') {
    this.quill.setContents(this.quill.clipboard.convert({ html: safeHTML(html || plainHTML(text)) }), 'silent');
    this.quill.history.clear();
  }
  html() { return safeHTML(this.quill.getSemanticHTML()); }
  text() { return richText(this.html()); }
  enable(value: boolean) { this.quill.enable(value); }
}

export async function copyRich(html: string, text: string) {
  const clean = safeHTML(html), box = document.createElement('div'); box.innerHTML = clean;
  // Local image URLs cannot be resolved by Bilibili. Export their files separately.
  const images = new Map<string, number>();
  box.querySelectorAll('img').forEach(img => {
    const src = img.getAttribute('src')!;
    const emote = emoteByLocal.get(src);
    if (emote) { img.src = emote.src; img.width = img.height = 24; img.style.verticalAlign = 'text-bottom'; return; }
    if (!images.has(src)) images.set(src, images.size + 1);
    img.replaceWith(document.createTextNode(`【图片 ${images.get(src)}：请从图文素材包上传】`));
  });
  // Match the measured Bilibili editor defaults without relying on our app CSS.
  box.style.cssText = 'font-family:"PingFang SC","Helvetica Neue","Microsoft YaHei",sans-serif;font-size:17px;line-height:1.7;color:#2f3238';
  box.querySelectorAll<HTMLElement>('p,h2,h3,blockquote,ul,ol').forEach(e => {
    e.style.margin = `${e.previousElementSibling ? (e.matches('h2,h3') ? 36 : 24) : 0}px 0 0`;
    e.style.lineHeight = '1.7';
    if (e.matches('h2,h3')) { e.style.fontSize = e.matches('h2') ? '18px' : '17px'; e.style.fontWeight = '500'; }
  });
  await navigator.clipboard.write([new ClipboardItem({
    'text/html': new Blob([box.outerHTML], { type: 'text/html' }),
    'text/plain': new Blob([text], { type: 'text/plain' }),
  })]);
}
