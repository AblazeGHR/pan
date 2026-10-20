/* global fetch, console, process, Buffer */
import assert from 'node:assert/strict';
import path from 'node:path';
import { URL } from 'node:url';
import { chromium, expect } from '@playwright/test';
const [base, sessionId, runtime, adapter = 'codex'] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
assert.ok(!['8767', '8768', ''].includes(new URL(base).port));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  const writes = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.url().endsWith('/native-control/write')) writes.push({ status: response.status(), body: response.request().postData() });
  });
  const history = async () => {
    const response = await fetch(`${base}/api/sessions/${sessionId}/history?limit=50`);
    assert.equal(response.status, 200);
    return (await response.json()).history;
  };
  const waitHistory = async count => {
    for (let attempts = 0; attempts < 600; attempts++) {
      const rows = await history();
      if (rows.filter(row => row.role === 'assistant').length === count) return rows;
      await page.waitForTimeout(50);
    }
    await page.screenshot({ path: path.join(runtime, 'native-sync-failed.png') });
    throw new Error(`history did not reach ${count}: ${JSON.stringify({ rows: await history(), writes, errors })}`);
  };
  await page.goto(`${base}/react/?panE2E=1`, { waitUntil: 'domcontentloaded' });
  await page.locator(`[data-session-card-id="${sessionId}"]`).click();
  const composer = page.locator('[contenteditable="true"][data-placeholder]').first();
  await composer.fill('BROWSER PAN ONE');
  await composer.press('Enter');
  await waitHistory(1);
  await page.getByText('BROWSER PAN ONE', { exact: true }).first().waitFor();
  await page.getByRole('button', { name: 'Terminal TUI', exact: true }).click();
  const terminal = page.locator('.xterm-helper-textarea');
  await terminal.waitFor({ state: 'attached' });
  await terminal.focus();
  await page.keyboard.type('BROWSER NATIVE TWO', { delay: 35 });
  await page.waitForTimeout(1000); // Native input paste coalescing before Enter.
  await page.keyboard.press('Enter');
  await waitHistory(2);
  await page.getByRole('button', { name: 'Switch to Bubble view' }).click();
  await page.getByText('BROWSER NATIVE TWO', { exact: true }).first().waitFor();
  if (adapter === 'codex') {
    await page.screenshot({ path: path.join(runtime, 'two-turns.png') });
    await expect(page.locator('.msg.assistant').getByText('synthetic native answer', { exact: true })).toHaveCount(2);
  }
  await composer.fill('BROWSER PAN THREE');
  await composer.press('Enter');
  await waitHistory(3);
  await page.getByRole('button', { name: 'Terminal TUI', exact: true }).click();
  await terminal.waitFor({ state: 'attached' });
  // xterm's accessibility tree is not the rendered screen. Read the native
  // output API independently to prove Pan's later input reached that screen.
  let cursor = '0';
  let screen = '';
  for (;;) {
    const response = await fetch(`${base}/api/sessions/${sessionId}/native-control/output?cursor=${cursor}`,
      { headers: { Origin: base, 'Sec-Fetch-Site': 'same-origin' } });
    assert.equal(response.status, 200);
    const output = (await response.json()).result;
    if (!output.data) break;
    screen += Buffer.from(output.data, 'base64').toString('utf8');
    cursor = output.cursor;
  }
  assert.ok(screen.includes('BROWSER PAN THREE'), screen.slice(-2000));
  await page.reload();
  await page.locator(`[data-session-card-id="${sessionId}"]`).click();
  await terminal.waitFor({ state: 'attached' });
  await terminal.focus();
  await page.keyboard.type('BROWSER NATIVE FOUR', { delay: 35 });
  await page.waitForTimeout(1000);
  await page.keyboard.press('Enter');
  await waitHistory(4);
  await page.getByRole('button', { name: 'Switch to Bubble view' }).click();
  await page.getByText('BROWSER NATIVE TWO', { exact: true }).first().waitFor();
  await page.getByText('BROWSER PAN THREE', { exact: true }).first().waitFor();
  if (adapter === 'codex') await expect(page.locator('.msg.assistant').getByText('synthetic native answer', { exact: true })).toHaveCount(4);
  await page.screenshot({ path: path.join(runtime, 'native-sync.png') });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, actualPanHTTP: true, actualPanWS: true,
    actualCLI: adapter, browser: 'Chromium', turns: 4, refreshPersistent: true, pageErrors: errors }));
} finally {
  await browser.close();
}
