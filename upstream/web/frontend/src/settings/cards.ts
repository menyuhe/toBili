import { escapeHtml } from '../utils/text';

export function bindSettingsCards() {
  const stack = document.querySelector<HTMLElement>('#section-settings .settings-stack');
  if (!stack) return;
  const panels = [...stack.children] as HTMLElement[];
  stack.classList.add('settings-card-grid');
  let active: HTMLElement | null = null;
  let opener: HTMLElement | null = null;
  const descriptions: Record<string, string> = {
    '参与文案': '设置参与时使用的评论与转发文案。',
    'LLM 配置': '管理模型、接口地址与密钥，测试连接。',
    '软件与数据': '查看版本、数据位置，导出诊断包。',
    '通知与提醒': '配置声音与钉钉提醒，查看发送结果。',
    '开奖与自动清理': '配置删除转发、取关与 UP 保留名单。',
    '多端同步': '共享活动资料与各账号转发关联，任务和清理权限保留本机。',
  };
  const close = () => {
    if (!active) return;
    active.hidden = true; active = null;
    document.body.classList.remove('settings-dialog-open');
    opener?.focus();
  };
  const openers = new Map<HTMLElement, () => void>();
  panels.forEach((panel, index) => {
    const title = panel.querySelector('h2')?.textContent?.trim() || '设置';
    const card = document.createElement('button');
    card.type = 'button'; card.className = 'settings-entry';
    card.setAttribute('aria-haspopup', 'dialog');
    card.innerHTML = `<span class="settings-entry-number">${String(index + 1).padStart(2, '0')}</span><strong>${escapeHtml(title)}</strong><span>${escapeHtml(descriptions[title] || '配置提醒方式与事件，查看近期发送结果。')}</span><span class="settings-entry-action">管理设置 →</span>`;
    const help = title === '软件与数据' ? descriptions[title] : '修改后请点击面板内的保存按钮；关闭保留本次填写内容。';
    const overlay = document.createElement('div');
    overlay.className = 'settings-dialog'; overlay.hidden = true;
    overlay.innerHTML = `<section class="settings-dialog-sheet" role="dialog" aria-modal="true" aria-labelledby="settings-dialog-title-${index}"><header><div><h2 id="settings-dialog-title-${index}">${escapeHtml(title)}</h2><p>${escapeHtml(help)}</p></div><button type="button" class="btn btn-secondary" aria-label="关闭${escapeHtml(title)}">关闭</button></header><div class="settings-dialog-content"></div></section>`;
    overlay.querySelector('.settings-dialog-content')!.append(panel);
    document.body.append(overlay); stack.append(card);
    const open = () => {
      close(); opener = card; active = overlay; overlay.hidden = false;
      document.body.classList.add('settings-dialog-open');
      overlay.querySelector<HTMLButtonElement>('header button')!.focus();
    };
    openers.set(panel, open);
    card.addEventListener('click', open);
    overlay.querySelector('header button')!.addEventListener('click', close);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  });
  window.addEventListener('binggo:settings-open', event => {
    const target = document.getElementById((event as CustomEvent<string>).detail);
    for (const [panel, open] of openers) if (target && (panel === target || panel.contains(target))) { open(); break; }
  });
  window.addEventListener('binggo:section', () => close());
  document.addEventListener('keydown', event => {
    if (!active || document.getElementById('app-confirm-modal')?.hidden === false) return;
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    if (event.key !== 'Tab' || !active) return;
    const focusable = [...active.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], summary')].filter(el => el.getClientRects().length);
    const first = focusable[0], last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  });
}
