/* global document, window, process, URL, console */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium, expect } from '@playwright/test';

const base = process.env.PAN_E2E_BASE_URL;
if (!base || [8767, 8768].includes(Number(new URL(base).port))) throw new Error('An isolated unprotected PAN_E2E_BASE_URL is required');
const runtime = path.resolve(process.env.PAN_E2E_RUNTIME || 'test-results/shortcut-capture');
await fs.mkdir(runtime, { recursive: true });
const profile = await fs.mkdtemp(path.join(runtime, 'chromium-profile-'));
const context = await chromium.launchPersistentContext(profile, { headless: true, viewport: { width: 1100, height: 700 } });
const page = context.pages()[0];
page.setDefaultTimeout(10000);
const results = [];
const requests = [];
const pageErrors = [];
const ownedSessions = [];
page.on('pageerror', (error) => pageErrors.push(String(error)));
page.on('request', (request) => {
  if (request.method() === 'POST' && /\/queue$|\/worker\/steer$/.test(new URL(request.url()).pathname)) {
    requests.push({ path: new URL(request.url()).pathname, body: request.postDataJSON() });
  }
});
async function api(method, endpoint, body) {
  const response = await context.request.fetch(base + endpoint, { method, data: body });
  assert.ok(response.ok(), `${endpoint}: ${response.status()}`);
  return response.json();
}
const row = (id) => page.locator(`[data-shortcut-action="${id}"]`);
async function openSettings() {
  await page.locator('button[title="App settings"]').first().click();
  await page.getByRole('tab', { name: 'Shortcuts', exact: true }).click();
}
async function closeSettings() {
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'App Settings' })).toHaveCount(0);
}
async function record(id, keys, label) {
  await row(id).locator('[data-shortcut-record]').click();
  await page.keyboard.press(keys);
  await expect(row(id).locator('[data-shortcut-binding]')).toHaveText(label);
  await expect(row(id).getByRole('status')).toContainText('Saved');
}
async function select(session) {
  await page.locator('[data-session-card-id]').filter({ hasText: session.name }).first().click();
  await expect(page.getByTestId('rich-text-composer')).toBeVisible();
}
async function draft(text) {
  await page.getByTestId('rich-text-composer').fill(text);
}
async function text() {
  return page.locator('#chatInput').inputValue();
}
async function mark(name, evidence = {}) {
  results.push({ name, ...evidence });
  console.log(`PASS ${name}`);
}

try {
  await api('PUT', '/api/settings/ui', { shortcuts: {} });
  const session = await api('POST', '/api/sessions', { name: `Capture-${Date.now()}`, adapter: 'cbc', mcpServers: [], outputMode: 'stream' });
  ownedSessions.push(session.id);
  await page.goto(`${base}/react/`);
  await select(session);
  await openSettings();

  // Actual Chromium keyboard delivery, including the user's Ctrl+4 report.
  for (const [keys, label] of [
    ['Control+4', 'Ctrl+4'], ['Control+5', 'Ctrl+5'], ['Control+Shift+4', 'Ctrl+Shift+4'],
    ['Control+Shift+F10', 'Ctrl+Shift+F10'], ['Alt+4', 'Alt+4'], ['F8', 'F8'],
  ]) await record('composer.steer', keys, label);
  await mark('real keyboard matrix: Ctrl+4/5, Ctrl+Shift+4/F10, Alt+4, F8');

  // A lower row must own the alert, and the alert must be within the scroll viewport.
  const lastRow = row('session.toggleNotification');
  await lastRow.locator('[data-shortcut-record]').click();
  await page.keyboard.press('Control+k');
  await expect(lastRow.getByRole('alert')).toContainText('Open command palette');
  const visibleAlert = await lastRow.getByRole('alert').evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return element.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
  });
  assert.ok(visibleAlert, 'inline conflict alert is occluded/outside the visible viewport');
  await page.screenshot({ path: path.join(runtime, 'inline-conflict.png') });
  await mark('conflict alert visible at the edited lower row', { visibleAlert });
  await lastRow.locator('[data-shortcut-record]').click();
  await page.keyboard.press('Control+r');
  await expect(lastRow.getByRole('alert')).toContainText('reserved');
  await lastRow.locator('[data-shortcut-record]').click();
  await page.keyboard.press('x');
  await expect(lastRow.getByRole('alert')).toContainText('modifier');
  await page.keyboard.press('Tab');
  assert.notEqual(await lastRow.locator('[data-shortcut-record]').getAttribute('aria-pressed'), 'true');
  assert.notEqual(await page.evaluate(() => document.activeElement?.getAttribute('data-shortcut-record')), 'session.toggleNotification');
  await lastRow.locator('[data-shortcut-record]').click();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'App Settings' })).toBeVisible();
  await mark('reserved/unsupported keys give local feedback; Tab moves focus; Escape cancels only capture');

  await page.route('**/api/settings/ui', async (route) => {
    if (route.request().method() === 'PUT') return route.fulfill({ status: 503, json: { detail: 'isolated write failure' } });
    return route.continue();
  });
  await row('composer.steer').locator('[data-shortcut-clear]').click();
  await expect(row('composer.steer').getByRole('alert')).toContainText('Could not save');
  await expect(row('composer.steer').locator('[data-shortcut-binding]')).toHaveText('F8');
  await page.unroute('**/api/settings/ui');
  await row('composer.steer').locator('[data-shortcut-clear]').click();
  await expect(row('composer.steer').getByRole('status')).toContainText('Saved');
  await expect(row('composer.steer').locator('[data-shortcut-binding]')).toHaveText('Not set');
  await row('composer.steer').locator('[data-shortcut-reset]').click();
  await expect(row('composer.steer').getByRole('status')).toContainText('Saved');
  await mark('failed save reports locally and rolls back; clear/reset persist');

  // Capturing a global action's own default must not execute that action.
  const collapsedBefore = await page.evaluate(() => window.localStorage.getItem('pan:sidebarCollapsed'));
  await record('app.toggleSidebar', 'Control+b', 'Ctrl+B');
  assert.equal(await page.evaluate(() => window.localStorage.getItem('pan:sidebarCollapsed')), collapsedBefore);
  await closeSettings();
  await page.locator('body').click({ position: { x: 1050, y: 20 } });
  await page.keyboard.press('Control+b');
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('pan:sidebarCollapsed'))).not.toBe(collapsedBefore);
  await page.keyboard.press('Control+b');
  await openSettings();
  await mark('recording suppresses global action; capture cleanup restores immediate global execution');

  await record('composer.steer', 'Control+4', 'Ctrl+4');
  await record('composer.newline', 'Control+Enter', 'Ctrl+Enter');
  await row('composer.send').locator('[data-shortcut-record]').click();
  await page.keyboard.press('Control+Enter');
  await expect(row('composer.send').getByRole('alert')).toContainText('Insert newline');
  await closeSettings();
  await draft('abcdef');
  const composer = page.getByTestId('rich-text-composer');
  await composer.press('Home');
  await composer.press('ArrowRight');
  await composer.press('Shift+ArrowRight');
  await composer.press('Shift+ArrowRight');
  const beforeNewline = requests.length;
  await composer.press('Control+Enter');
  await expect.poll(text).toBe('a\ndef');
  assert.equal(await composer.evaluate((element) => document.activeElement === element), true);
  await composer.press('Control+z');
  await expect.poll(text).toBe('abcdef');
  assert.equal(requests.length, beforeNewline);
  await mark('custom newline replaces selection, keeps focus/native undo, sends zero requests');

  // Plain textarea compatibility path, revealed only in this test.
  const textarea = page.locator('#chatInput');
  await textarea.evaluate((element) => { element.className = ''; element.removeAttribute('aria-hidden'); element.tabIndex = 0; });
  await textarea.fill('abcdef');
  await textarea.focus();
  await textarea.evaluate((element) => element.setSelectionRange(1, 3));
  await textarea.press('Control+Enter');
  await expect(textarea).toHaveValue('a\ndef');
  await textarea.press('Control+z');
  await expect(textarea).toHaveValue('abcdef');
  await mark('textarea path keeps selection and native undo');

  await page.reload();
  await openSettings();
  await expect(row('composer.steer').locator('[data-shortcut-binding]')).toHaveText('Ctrl+4');
  await expect(row('composer.newline').locator('[data-shortcut-binding]')).toHaveText('Ctrl+Enter');
  const diskSettings = await api('GET', '/api/settings/ui');
  assert.equal(diskSettings.shortcuts['composer.steer'], 'Mod+4');
  await closeSettings();
  await select(session);
  await expect(composer).toHaveAttribute('data-placeholder', /Ctrl\+Enter/);
  await mark('real backend settings write/read + reload persistence + current hint');

  // A persisted collision can arrive from another client/config file: neither side may execute.
  const conflictBindings = { ...diskSettings.shortcuts, 'composer.send': 'Mod+Enter', 'queue.enqueueLocked': 'Ctrl+Enter' };
  await api('PUT', '/api/settings/ui', { shortcuts: conflictBindings });
  await page.reload();
  await select(session);
  await draft('conflicting draft');
  const beforeConflict = requests.length;
  await composer.press('Control+Enter');
  assert.equal(requests.length, beforeConflict);
  assert.equal(await text(), 'conflicting draft');
  await api('PUT', '/api/settings/ui', { shortcuts: diskSettings.shortcuts });
  await page.reload();
  await select(session);
  await mark('persisted send/newline/queue alias collision produces zero requests and no edit');

  // Test UI routing with an isolated WS running fixture and intercepted Steer responses.
  await api('POST', '/__e2e/broadcast', { event: { type: 'worker.status', sessionId: session.id, workerId: 'capture-fixture-worker', status: 'running', generation: 999 } });
  await expect(page.getByRole('button', { name: 'Steer', exact: true })).toBeVisible();
  let steerOutcome = 'accepted';
  await page.route(`**/api/sessions/${session.id}/worker/steer`, (route) => route.fulfill({
    json: steerOutcome === 'accepted' ? { outcome: 'accepted', status: 'steer sent', workerId: 'capture-fixture-worker' }
      : { outcome: 'unknown', error: 'isolated admission unknown' },
  }));
  await draft('keyboard steer');
  let count = requests.length;
  await composer.press('Control+4');
  await expect.poll(() => requests.length).toBe(count + 1);
  assert.match(requests.at(-1).path, /\/worker\/steer$/);
  assert.equal(requests.at(-1).body.text, 'keyboard steer');
  await expect(page.getByRole('button', { name: 'Steer', exact: true })).toBeEnabled();
  await draft('unknown steer');
  steerOutcome = 'unknown';
  await composer.press('Control+4');
  await expect.poll(() => requests.length).toBe(count + 2);
  await expect(page.getByRole('button', { name: 'Steer', exact: true })).toBeDisabled();
  await draft('newer draft');
  await composer.press('Control+4');
  await composer.press('Enter');
  assert.equal(requests.length, count + 2);
  await mark('intercepted Steer uses same path/id; unknown admission blocks both repeat Steer and Send', { level: 'request interception + WS fixture, not provider acceptance' });

  // New disposable Session, real backend queue enqueue; no CLI/provider required.
  const real = await api('POST', '/api/sessions', { name: `Capture-real-${Date.now()}`, adapter: 'cbc', mcpServers: [], outputMode: 'stream' });
  ownedSessions.push(real.id);
  await page.reload();
  await select(real);
  await api('PATCH', `/api/sessions/${real.id}/queue/paused`, { paused: true });
  await draft('real keyboard queue');
  count = requests.length;
  const receipt = page.waitForResponse((response) => response.url().endsWith(`/api/sessions/${real.id}/queue`) && response.request().method() === 'POST');
  await composer.press('Enter');
  const response = await receipt;
  assert.ok(response.ok());
  const body = await response.json();
  assert.ok(body.item?.id, JSON.stringify(body));
  assert.equal(body.queuePaused, true);
  assert.equal(requests.length, count + 1);
  const queue = await api('GET', `/api/sessions/${real.id}/queue`);
  assert.ok(queue.items.some((item) => item.text === 'real keyboard queue'));
  await mark('real Enter send produced one durable backend queue item', { level: 'real backend durable enqueue; paused queue, deterministic local fake CLI only', receipt: body });
  await openSettings();
  await record('composer.send', 'Alt+Enter', 'Alt+Enter');
  await closeSettings();
  await draft('custom send keyboard');
  count = requests.length;
  const customReceipt = page.waitForResponse((candidate) => candidate.url().endsWith(`/api/sessions/${real.id}/queue`) && candidate.request().method() === 'POST');
  await composer.press('Alt+Enter');
  assert.ok((await customReceipt).ok());
  assert.equal(requests.length, count + 1);
  await openSettings();
  await page.getByTestId('shortcuts-restore-all').click();
  await expect(page.getByRole('status')).toContainText('Saved');
  await expect(row('composer.send').locator('[data-shortcut-binding]')).toHaveText('Enter');
  await expect(row('composer.newline').locator('[data-shortcut-binding]')).toHaveText('Shift+Enter');
  await expect(row('composer.steer').locator('[data-shortcut-binding]')).toHaveText('Not set');
  await closeSettings();
  await draft('default newline');
  count = requests.length;
  await composer.press('End');
  await composer.press('Shift+Enter');
  await composer.press('x');
  await expect.poll(text).toBe('default newline\nx');
  assert.equal(requests.length, count);
  await mark('custom Send takes effect immediately; restore-all returns Enter/Shift+Enter/unbound Steer defaults');
  assert.deepEqual(pageErrors, []);
} catch (error) {
  await page.screenshot({ path: path.join(runtime, 'failure.png'), fullPage: true });
  throw error;
} finally {
  await fs.writeFile(path.join(runtime, 'capture-results.json'), JSON.stringify({ base, profile, results, requests, pageErrors }, null, 2));
  try {
    for (const id of ownedSessions) await api('DELETE', `/api/sessions/${id}`);
  } finally {
    await context.close();
  }
}
