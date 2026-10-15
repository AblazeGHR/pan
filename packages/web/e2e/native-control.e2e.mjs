/* global URL, fetch, console, document */
import { Buffer } from 'node:buffer';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';

const adapter = process.env.PAN_NATIVE_TEST_ADAPTER === 'cbc' ? 'cbc' : 'codex';
const cwd = fileURLToPath(new URL('..', import.meta.url));
const port = await new Promise((resolve, reject) => {
  const probe = net.createServer(); probe.once('error', reject);
  probe.listen(0, '127.0.0.1', () => {
    const value = probe.address().port; probe.close(() => resolve(value));
  });
});
const server = await createServer({ configFile: false, root: path.join(cwd, 'src'), cacheDir: path.join(cwd, 'test-results/native-control/vite-cache'), resolve: { alias: { '@': path.join(cwd, 'src') } }, plugins: [react(), tailwindcss()], server: { host: '127.0.0.1', port, strictPort: true, proxy: {} } });
await server.listen();
let browser;
try {
  const url = `http://127.0.0.1:${port}/native-control-harness.html?adapter=${adapter}`;
  let ready = false;
  for (let n = 0; n < 80; n++) {
    try { ready = (await fetch(url)).ok; } catch { /* startup only */ }
    if (ready) break; await delay(100);
  }
  assert.ok(ready);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  let mode = 'pan'; let generation = 0; let enabled = true; let pendingEnabled = null;
  const writes = []; const errors = []; const modes = []; const configurations = [];
  let rejectSwitch = false;
  const state = () => ({ workerId: 'same-worker', mode, generation, error: null, enabled, pendingEnabled });
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/api/**', async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    let result = {};
    if (path.includes('/native-control')) {
      if (path.endsWith('/configuration')) {
        pendingEnabled = request.postDataJSON().enabled;
        configurations.push(pendingEnabled);
      }
      if (path.endsWith('/mode')) {
        if (rejectSwitch) {
          await route.fulfill({ status: 409, json: { ok: false, error: { code: 'native-switch-unconfirmed' } } }); return;
        }
        mode = request.postDataJSON().mode; generation++; modes.push(mode);
      }
      if (path.endsWith('/write')) { writes.push(request.postDataJSON()); result = { ok: true }; }
      else if (path.endsWith('/output')) {
        const cursor = new URL(request.url()).searchParams.get('cursor');
        result = { ...state(), cursor: '25', gap: false, data: cursor === '0' ? Buffer.from('Codex native 中文\r\n').toString('base64') : '' };
      } else result = state();
      await route.fulfill({ json: { ok: true, result } }); return;
    }
    if (path.endsWith('/queue')) result = { items: [{ id: 'q-browser', text: 'Durable queue retained', source: 'user', kind: 'task', createdAt: '2026-10-10T00:00:00Z', meta: { dispatchState: 'queued', revision: 1 } }], queueRevision: 1, paused: false };
    await route.fulfill({ json: result });
  });
  await page.goto(url);
  const tui = page.getByRole('button', { name: 'Terminal TUI', exact: true });
  try { await tui.click({ timeout: 10000 }); }
  catch (error) { throw new Error(`${error.message}; page errors: ${JSON.stringify(errors)}; body: ${await page.locator('body').innerText()}`); }
  await page.getByTestId('native-terminal').waitFor({ state: 'visible' });
  await page.getByText('Durable queue retained').waitFor({ state: 'visible' });
  for (const title of ['Restart worker', 'Interrupt', 'Takeover', 'Kill worker', 'Delayed restart: after task completion, before queued work']) {
    await page.getByTitle(title, { exact: true }).waitFor({ state: 'visible' });
  }
  assert.equal(await tui.getAttribute('aria-pressed'), 'true');
  for (const name of ['Switch to TUI view', 'Switch to Bubble view', 'Switch to Raw view']) {
    assert.equal(await page.getByRole('button', { name }).getAttribute('aria-pressed'), 'false');
  }
  assert.equal(await page.locator('[contenteditable="true"][data-placeholder]').count(), 0);
  await page.locator('.xterm-helper-textarea').focus();
  await page.keyboard.type('hello'); await page.keyboard.press('Enter');
  await page.waitForTimeout(200);
  assert.ok(writes.some((entry) => entry.text === '\r'));
  assert.ok(writes.filter((entry) => entry.text).every((entry) => entry.workerId === 'same-worker' && entry.generation === generation));
  const beforeRefresh = modes.length;
  await page.reload();
  await page.getByTestId('native-terminal').waitFor({ state: 'visible' });
  await page.getByText('Durable queue retained').waitFor({ state: 'visible' });
  assert.equal(modes.length, beforeRefresh, 'refresh must not switch control or stop the kernel');
  await page.getByRole('button', { name: 'Switch to Bubble view' }).click();
  await page.getByTestId('native-terminal').waitFor({ state: 'detached' });
  await page.locator('[contenteditable="true"][data-placeholder]').waitFor({ state: 'visible' });
  assert.deepEqual(modes, ['native', 'pan']);
  // Existing stdio Session upgrade: confirm, preserve Pan UI while pending,
  // then enter TUI only after the server acknowledges idle conversion.
  enabled = false;
  await page.goto(`${url}&standard=1`);
  page.once('dialog', (dialog) => dialog.dismiss());
  await tui.click();
  assert.deepEqual(configurations, []);
  page.once('dialog', (dialog) => dialog.accept());
  await tui.click();
  await page.waitForFunction(() => document.body.textContent.includes('Terminal TUI · pending idle'));
  assert.deepEqual(configurations, [true]);
  assert.equal(await page.getByTestId('native-terminal').count(), 0);
  assert.deepEqual(modes, ['native', 'pan'], 'pending conversion must not switch or interrupt');
  enabled = true; pendingEnabled = null; // Simulated real idle completion, not a UI timer assumption.
  await page.getByTestId('native-terminal').waitFor({ state: 'visible' });
  assert.deepEqual(modes, ['native', 'pan', 'native']);
  await page.getByRole('button', { name: 'Switch to Bubble view' }).click();
  await page.locator('[contenteditable="true"][data-placeholder]').waitFor({ state: 'visible' });
  // Unknown ownership is a write gate, not a navigation trap. No production
  // Session is mutated: this is the real UI against an intercepted fixture.
  rejectSwitch = true; mode = 'switching';
  await page.reload();
  await page.getByTestId('native-terminal').waitFor({ state: 'visible' });
  await page.getByRole('button', { name: 'Switch to Raw view' }).click();
  await page.getByTestId('native-terminal').waitFor({ state: 'detached' });
  await page.getByRole('alert').filter({ hasText: 'input is suspended' }).waitFor({ state: 'visible' });
  assert.equal(await page.locator('[contenteditable="true"][data-placeholder]').count(), 0);
  await page.getByText('Durable queue retained').waitFor({ state: 'visible' });
  await page.getByTitle('Kill worker', { exact: true }).waitFor({ state: 'visible' });
  mode = 'pan'; rejectSwitch = false;
  await page.locator('[contenteditable="true"][data-placeholder]').waitFor({ state: 'visible' });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, adapter, browser: 'Chromium', real_components: true, api: 'intercepted fixture', native_input: true, visible_queue: true, refresh_keeps_control: true, pan_composer_restored: true, stdio_upgrade_waits_idle: true, page_errors: errors }));
} finally {
  if (browser) await browser.close();
  await server.close();
}
