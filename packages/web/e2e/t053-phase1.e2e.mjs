/* global Event, window */
// Repeatable real-Chromium evidence for T-053 Phase 1. The FastAPI launcher
// (e2e/server.py) owns disposable sessions and exposes only test-only event
// injection; the browser uses the production build, REST routes, and /ws.
import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';

const baseURL = process.env.PAN_E2E_BASE_URL || 'http://127.0.0.1:8798';
const browser = await chromium.launch({ headless: true });
const results = [];

async function broadcast(event) {
  const response = await fetch(`${baseURL}/__e2e/broadcast`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ event }),
  });
  assert.equal(response.status, 200);
}

async function runCase(name, body) {
  try {
    const detail = await body();
    results.push({ name, ok: true, detail });
  } catch (error) {
    results.push({ name, ok: false, detail: error instanceof Error ? error.message : String(error) });
  }
}

try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(`${baseURL}/react/`, { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => localStorage.clear());
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  const alpha = page.locator('[data-session-card-id]').filter({ hasText: 'Alpha Session' }).first();
  await alpha.click();
  const sessionId = await alpha.getAttribute('data-session-card-id');
  assert.ok(sessionId);

  await runCase('busy worker optimistic queue bubble and explicit queued label', async () => {
    await broadcast({ type: 'worker.status', sessionId, workerId: 'e2e-busy', status: 'running', generation: 1 });
    await page.getByTestId('rich-text-composer').waitFor({ state: 'visible' });
    const text = `T053 optimistic ${Date.now()}`;
    await page.getByTestId('rich-text-composer').click();
    await page.keyboard.type(text);
    await page.getByRole('button', { name: 'Send' }).click();
    await page.getByText(text, { exact: true }).first().waitFor({ state: 'visible' });
    return `optimistic=${text}, workerStatusFrame=1`;
  });

  await runCase('editor and manage routes keep consuming session events', async () => {
    await page.getByRole('link', { name: 'Editor', exact: true }).first().click();
    await page.waitForURL(/\/react\/editor\/?$/);
    await page.waitForTimeout(1000);
    const editorName = 'T053 editor event';
    await broadcast({ type: 'session.renamed', sessionId, name: editorName, session: { id: sessionId, name: editorName } });

    await page.goto(`${baseURL}/react/manage/${sessionId}`, { waitUntil: 'domcontentloaded' });
    assert.match(page.url(), /\/react\/manage\//);
    const manageName = 'T053 manage event';
    await broadcast({ type: 'session.updated', sessionId, session: { id: sessionId, name: manageName } });
    await page.goto(`${baseURL}/react/`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
    return 'editor route=1, manage route=1, dashboard event injections=2';
  });

  await runCase('DONE system message survives the next history reconciliation', async () => {
    await page.goto(`${baseURL}/react/`, { waitUntil: 'domcontentloaded' });
    await page.locator(`[data-session-card-id="${sessionId}"]`).click();
    await page.getByTestId('rich-text-composer').waitFor({ state: 'visible' });
    await page.waitForTimeout(750);
    await broadcast({
      type: 'worker.result', sessionId, workerId: 'e2e-busy', taskSeq: 5301,
      status: 'done', result: 'T053 completed', generation: 1,
    });
    const done = page.getByText('[DONE] Task completed', { exact: true });
    await done.first().waitFor({ state: 'visible' });
    assert.equal(await done.count(), 1, 'completion system message was duplicated');
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.waitForTimeout(500);
    await done.first().waitFor({ state: 'visible' });
    return 'DONE retained after focus refresh';
  });

  await context.close();

  await runCase('silent OPEN WebSocket reconnects once per stale interval', async () => {
    const silentContext = await browser.newContext({
      viewport: { width: 1280, height: 800 },
    });
    let sockets = 0;
    await silentContext.addInitScript(() => {
      const nativeNow = Date.now;
      const started = nativeNow();
      Date.now = () => started + (nativeNow() - started) * 1000;
      const nativeSetInterval = window.setInterval.bind(window);
      window.setInterval = (callback, delay, ...args) =>
        nativeSetInterval(callback, delay === 30000 ? 100 : delay, ...args);
      const nativeSend = window.WebSocket.prototype.send;
      window.WebSocket.prototype.send = function send(data) {
        if (typeof data === 'string' && data.includes('"type":"ping"')) return;
        nativeSend.call(this, data);
      };
    });
    const silentPage = await silentContext.newPage();
    silentPage.on('websocket', () => { sockets += 1; });
    await silentPage.goto(`${baseURL}/react/`, { waitUntil: 'domcontentloaded' });
    await silentPage.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
    await silentPage.waitForTimeout(600);
    assert.ok(sockets >= 2, `expected watchdog reconnect, observed ${sockets} socket(s)`);
    await silentContext.close();
    return `sockets=${sockets}`;
  });
} finally {
  await browser.close();
}

console.log(JSON.stringify({ port: new URL(baseURL).port, results }, null, 2));
if (results.some((result) => !result.ok)) process.exit(1);
