import { fetchJSON } from '../api/client';
import { escapeHtml } from '../utils/text';
import { showToast } from '../shell/toast';
import { openAppConfirm } from '../shell/confirm';
import { RichField, copyRich, safeHTML } from './rich';

type Item = { dynamic_id: string; activity_title: string; lottery_type: string; lottery_time: string; lottery_timestamp?: number | null; activity_status: string };
type Template = { title: string; intro: string; outro: string; intro_html: string; outro_html: string; theme: string; layout: string; group_by_date: boolean; hero_image: string; separator_image: string };
type Draft = { uid: string; ids: string[]; template: Template; title: string; body: string; body_html: string; draw_start: string; draw_end: string };
const esc = (v: unknown) => escapeHtml(String(v ?? ''));
const el = (id: string) => document.getElementById(`article-${id}`) as HTMLInputElement;
const chinaDay = (timestamp: number) => new Date(timestamp * 1000 + 8 * 3600000).toISOString().slice(0, 10);
const tone = (kind: string) => ({ '互动抽奖': 'interactive', '转发抽奖': 'repost', '预约抽奖': 'reservation' }[kind] || 'other');

export function bindArticle() {
  const root = document.getElementById('section-article');
  if (!root) return;
  root.innerHTML = `<div class="article-workspace">
    <section class="panel article-picker"><div class="article-head"><div><h2>选择本期活动</h2><p class="caption">选择独立于预选池，每篇最多 100 个。</p></div><button class="btn btn-secondary btn-compact" id="article-refresh">更新列表</button></div>
      <div class="article-filters"><input class="form-input" id="article-search" placeholder="搜索奖品或活动 ID" aria-label="搜索专栏活动"><select class="form-input" id="article-type" aria-label="筛选抽奖类型"><option value="">全部类型</option><option>互动抽奖</option><option>转发抽奖</option><option>预约抽奖</option></select></div>
      <div class="article-range"><label>开奖区间<select class="form-input" id="article-range-preset"><option value="all">不限时间</option><option value="today">今天</option><option value="tomorrow">明天</option><option value="3">未来 3 天（含今天）</option><option value="7">未来 7 天（含今天）</option><option value="custom">自定义区间</option></select></label><div class="article-range-dates"><label>开始日期<input class="form-input" type="date" id="article-draw-start"></label><label>结束日期<input class="form-input" type="date" id="article-draw-end"></label></div><p class="caption">按北京时间，包含首尾两天；筛选不会移除已选活动。</p><p id="article-range-warning" class="caption" role="status"></p></div>
      <div class="article-tools"><label><input type="checkbox" id="article-ended"> 包含已结束</label><button class="btn btn-ghost btn-compact" id="article-add-page">选择本页</button><button class="btn btn-ghost btn-compact" id="article-clear">清空选择</button></div>
      <p class="caption" id="article-count" aria-live="polite"></p><div id="article-items" class="article-items"></div>
      <div class="article-pager"><button class="btn btn-secondary btn-compact" id="article-prev">上一页</button><span id="article-page" class="caption"></span><button class="btn btn-secondary btn-compact" id="article-next">下一页</button></div>
      <details class="article-chosen"><summary id="article-chosen-label">已选活动</summary><div id="article-chosen-items"></div></details>
    </section>
    <section class="panel article-editor"><div class="article-head"><div><h2>专栏草稿</h2><p class="caption">按抽奖类型归类，保留奖品、开奖时间和原动态链接。</p></div><span class="caption" id="article-save-state" role="status"></span></div>
      <details class="article-template"><summary>文章模板</summary><p class="caption">支持 {date}（今天日期）、{count}（活动数量）、{draw_range} 或 {开奖区间}（开奖日期范围）。未设区间时使用已选活动的最早至最晚开奖日期。随草稿保存，下次沿用。</p>
        <div class="article-design-options"><label>主题色<select class="form-input" id="article-theme"><option value="blue">晴空蓝</option><option value="pink">樱花粉</option><option value="orange">暖杏橙</option></select></label><label>活动排版<select class="form-input" id="article-layout"><option value="compact">简洁清单</option><option value="detailed">详细说明</option></select></label><label class="article-date-group"><input type="checkbox" id="article-group-date" checked> 类型内按开奖日期分组</label></div>
        <div class="article-template-images"><div><span>文章头图</span><img id="article-hero-preview" hidden alt="文章头图预览"><button class="btn btn-secondary btn-compact" id="article-hero-pick">选择头图</button><button class="btn btn-ghost btn-compact" id="article-hero-clear">移除</button></div><div><span>章节分隔图</span><img id="article-separator-preview" hidden alt="章节分隔图预览"><button class="btn btn-secondary btn-compact" id="article-separator-pick">选择分隔图</button><button class="btn btn-ghost btn-compact" id="article-separator-clear">移除</button></div></div>
        <label class="settings-field">标题模板<input class="form-input" id="article-template-title" maxlength="200"></label>
        <div class="settings-field article-rich-small"><span>开场 · 可设颜色、插入图片</span><div id="article-template-intro"></div></div>
        <div class="settings-field article-rich-small"><span>结尾 · 可设颜色、插入图片</span><div id="article-template-outro"></div></div></details>
      <div class="article-tools"><button class="btn btn-primary" id="article-generate">一键整理</button><button class="btn btn-secondary" id="article-save">保存草稿与模板</button><button class="btn btn-ghost" id="article-reset">一键清空</button><button class="btn btn-ghost" id="article-preview-toggle">阅读预览</button></div>
      <p id="article-warning" class="caption" role="status"></p>
      <label class="settings-field">文章标题<input class="form-input" id="article-title" maxlength="300" placeholder="选择活动后点击一键整理"></label>
      <div class="article-body-field" id="article-edit-area"><p class="caption">正文 · 选中文字设置颜色，图片可上传或直接粘贴</p><div id="article-body"></div></div><article id="article-reading-preview" class="article-paper" hidden></article>
      <div class="article-tools"><button class="btn btn-primary" id="article-copy-rich">复制富文本</button><button class="btn btn-secondary" id="article-export-rich">导出图文素材包</button><button class="btn btn-ghost" id="article-copy-title">复制标题</button><button class="btn btn-ghost" id="article-copy-body">复制纯文本</button><button class="btn btn-ghost" id="article-export">导出 TXT</button><span class="caption" id="article-length">0 字</span></div>
      <p class="caption article-publish-note">按 B站编辑器的字号与段距预览；窗口宽度会影响换行。富文本可复制颜色、链接和表情；自选图片请从素材包上传。表情、图片注释和链接卡片以 B站粘贴后的预览为准。</p>
    </section></div>`;
  let items: Item[] = [], selected = new Set<string>(), page = 1, loaded = false, busy = false, dirty = false, uid = '', uploading = 0, preview = false;
  let heroImage = '', separatorImage = '';
  const status = (text: string) => { el('save-state').textContent = text; };
  const markDirty = () => { dirty = true; status('有未保存修改'); el('length').textContent = `${bodyEditor.text().length} 字`; };
  const upload = async (file: File): Promise<string> => {
    if (!loaded || busy) throw new Error('请等待当前操作完成');
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 8 * 1024 * 1024) throw new Error('请选择不超过 8 MB 的 PNG、JPG 或 WebP 图片');
    uploading++; render();
    try {
      const data = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(new Error('图片读取失败')); reader.readAsDataURL(file); });
      return (await fetchJSON<{src: string}>('/api/article/images', {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({uid, data})})).src;
    } finally { uploading--; render(); }
  };
  const introEditor = new RichField(el('template-intro'), '模板开场', markDirty, upload);
  const outroEditor = new RichField(el('template-outro'), '模板结尾', markDirty, upload);
  const bodyEditor = new RichField(el('body'), '专栏正文', markDirty, upload);
  const editors = [introEditor, outroEditor, bodyEditor];
  editors.forEach(editor => editor.enable(false));
  const invalidRange = () => !!(el('draw-start').value && el('draw-end').value && el('draw-start').value > el('draw-end').value);
  const inRange = (r: Item) => {
    const start = el('draw-start').value, end = el('draw-end').value;
    if (!start && !end) return true;
    if (!r.lottery_timestamp) return false;
    const day = chinaDay(r.lottery_timestamp);
    return (!start || day >= start) && (!end || day <= end);
  };
  const filtered = () => items.filter(r => (!el('type').value || r.lottery_type === el('type').value)
    && inRange(r)
    && (el('ended').checked || r.activity_status !== '已结束')
    && `${r.activity_title} ${r.dynamic_id}`.toLowerCase().includes(el('search').value.trim().toLowerCase()));
  const eligibleIds = () => [...selected].filter(id => items.some(r => r.dynamic_id === id && inRange(r)));
  const render = () => {
    const scrollTop = el('items').scrollTop;
    const focusedId = (document.activeElement as HTMLElement | null)?.dataset.articleId;
    const rows = filtered(), pages = Math.max(1, Math.ceil(rows.length / 20)); page = Math.min(page, pages);
    el('count').textContent = `已选 ${selected.size} / 100 · 筛选到 ${rows.length} 个活动`;
    el('items').innerHTML = rows.slice((page - 1) * 20, page * 20).map(r => `<label class="article-choice article-tone-${tone(r.lottery_type)}"><input type="checkbox" data-article-id="${esc(r.dynamic_id)}" ${selected.has(r.dynamic_id) ? 'checked' : ''}><span><strong>${esc(r.activity_title)}</strong><small>${esc(r.lottery_type)} · ${esc(r.lottery_time)}${r.activity_status === '已结束' ? ' · 已结束' : ''}</small></span></label>`).join('') || '<p class="article-empty">没有匹配活动，试试调整筛选或先更新数据源。</p>';
    el('prev').disabled = page <= 1; el('next').disabled = page >= pages;
    const eligible = eligibleIds(), outside = selected.size - eligible.length;
    el('range-warning').textContent = invalidRange() ? '开始日期不能晚于结束日期' : outside ? `已选中 ${outside} 个区间外、时间待定或不可用的活动，整理时将跳过；可整理 ${eligible.length} 个。${eligible.length ? '' : '请调整区间或重新选择活动。'}` : '';
    el('generate').disabled = busy || !!uploading || !loaded || !eligible.length || invalidRange();
    el('reset').disabled = busy || !!uploading || !loaded;
    el('save').disabled = busy || !!uploading || !loaded || invalidRange();
    for (const id of ['export-rich', 'copy-rich', 'hero-pick', 'separator-pick']) el(id).disabled = busy || !!uploading || !loaded;
    el('page').textContent = `${page} / ${pages}`;
    el('chosen-label').textContent = `已选 ${selected.size} 个（含其他页）`;
    const byId = new Map(items.map(r => [r.dynamic_id, r]));
    el('chosen-items').innerHTML = [...selected].map(id => `<div class="article-chosen-row article-tone-${tone(byId.get(id)?.lottery_type || '')}"><span>${esc(byId.get(id)?.activity_title || `活动 ${id}（已移出列表）`)}${byId.has(id) && !inRange(byId.get(id)!) ? '<small>区间外 / 时间待定</small>' : ''}</span><button class="btn btn-ghost btn-compact" data-article-remove="${esc(id)}" aria-label="移除 ${esc(byId.get(id)?.activity_title || id)}">移除</button></div>`).join('');
    if (focusedId) [...root.querySelectorAll<HTMLInputElement>('[data-article-id]')].find(r => r.dataset.articleId === focusedId)?.focus({ preventScroll: true });
    el('items').scrollTop = scrollTop;
  };
  const draft = (): Draft => ({ uid, ids: [...selected], draw_start: el('draw-start').value, draw_end: el('draw-end').value, template: { title: el('template-title').value,
    intro: introEditor.text(), outro: outroEditor.text(), intro_html: introEditor.html(), outro_html: outroEditor.html(), theme: el('theme').value, layout: el('layout').value, group_by_date: el('group-date').checked, hero_image: heroImage, separator_image: separatorImage }, title: el('title').value, body: bodyEditor.text(), body_html: bodyEditor.html() });
  const run = async (fn: () => Promise<void>) => {
    if (busy) return; busy = true;
    editors.forEach(editor => editor.enable(false));
    root.querySelectorAll<HTMLButtonElement>('button').forEach(b => { b.disabled = true; });
    try { await fn(); } catch (e) { showToast(e instanceof Error ? e.message : '操作失败，草稿已保留', 'error'); }
    finally { busy = false; editors.forEach(editor => editor.enable(loaded)); root.querySelectorAll<HTMLButtonElement>('button').forEach(b => { b.disabled = false; }); render(); }
  };
  const load = async () => {
    const [data, saved] = await Promise.all([fetchJSON<{ items: Item[] }>('/api/article/candidates'), loaded ? Promise.resolve(null) : fetchJSON<Draft>('/api/article/draft')]);
    items = data.items;
    if (saved) {
      uid = saved.uid;
      selected = new Set(saved.ids);
      el('draw-start').value = saved.draw_start || ''; el('draw-end').value = saved.draw_end || '';
      el('range-preset').value = saved.draw_start || saved.draw_end ? 'custom' : 'all';
      el('template-title').value = saved.template.title;
      introEditor.set(saved.template.intro, saved.template.intro_html); outroEditor.set(saved.template.outro, saved.template.outro_html);
      el('theme').value = saved.template.theme || 'blue'; el('layout').value = saved.template.layout || 'compact'; el('group-date').checked = saved.template.group_by_date !== false;
      heroImage = saved.template.hero_image || ''; separatorImage = saved.template.separator_image || ''; updateImages();
      el('title').value = saved.title; bodyEditor.set(saved.body, saved.body_html);
      status(saved.body ? '已载入本机草稿' : '尚未生成'); el('length').textContent = `${bodyEditor.text().length} 字`;
    }
    loaded = true; render();
  };
  window.addEventListener('binggo:section', event => { if ((event as CustomEvent).detail === 'article' && !loaded) void run(load); });
  el('refresh').addEventListener('click', () => void run(load));
  for (const id of ['search', 'type', 'ended']) el(id).addEventListener('input', () => { page = 1; render(); });
  for (const id of ['draw-start', 'draw-end']) el(id).addEventListener('input', () => { el('range-preset').value = 'custom'; page = 1; markDirty(); render(); });
  el('range-preset').addEventListener('change', () => {
    const value = el('range-preset').value;
    if (value === 'custom') return;
    const today = Date.now() / 1000, offset = value === 'tomorrow' ? 1 : 0;
    el('draw-start').value = value === 'all' ? '' : chinaDay(today + offset * 86400);
    el('draw-end').value = value === 'all' ? '' : chinaDay(today + (value === '3' || value === '7' ? Number(value) - 1 : offset) * 86400);
    page = 1; markDirty(); render();
  });
  root.addEventListener('change', e => {
    const target = e.target as HTMLInputElement, id = target.dataset.articleId;
    if (!id) return;
    if (target.checked && selected.size >= 100) { target.checked = false; showToast('每篇最多选择 100 个活动', 'error'); return; }
    if (target.checked) selected.add(id); else selected.delete(id);
    markDirty(); render();
  });
  root.addEventListener('click', e => {
    const button = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-article-remove]');
    if (button) { selected.delete(button.dataset.articleRemove!); markDirty(); render(); }
  });
  el('add-page').addEventListener('click', () => { for (const r of filtered().slice((page - 1) * 20, page * 20)) { if (selected.size < 100) selected.add(r.dynamic_id); } markDirty(); render(); });
  el('clear').addEventListener('click', () => { selected.clear(); markDirty(); render(); });
  el('prev').addEventListener('click', () => { page--; render(); }); el('next').addEventListener('click', () => { page++; render(); });
  for (const id of ['template-title', 'title', 'theme', 'layout', 'group-date']) el(id).addEventListener('input', markDirty);
  el('generate').addEventListener('click', () => void run(async () => {
    if (bodyEditor.text() && !await openAppConfirm({ title: '重新整理正文？', desc: '会根据当前选择和模板重新生成标题、正文。已保存的草稿在再次保存前仍可恢复。', confirmLabel: '重新整理', cancelLabel: '取消' })) return;
    const { draw_start, draw_end, template } = draft();
    const ids = eligibleIds(), skipped = selected.size - ids.length;
    if (!ids.length || invalidRange()) return;
    const data = await fetchJSON<{ title: string; body: string; body_html: string; warnings: string[] }>('/api/article/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid, ids, template, draw_start, draw_end }) });
    el('title').value = data.title; bodyEditor.set(data.body, data.body_html); refreshPreview();
    el('warning').textContent = `已整理 ${ids.length} 个活动${skipped ? `，跳过 ${skipped} 个不符合区间或不可用的活动` : ''}。` + (data.warnings.length ? `发布前核对：${data.warnings.join('；')}` : '可继续修改标题和正文。'); markDirty();
  }));
  el('reset').addEventListener('click', () => void run(async () => {
    if (!await openAppConfirm({ title: '清空当前专栏？', desc: '清空当前标题、正文和已选活动，保留文章模板及筛选条件。保存后将覆盖本机草稿。', confirmLabel: '清空', cancelLabel: '取消' })) return;
    selected.clear(); el('title').value = ''; bodyEditor.set('', '');
    el('warning').textContent = ''; refreshPreview(); markDirty();
  }));
  el('save').addEventListener('click', () => void run(async () => {
    const value = JSON.stringify(draft());
    await fetchJSON('/api/article/draft', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: value });
    dirty = JSON.stringify(draft()) !== value; status(dirty ? '有未保存修改' : '已保存到本机');
  }));
  for (const part of ['title', 'body']) el(`copy-${part}`).addEventListener('click', async () => {
    const value = part === 'body' ? bodyEditor.text() : el(part).value;
    if (!value.trim()) { showToast('请先生成或填写内容', 'error'); return; }
    try { await navigator.clipboard.writeText(value); showToast(part === 'title' ? '标题已复制' : '正文已复制', 'success'); }
    catch { showToast('剪贴板暂不可用，请导出 TXT 或手动选中复制', 'info'); }
  });
  el('export').addEventListener('click', () => {
    const d = draft(); if (!d.body.trim()) { showToast('请先生成正文', 'error'); return; }
    const url = URL.createObjectURL(new Blob(['\ufeff', d.title, '\n\n', d.body], { type: 'text/plain;charset=utf-8' }));
    const a = document.createElement('a'); a.href = url; a.download = `${(d.title || '抽奖专栏').replace(/[\\/:*?"<>|]/g, '_').slice(0, 80)}.txt`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  const updateImages = () => {
    for (const [key, src] of [['hero', heroImage], ['separator', separatorImage]]) {
      const img = document.getElementById(`article-${key}-preview`) as HTMLImageElement;
      img.hidden = !src; if (src) img.src = src; else img.removeAttribute('src');
    }
  };
  for (const key of ['hero', 'separator']) {
    el(`${key}-pick`).addEventListener('click', () => {
      const input = document.createElement('input'); input.type = 'file'; input.accept = 'image/png,image/jpeg,image/webp';
      input.addEventListener('change', async () => {
        if (!input.files?.[0]) return;
        try { const src = await upload(input.files[0]); if (key === 'hero') heroImage = src; else separatorImage = src; updateImages(); markDirty(); }
        catch (error) { showToast(error instanceof Error ? error.message : '图片添加失败', 'error'); }
      }); input.click();
    });
    el(`${key}-clear`).addEventListener('click', () => { if (key === 'hero') heroImage = ''; else separatorImage = ''; updateImages(); markDirty(); });
  }
  const refreshPreview = () => { if (preview) el('reading-preview').innerHTML = `<h1>${esc(el('title').value)}</h1>${safeHTML(bodyEditor.html())}`; };
  el('preview-toggle').addEventListener('click', () => {
    preview = !preview; el('edit-area').hidden = preview; el('reading-preview').hidden = !preview;
    el('preview-toggle').textContent = preview ? '继续编辑' : '阅读预览'; refreshPreview();
  });
  el('copy-rich').addEventListener('click', async () => {
    try { await copyRich(bodyEditor.html(), bodyEditor.text()); showToast('富文本已复制；图片请从素材包上传到 B 站', 'success'); }
    catch { showToast('富文本剪贴板暂不可用，请导出素材包后打开 article.html 复制', 'info'); }
  });
  el('export-rich').addEventListener('click', () => void run(async () => {
    const response = await fetch('/api/article/export', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(draft())});
    if (!response.ok) { const error = await response.json(); throw new Error(error.detail || '导出失败'); }
    const url = URL.createObjectURL(await response.blob()), link = document.createElement('a');
    link.href = url; link.download = 'toBili-图文素材包.zip'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }));
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  const content = root.closest<HTMLElement>('.content-area');
  if (content && typeof ResizeObserver !== 'undefined') {
    const resize = () => {
      const style = getComputedStyle(content);
      root.style.setProperty('--article-available-height', `${content.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)}px`);
    };
    new ResizeObserver(resize).observe(content); resize();
  }
  render();
}
