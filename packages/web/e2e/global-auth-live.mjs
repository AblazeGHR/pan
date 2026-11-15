import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';

const [base, out, phase = 'login'] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || /:(8767|8768)$/.test(base)) throw Error('Owned dynamic port required');
if (!process.env.PAN_TEST_AUTH_PASSWORD) throw Error('Owned test password required');
await fs.mkdir(out, { recursive: true });
const profile = path.join(out, 'tmp-browser-profile');
const errors = [];
const launch = () => chromium.launchPersistentContext(profile, { headless: true, viewport: { width: 1280, height: 850 } });
let context = await launch();
let page = context.pages()[0] || await context.newPage();
page.on('pageerror', error => errors.push(error.message));
const checks = {};
try {
  await page.goto(base + '/react/');
  if (phase === 'login') {
    await expect(page.getByRole('heading', { name: '登录 Pan', exact: true })).toBeVisible();
    const denied = await page.request.get(base + '/api/sessions');
    expect(denied.status()).toBe(401);
    for (const width of [1280, 390, 320]) {
      await page.setViewportSize({ width, height: 850 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: path.join(out, `login-${width}.png`) });
    }
    await page.getByLabel('管理员密码', { exact: true }).fill(process.env.PAN_TEST_AUTH_PASSWORD);
    const loggedIn = page.waitForResponse(r => r.url() === base + '/api/automation/login' && r.request().method() === 'POST');
    await page.getByRole('button', { name: '登录', exact: true }).click();
    expect((await loggedIn).status()).toBe(200);
    await expect(page.getByRole('heading', { name: '登录 Pan', exact: true })).toHaveCount(0);
    expect((await page.request.get(base + '/api/sessions')).status()).toBe(200);
    const cookies = await context.cookies();
    const session = cookies.find(c => c.name === 'pan_automation_session');
    expect(session?.httpOnly).toBe(true);
    expect(session?.expires).toBeGreaterThan(Date.now() / 1000 + 29 * 86400);
    const storage = await page.evaluate(() => ({ local: Object.values(localStorage), session: Object.values(sessionStorage), cookie: document.cookie }));
    expect(JSON.stringify(storage)).not.toContain(process.env.PAN_TEST_AUTH_PASSWORD);
    expect(storage.cookie).not.toContain('pan_automation_session');
    checks.login = true;
    await context.close();
    context = await launch();
    page = context.pages()[0] || await context.newPage();
    await page.goto(base + '/react/');
    await expect(page.getByRole('heading', { name: '登录 Pan', exact: true })).toHaveCount(0);
    expect((await page.request.get(base + '/api/sessions')).status()).toBe(200);
    checks.browserReopen = true;
    await page.screenshot({ path: path.join(out, 'authenticated.png') });
  } else if (phase === 'restart') {
    expect((await page.request.get(base + '/api/sessions')).status()).toBe(200);
    await expect(page.getByRole('heading', { name: '登录 Pan', exact: true })).toHaveCount(0);
    checks.coldRestart = true;
    const logout = await page.evaluate(async () => (await fetch('/api/automation/logout', { method: 'POST' })).status);
    expect(logout).toBe(200);
    // Existing WS revocation brings the boundary back without a navigation.
    await expect(page.getByRole('heading', { name: '登录 Pan', exact: true })).toBeVisible({ timeout: 6000 });
    expect((await page.request.get(base + '/api/sessions')).status()).toBe(401);
    checks.logout = true;
  }
  expect(errors).toEqual([]);
} finally {
  if (!page.isClosed()) {
    await page.screenshot({ path: path.join(out, `last-${phase}.png`) });
    await fs.writeFile(path.join(out, `layout-${phase}.json`), JSON.stringify(await page.evaluate(() =>
      [...document.querySelectorAll('html,body,#root,main,section,h1')].map(node => ({
        tag: node.tagName, classes: node.className, rect: node.getBoundingClientRect().toJSON(),
        display: getComputedStyle(node).display, visibility: getComputedStyle(node).visibility,
      }))), null, 2));
  }
  await context.close();
  await fs.writeFile(path.join(out, `browser-${phase}.json`), JSON.stringify({ phase, checks, errors }, null, 2));
}
