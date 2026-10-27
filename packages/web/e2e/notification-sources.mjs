import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';

const [base, out, owner] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || /:(8767|8768)$/.test(base)) throw Error('Owned random port only');
const browser = await chromium.launch({ headless: true });
const evidence = { realLogin: false, errors: [], requests: [] };
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
  await context.addInitScript(() => {
    window.__notificationCalls = [];
    window.Notification = class {
      static permission = 'granted';
      static requestPermission() { throw Error('Background event must not request permission'); }
      constructor(title, options) { window.__notificationCalls.push({ title, options }); }
      close() {}
    };
  });
  const login = await context.request.post(base + '/api/automation/login', {
    headers: { Origin: base }, data: { key: process.env.PAN_TEST_NOTIFICATION_KEY },
  });
  expect(login.status()).toBe(200);
  evidence.realLogin = true;
  const page = await context.newPage();
  page.on('pageerror', (error) => evidence.errors.push(error.message));
  page.on('response', (response) => {
    if (response.url().endsWith('/api/settings/ui') && response.request().method() === 'PUT') {
      evidence.requests.push({ status: response.status(), method: 'PUT' });
    }
  });
  await page.goto(base + '/react/');
  await page.locator('button[title="App settings"]:visible').first().click();
  const settings = page.getByRole('dialog', { name: 'App Settings', exact: true });
  await settings.getByRole('tab', { name: 'Behavior', exact: true }).click();
  const behavior = settings.getByTestId('session-error-bridge-section');
  const sessionSwitch = behavior.getByRole('checkbox');
  await expect(sessionSwitch).toBeChecked();
  await sessionSwitch.click();
  await expect(sessionSwitch).not.toBeChecked();
  await expect(sessionSwitch).toBeEnabled();
  await settings.getByRole('tab', { name: 'Notifications', exact: true }).click();
  for (const [source, name] of [['lockerUnread', 'Locker new unread'], ['newError', 'New errors']]) {
    const panel = settings.getByTestId(`notification-source-${source}`);
    await panel.getByRole('checkbox', { name: 'Enabled', exact: true }).check();
    await panel.getByRole('checkbox', { name: 'QQ Report', exact: true }).check();
    await expect(panel.getByRole('button', { name: `Save ${name}`, exact: true })).toBeDisabled();
    await panel.getByRole('checkbox', { name: /Recording fake contact/ }).check();
    await expect(panel.getByRole('checkbox', { name: /Subscribe/ })).toHaveCount(0);
    await expect(panel.getByRole('checkbox', { name: 'System', exact: true })).toBeVisible();
    await expect(panel.getByRole('checkbox', { name: 'Browser', exact: true })).toBeVisible();
    if (source === 'newError') {
      await panel.getByRole('checkbox', { name: 'QQ Report', exact: true }).uncheck();
      await panel.getByRole('checkbox', { name: 'Browser', exact: true }).check();
    }
    await panel.getByRole('button', { name: `Save ${name}`, exact: true }).click();
    await expect(panel.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible();
  }
  await page.screenshot({ path: out + '/notifications-desktop.png' });
  await page.reload();
  await page.locator('button[title="App settings"]:visible').first().click();
  await page.getByRole('tab', { name: 'Notifications', exact: true }).click();
  await expect(page.getByTestId('notification-source-newError').getByRole('checkbox', { name: 'Enabled', exact: true })).toBeChecked();
  await page.getByRole('dialog', { name: 'App Settings', exact: true }).getByRole('button', { name: 'Close', exact: true }).click();
  const emit = () => context.request.post(base + '/__test/notification/emit', {
    headers: { 'x-notification-fixture': process.env.PAN_TEST_NOTIFICATION_NONCE },
    data: { owner, kind: 'error', id: 90 },
  });
  expect((await emit()).status()).toBe(200);
  expect((await emit()).status()).toBe(200);
  await expect.poll(() => page.evaluate(() => window.__notificationCalls.length)).toBe(1);
  const notices = await page.evaluate(() => window.__notificationCalls);
  expect(notices[0].options.body).toContain('se_');
  expect(notices[0].options.body).not.toContain('private-value');
  evidence.liveAuthenticatedBrowserDelivery = true;
  evidence.browserProvider = 'recording fake';
  evidence.duplicateEventBrowserCount = notices.length;
  // Existing Session Postbox still exposes both QQ Subscribe and Report and
  // the same System/Browser tabs. No real contact is contacted by this probe.
  const card = page.locator(`[data-session-card-id="${owner}"]`);
  await card.getByRole('button', { name: 'Session actions', exact: true }).click();
  await page.getByRole('button', { name: 'msgBridge', exact: true }).click();
  const postbox = page.getByRole('dialog', { name: 'msgBridge', exact: true });
  await expect(postbox.getByRole('button', { name: 'Subscribe', exact: true })).toBeVisible();
  await expect(postbox.getByRole('button', { name: 'Report', exact: true })).toBeVisible();
  await postbox.getByRole('tab', { name: 'System', exact: true }).click();
  await expect(postbox.getByRole('checkbox', { name: 'Completion notifications' })).toBeVisible();
  await postbox.getByRole('tab', { name: 'Browser', exact: true }).click();
  await expect(postbox.getByRole('button', { name: 'Request browser permission' })).toBeVisible();
  evidence.sessionPostboxPreserved = true;
  expect(evidence.errors).toEqual([]);
  expect(evidence.requests.length).toBeGreaterThanOrEqual(3);
  evidence.chromiumVersion = browser.version();
  await fs.writeFile(out + '/browser.json', JSON.stringify(evidence, null, 2));
} catch (error) {
  await fs.writeFile(out + '/browser-failure.json', JSON.stringify({ ...evidence, failure: String(error) }, null, 2));
  throw error;
} finally {
  await browser.close();
}
