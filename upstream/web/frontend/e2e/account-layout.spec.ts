import { test, expect } from "@playwright/test";

test.use({ channel: process.env.PLAYWRIGHT_CHANNEL, reducedMotion: "reduce" });
const states = [
  { name: "logged-out", logged_in: false, message: "请使用侧边栏扫码登录" },
  { name: "pending", logged_in: false, cookie_saved: true, message: "凭证已保存，正在验证登录状态" },
  { name: "network", logged_in: false, cookie_saved: true, network_error: true, mid: "999001", message: "暂时无法连接，请稍后刷新账号" },
  { name: "loading", logged_in: true, mid: "999001", uname: "测试账号", extras_loading: true },
];
for (const state of states) {
  test(`${state.name}: account content fits the card`, async ({ page }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("binggo-onboarding-v1", "done"));
    await page.route("**/api/account", route => route.fulfill({ json: state }));
    await page.goto("/");
    const hero = page.locator("#account-hero");
    await expect(hero.locator(":scope > .account-hero-body")).toBeVisible();
    for (const width of [1320, 960, 720, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const theme of ["light", "dark"]) {
        await page.evaluate(value => document.documentElement.dataset.theme = value, theme);
        const sizes = await hero.evaluate(el => {
          const body = el.querySelector('.account-hero-body')!;
          return { width: body.getBoundingClientRect().width,
            overflow: el.scrollWidth - el.clientWidth,
            pills: [...el.querySelectorAll('.setup-pill')].map(p => p.getBoundingClientRect().height) };
        });
        expect(sizes.width).toBeGreaterThan(width > 720 ? 300 : 100);
        expect(sizes.overflow).toBeLessThanOrEqual(1);
        expect(sizes.pills.length).toBeGreaterThan(0);
        expect(Math.max(...sizes.pills)).toBeLessThan(40);
        if (width === 1320 && theme === 'light') await hero.screenshot({ animations: "disabled", path: testInfo.outputPath(`account-layout-${state.name}.png`) });
      }
    }
  });
}
