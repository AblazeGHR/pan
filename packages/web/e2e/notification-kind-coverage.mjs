import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';

const [base, out, owner] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || /:(8767|8768)$/.test(base)) throw Error('Owned random port only');
const browser = await chromium.launch({ headless: true });
const evidence = { errors: [], browserProvider: 'recording fake', source: 'production React' };
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
  await context.addInitScript(() => {
    window.__notificationCalls = [];
    window.Notification = class {
      static permission = 'granted';
      static requestPermission() { throw Error('Background must not request permission'); }
      constructor(title, options) { window.__notificationCalls.push({ title, options }); }
      close() {}
    };
  });
  const login = await context.request.post(base + '/api/automation/login', {
    headers: { Origin: base }, data: { key: process.env.PAN_TEST_NOTIFICATION_KEY },
  });
  expect(login.status()).toBe(200);
  const page = await context.newPage();
  page.on('pageerror', (error) => evidence.errors.push(error.message));
  await page.goto(base + '/react/');
  const fixture = { 'x-notification-fixture': process.env.PAN_TEST_NOTIFICATION_NONCE };
  const emit = async (id, errorKind, code) => {
    const response = await context.request.post(base + '/__test/notification/emit', { headers: fixture,
      data: { owner, kind: 'error', id, errorKind, ...(code ? { code } : {}) } });
    expect(response.status()).toBe(200);
    return (await response.json()).id;
  };
  const records = async () => {
    const response = await context.request.get(base + '/__test/notification/records', { headers: fixture });
    expect(response.status()).toBe(200);
    return response.json();
  };
  const browserCount = () => page.evaluate(() => window.__notificationCalls.length);
  const open = async () => {
    await page.locator('button[title="App settings"]:visible').first().click();
    return page.getByRole('dialog', { name: 'App Settings', exact: true });
  };
  const settings = await open();
  await settings.getByRole('tab', { name: 'Behavior', exact: true }).click();
  const behavior = settings.getByTestId('session-error-bridge-section');
  const selector = behavior.getByTestId('error-notification-exclusions');
  await selector.getByRole('searchbox').fill('tool_failure');
  const tool = selector.getByRole('checkbox', { name: 'Pan kind · tool_failure', exact: true });
  await expect(tool).toBeVisible();
  const bounds = await selector.getByLabel('Error types', { exact: true }).evaluate((el) => ({ height: el.clientHeight, overflow: getComputedStyle(el).overflowY }));
  expect(bounds).toEqual({ height: 160, overflow: 'auto' });
  await tool.click();
  await expect(tool).toBeChecked();
  await expect(tool).toBeEnabled();
  const exclusionSwitch = selector.getByRole('checkbox', { name: 'Enable error notification exclusions' });
  await exclusionSwitch.click();
  await expect(exclusionSwitch).toBeChecked();
  await expect(exclusionSwitch).toBeEnabled();
  const sessionExcluded = await emit(201, 'tool_failure');
  expect((await records()).attempts.filter((a) => a.identity === sessionExcluded)).toHaveLength(0);
  await emit(202, 'transient_provider_error', 'unknownFutureCode');
  await expect.poll(browserCount).toBe(1);
  await expect.poll(async () => (await records()).records.length).toBe(2);
  // Switch from Session-only to global-only, editing the same saved selector.
  const sessionSwitch = behavior.getByRole('checkbox', { name: /Send Session errors/ });
  await sessionSwitch.click();
  await expect(sessionSwitch).not.toBeChecked();
  await expect(sessionSwitch).toBeEnabled();
  await settings.getByRole('tab', { name: 'Notifications', exact: true }).click();
  const panel = settings.getByTestId('notification-source-newError');
  const shared = panel.getByTestId('error-notification-exclusions');
  const globalTool = shared.getByRole('checkbox', { name: 'Pan kind · tool_failure', exact: true });
  await expect(globalTool).toBeChecked();
  await expect(shared.getByRole('checkbox', { name: 'codex_error_payload · serverOverloaded', exact: true })).toBeChecked();
  await panel.getByRole('checkbox', { name: 'Enabled', exact: true }).check();
  await panel.getByRole('button', { name: 'Save New errors', exact: true }).click();
  await expect(panel.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible();
  await expect(panel.getByRole('checkbox', { name: /Subscribe/ })).toHaveCount(0);
  const sharedSwitch = shared.getByRole('checkbox', { name: 'Enable error notification exclusions' });
  await sharedSwitch.click();
  await expect(sharedSwitch).not.toBeChecked();
  await expect(sharedSwitch).toBeEnabled();
  await expect(globalTool).toBeChecked();
  await sharedSwitch.click();
  await expect(sharedSwitch).toBeChecked();
  await expect(sharedSwitch).toBeEnabled();
  const globalExcluded = await emit(203, 'tool_failure', 'unknownFutureCode');
  expect((await records()).attempts.filter((a) => a.identity === globalExcluded)).toHaveLength(0);
  await globalTool.click();
  await expect(globalTool).not.toBeChecked();
  await expect(globalTool).toBeEnabled();
  await emit(201, 'tool_failure');
  await emit(203, 'tool_failure', 'unknownFutureCode');
  expect((await records()).attempts.filter((a) => [sessionExcluded, globalExcluded].includes(a.identity))).toHaveLength(0);
  await emit(204, 'tool_failure', 'unknownFutureCode');
  await expect.poll(browserCount).toBe(2);
  await expect.poll(async () => (await records()).records.length).toBe(4);
  await globalTool.click();
  await expect(globalTool).toBeChecked();
  await expect(globalTool).toBeEnabled();
  await page.screenshot({ path: out + '/tool-kind-notifications.png' });
  await settings.getByRole('tab', { name: 'Behavior', exact: true }).click();
  await expect(selector.getByRole('checkbox', { name: 'Pan kind · tool_failure', exact: true })).toBeChecked();
  await sessionSwitch.click();
  await expect(sessionSwitch).toBeChecked();
  await expect(sessionSwitch).toBeEnabled();
  await emit(205, 'tool_failure', 'unknownFutureCode');
  await emit(206, 'native_operation_error');
  await expect.poll(browserCount).toBe(3);
  await expect.poll(async () => (await records()).records.length).toBe(6);
  const notices = await page.evaluate(() => window.__notificationCalls);
  expect(new Set(notices.map((n) => n.options.body)).size).toBe(3);
  await page.reload();
  await open();
  await page.getByRole('tab', { name: 'Behavior', exact: true }).click();
  await expect(page.getByTestId('session-error-bridge-section').getByRole('checkbox', { name: 'Pan kind · tool_failure', exact: true })).toBeChecked();
  expect(evidence.errors).toEqual([]);
  Object.assign(evidence, { realLogin: true, sessionOnlyExcluded: true, globalOnlyExcluded: true,
    kindWithUnknownCodeExcluded: true, unmatchedTypeForwarded: true, disabledSelectionPreserved: true,
    removedSelectionNoReplay: true, sharedPersistence: true, samePathDedup: true,
    browserCount: notices.length, scrollWindow: bounds, chromiumVersion: browser.version() });
  await fs.writeFile(out + '/browser.json', JSON.stringify(evidence, null, 2));
} catch (error) {
  await fs.writeFile(out + '/browser-failure.json', JSON.stringify({ ...evidence, failure: String(error) }, null, 2));
  throw error;
} finally { await browser.close(); }
