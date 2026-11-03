// Local browser QA with isolated relay storage; never contacts Bilibili.
import { chromium, expect } from '../web/frontend/node_modules/@playwright/test/index.mjs';
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, 'ui-review/cloud-v1');
mkdirSync(output, { recursive: true });
mkdirSync(path.join(root, 'test-runtime'), { recursive: true });
const runtime = mkdtempSync(path.join(root, 'test-runtime/cloud-ui-'));
const origin = 'http://127.0.0.1:18991';
const python = path.join(root, '.venv/Scripts/python.exe');
const server = spawn(python, ['-m', 'uvicorn', 'cloud.app:create_app', '--factory', '--app-dir', 'upstream', '--host', '127.0.0.1', '--port', '18991', '--log-level', 'warning'], {
  cwd: root, windowsHide: true, env: { ...process.env, RELAY_PUBLIC_ORIGIN: origin,
    RELAY_DATABASE_URL: `sqlite:///${path.join(runtime, 'relay.db').replaceAll('\\', '/')}`, RELAY_REGISTRATION_INVITE: 'ui-test-invite' },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let logs = '';
server.stderr.on('data', data => { logs += data.toString(); });
let browser, desktopServer;
try {
  let ready = false;
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(`Relay exited: ${logs}`);
    try { if ((await fetch(origin + '/api/v1/health')).ok) { ready = true; break; } } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!ready) throw new Error('Relay did not start');
  const credentials = { username: 'ui_tester', password: 'local-browser-test-123', device_name: 'Windows 测试设备', kind: 'windows', invite: 'ui-test-invite' };
  const register = await fetch(origin + '/api/v1/auth/register', { method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json' }, body: JSON.stringify(credentials) });
  if (!register.ok) throw new Error(await register.text());
  const desktop = await register.json();
  const uid = '123456', did = '1234567890123456789';
  const desktopRequest = async (endpoint, body) => {
    const response = await fetch(origin + '/api/v1' + endpoint, { method: 'POST', headers: { Authorization: `Bearer ${desktop.device_token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!response.ok) throw new Error(`${endpoint}: ${await response.text()}`);
    return response.json();
  };
  await desktopRequest('/accounts/bind', { bili_uid: uid });
  await desktopRequest('/devices/heartbeat', { bili_uid: uid, ready: true });
  await desktopRequest('/sync/push', { bili_uid: uid, changes: [
    { change_id: 'ui_activity_01', kind: 'activity', key: did, base_version: 0, value: { dynamic_id: did, title: '秋日好礼 · 机械键盘与桌面周边', lottery_type: '互动抽奖', lottery_time: Math.floor(Date.now() / 1000) + 86400, activity_status: '未参加', draw_status: 'active' } },
    { change_id: 'ui_activity_02', kind: 'activity', key: '2234567890123456789', base_version: 0, value: { dynamic_id: '2234567890123456789', title: '周末抽奖 · 咖啡礼盒', lottery_type: '转发抽奖', lottery_time: Math.floor(Date.now() / 1000) - 86400, activity_status: '已结束', draw_status: 'ended' } },
    { change_id: 'ui_outcome_01', kind: 'outcome', key: '2234567890123456789', base_version: 0, value: { outcome: 'won', source: 'manual' } },
  ] });
  browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, reducedMotion: 'reduce', colorScheme: 'light' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://**/*', route => route.abort());
  await page.goto(origin);
  await expect(page.locator('#login-panel')).toBeVisible();
  await page.screenshot({ path: path.join(output, 'mobile-login.png'), fullPage: true });
  await page.locator('#username').fill(credentials.username);
  await page.locator('#password').fill(credentials.password);
  await page.getByRole('button', { name: '登录应用账号', exact: true }).click();
  await expect(page.locator('#dashboard')).toBeVisible();
  await expect(page.locator('.activity-card')).toHaveCount(2);
  await expect(page.locator('#executor-state')).toContainText('在线');
  await expect(page.locator('#password')).toHaveValue('');
  const cookies = await context.cookies();
  expect(cookies.find(cookie => cookie.name === 'tobili_session').httpOnly).toBeTruthy();
  await page.locator(`[data-keep="${did}"]`).click();
  await expect(page.locator('#notice')).toContainText('保留标记已同步');
  await expect(page.locator(`[data-keep="${did}"]`)).toHaveText('取消保留');
  await page.locator(`[data-participate="${did}"]`).click();
  await expect(page.locator('#notice')).toContainText('任务已提交');
  await page.screenshot({ path: path.join(output, 'mobile-activities.png'), fullPage: true });
  await page.locator('[data-tab="tasks"]').click();
  await expect(page.locator('.task-card')).toHaveCount(1);
  await page.screenshot({ path: path.join(output, 'mobile-tasks.png'), fullPage: true });
  await page.locator('[data-cancel]').click();
  await expect(page.locator('.task-card')).toContainText('已取消');
  await page.locator('[data-action="maintenance_check"]').click();
  await expect(page.locator('.task-card')).toHaveCount(2);
  await page.locator('[data-tab="devices"]').click();
  await expect(page.locator('.device-card')).toHaveCount(2);
  await page.screenshot({ path: path.join(output, 'mobile-devices.png'), fullPage: true });
  for (const width of [360, 390, 768]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  }
  await page.evaluate(async () => { await navigator.serviceWorker.ready; });
  const cacheKeys = await page.evaluate(async () => {
    const keys = await caches.keys(), urls = [];
    for (const key of keys) for (const request of await (await caches.open(key)).keys()) urls.push(new URL(request.url).pathname);
    return urls;
  });
  expect(cacheKeys.some(url => url.startsWith('/api/'))).toBeFalsy();
  await page.locator('#logout').click();
  await expect(page.locator('#login-panel')).toBeVisible();
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  expect(errors).toEqual([]);
  const desktopOrigin = 'http://127.0.0.1:18992';
  desktopServer = spawn(python, ['-m', 'uvicorn', 'web.app:app', '--app-dir', 'upstream', '--host', '127.0.0.1', '--port', '18992', '--log-level', 'warning'], {
    cwd: root, windowsHide: true, env: { ...process.env, BINGGO_HOME: path.join(runtime, 'desktop'), BINGGO_E2E: '1', BINGGO_DISABLE_BACKGROUND: '1', BILI_COOKIE: '', LLM_API_KEY: '' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  desktopServer.stderr.on('data', data => { logs += data.toString(); });
  ready = false;
  for (let i = 0; i < 100; i++) {
    if (desktopServer.exitCode !== null) throw new Error(`Desktop exited: ${logs}`);
    try { if ((await fetch(desktopOrigin + '/api/cloud/status')).ok) { ready = true; break; } } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!ready) throw new Error('Desktop preview did not start');
  const desktopContext = await browser.newContext({ viewport: { width: 1320, height: 880 }, reducedMotion: 'reduce' });
  const desktopPage = await desktopContext.newPage();
  desktopPage.on('pageerror', error => errors.push(error.message));
  await desktopPage.goto(desktopOrigin);
  await desktopPage.locator('[data-section="settings"]').click();
  await expect(desktopPage.locator('#cloud-panel')).toBeVisible();
  await desktopPage.locator('#cloud-panel').scrollIntoViewIfNeeded();
  await desktopPage.locator('#cloud-server').fill(origin);
  await desktopPage.locator('#cloud-user').fill(credentials.username);
  await desktopPage.locator('#cloud-password').fill(credentials.password);
  await desktopPage.locator('#cloud-device').fill('桌面 UI 测试设备');
  await desktopPage.locator('#cloud-login button[type="submit"]').click();
  await expect(desktopPage.locator('#cloud-connected')).toBeVisible();
  await expect(desktopPage.locator('#cloud-account-info')).toContainText(credentials.username);
  await expect(desktopPage.locator('#cloud-password')).toHaveValue('');
  await desktopPage.locator('#cloud-panel').screenshot({ path: path.join(output, 'desktop-settings.png') });
  await desktopPage.reload();
  await desktopPage.locator('[data-section="settings"]').click();
  await expect(desktopPage.locator('#cloud-connected')).toBeVisible();
  await desktopPage.locator('#cloud-disconnect').click();
  await expect(desktopPage.locator('#cloud-login')).toBeVisible();
  expect(errors).toEqual([]);
  writeFileSync(path.join(output, 'report.json'), JSON.stringify({ ok: true, widths: [360, 390, 768], checks: ['login', 'httpOnly session', 'activities', 'keep', 'submit', 'cancel', 'devices', 'offline shell only', 'logout', 'desktop connect', 'desktop session restore', 'desktop disconnect'], errors }, null, 2));
  process.stdout.write('PWA browser checks passed; screenshots: ui-review/cloud-v1\n');
} finally {
  if (browser) await browser.close();
  if (desktopServer) desktopServer.kill();
  server.kill();
}
