/* global fetch, process */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

// Real isolated Pan instance only: the base URL is supplied by the Python
// harness and never points at 8767/8768. This checks the actual React UI,
// the real PUT /api/settings/ui persistence, a real reload, and the
// save-failure alert path (the failure response is intercepted in-browser).
const baseURL = process.env.PAN_E2E_BASE_URL;
assert.ok(baseURL, 'PAN_E2E_BASE_URL is required');
const artifacts = process.env.PAN_E2E_ARTIFACTS || path.resolve('test-results/auto-force-ui');
const maId = process.env.PAN_E2E_MA_ID;
const targetId = process.env.PAN_E2E_TARGET_ID;
await fs.mkdir(artifacts, { recursive: true });

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1280, height: 900 },
  deviceScaleFactor: 1,
});

async function readSwitches(page) {
  const section = page.getByTestId('auto-force-send-section');
  await section.waitFor({ state: 'visible' });
  const switches = section.getByRole('switch');
  assert.equal(await switches.count(), 3);
  return {
    system: switches.nth(0),
    ma: switches.nth(1),
    report: switches.nth(2),
  };
}

async function openBehavior(page) {
  await page.getByTitle('App settings').first().click();
  await page.locator('#app-settings-tab-behavior').click();
  await page.getByTestId('auto-force-send-section').waitFor({ state: 'visible' });
}

async function checked(switches) {
  return {
    system: await switches.system.getAttribute('aria-checked'),
    ma: await switches.ma.getAttribute('aria-checked'),
    report: await switches.report.getAttribute('aria-checked'),
  };
}

async function backendSettings(page) {
  return page.evaluate(async () => {
    const response = await fetch('/api/settings/ui');
    return response.json();
  });
}

let page;
try {
  page = await context.newPage();
  await page.goto(`${baseURL}/react/`);
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  await openBehavior(page);
  let switches = await readSwitches(page);
  assert.deepEqual(await checked(switches), {
    system: 'false', ma: 'false', report: 'false',
  });

  await switches.system.click();
  await switches.ma.click();
  await page.waitForFunction(async () => {
    const response = await fetch('/api/settings/ui');
    const data = await response.json();
    return data.autoForceSend?.system === true && data.autoForceSend?.ma === true
      && data.autoForceSend?.report === false;
  });
  let settings = await backendSettings(page);
  assert.deepEqual(settings.autoForceSend, {
    system: true, ma: true, report: false,
  });
  await page.screenshot({
    path: path.join(artifacts, 'auto-force-settings-saved.png'),
    fullPage: true,
  });

  // Real refresh: the persisted values come back from config.json, not from
  // in-memory state.
  await page.reload();
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  await openBehavior(page);
  switches = await readSwitches(page);
  assert.deepEqual(await checked(switches), {
    system: 'true', ma: 'true', report: 'false',
  });

  // Save failure: the switch must keep the last saved value and show an
  // inline alert instead of pretending the write succeeded.
  await page.route('**/api/settings/ui', async (route) => {
    if (route.request().method() === 'PUT') {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'simulated auto-force save failure' }),
      });
      return;
    }
    await route.continue();
  });
  await switches.report.click();
  const alert = page.getByRole('alert').filter({ hasText: /simulated|500|save/i });
  await alert.waitFor({ state: 'visible' });
  assert.equal(await switches.report.getAttribute('aria-checked'), 'false');
  settings = await backendSettings(page);
  assert.equal(settings.autoForceSend.report, false);
  await page.screenshot({
    path: path.join(artifacts, 'auto-force-settings-save-failure.png'),
    fullPage: true,
  });
  await page.unroute('**/api/settings/ui');

  await page.reload();
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  await openBehavior(page);
  switches = await readSwitches(page);
  assert.deepEqual(await checked(switches), {
    system: 'true', ma: 'true', report: 'false',
  });

  // Trigger effect: the switch was set through the UI, then a real MA message
  // is sent while the target is running. The original queue item must be
  // delivered through the Steer fixture and disappear from the queue.
  if (maId && targetId) {
    await page.evaluate(async ({ ma, target }) => {
      const response = await fetch('/api/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sessionId: target,
          text: 'UI triggered MA',
          source: 'agent',
          sourceSessionId: ma,
        }),
      });
      if (!response.ok) throw new Error(`send failed: ${response.status}`);
    }, { ma: maId, target: targetId });
    await page.waitForFunction(async ({ target }) => {
      const history = await (await fetch(
        `/api/sessions/${target}/history?limit=50`)).json();
      return (history.history || []).some((row) =>
        String(row.content || '').includes('UI triggered MA'));
    }, { target: targetId }, { timeout: 20000 });
    // Assert the snapshot that satisfied the condition; a separate fetch can
    // race the receipt writer while history is already visible.
    let queue;
    const deadline = Date.now() + 20000;
    do {
      queue = await page.evaluate(async ({ target }) => (
        await (await fetch(`/api/sessions/${target}/queue`)).json()
      ), { target: targetId });
      if (!(queue.items || []).some((item) =>
        String(item.text || '').includes('UI triggered MA'))) break;
      await new Promise((resolve) => globalThis.setTimeout(resolve, 50));
    } while (Date.now() < deadline);
    assert.ok(!(queue.items || []).some((item) =>
      String(item.text || '').includes('UI triggered MA')), JSON.stringify(queue));
    await page.screenshot({
      path: path.join(artifacts, 'auto-force-ui-triggered.png'),
      fullPage: true,
    });
  }

  await fs.writeFile(path.join(artifacts, 'result.json'), JSON.stringify({
    ok: true,
    baseURL,
    final: await checked(switches),
    screenshots: [
      'auto-force-settings-saved.png',
      'auto-force-settings-save-failure.png',
    ],
  }, null, 2));
} finally {
  await context.close();
  await browser.close();
}
