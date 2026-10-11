/* global URL, console, process, window, fetch, setTimeout */
/**
 * Isolated-browser E2E for the notification → session deep link (research
 * prototype, branch research/notification-click-session-20261011).
 *
 * Scope (all local, no real Pan service, no real OS notification):
 *   1. Control: plain chat URL selects nothing (no auto-select).
 *   2. Cold start `?session=<id>` selects the addressed session and consumes
 *      the parameter.
 *   3. Cold start for a deleted/unknown session degrades to a toast, consumes
 *      the parameter and selects nothing.
 *   4. Malformed session reference is rejected with a toast.
 *
 * Runs against `vite preview` of the production build on a dynamic port so the
 * production basename (/react/) and query handling are what is actually
 * exercised. Mock mode (?mock=1) patches fetch and skips the WebSocket, so the
 * page never talks to a real backend. The OS-level notification click itself
 * cannot be automated; it stays a unit-tested simulation (see report).
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import net from 'node:net';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, '..');
const repoRoot = path.resolve(here, '../../..');
const outDir = process.env.PAN_NOTIFICATION_E2E_OUT
  || path.join(repoRoot, 'audit', 'notification-click');
await fs.mkdir(outDir, { recursive: true });

const MISSING_TOAST = '通知对应的会话不存在或已被删除，无法跳转';
const INVALID_TOAST = '会话链接无效';

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

const port = await freePort();
const base = `http://127.0.0.1:${port}`;
const chatUrl = `${base}/react/`;

const viteBin = path.join(webRoot, 'node_modules', 'vite', 'bin', 'vite.js');
const server = spawn(
  process.execPath,
  [viteBin, 'preview', '--port', String(port), '--strictPort', '--host', '127.0.0.1'],
  { cwd: webRoot, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true },
);
let serverLog = '';
server.stdout.on('data', (chunk) => { serverLog += chunk; });
server.stderr.on('data', (chunk) => { serverLog += chunk; });

async function waitForServer() {
  for (let attempt = 0; attempt < 160; attempt += 1) {
    try {
      const res = await fetch(chatUrl);
      if (res.ok) return;
    } catch {
      // retry
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`preview server did not become ready\n--- vite log ---\n${serverLog}`);
}

const results = [];
async function check(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
    console.log(`[PASS] ${name}`);
  } catch (error) {
    results.push({ name, ok: false, error: String(error?.message || error) });
    console.log(`[FAIL] ${name}: ${error?.message || error}`);
  }
}

const selectedCards = (page) => page.locator('[data-session-card-id].border-l-accent');
const sessionParamOf = (page) => page.evaluate(
  () => new URL(window.location.href).searchParams.get('session'),
);

let browser;
try {
  await waitForServer();
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const pageErrors = [];
  const foreignBackendRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => {
    if (request.url().includes(':8768') || request.url().includes(':8767')) {
      foreignBackendRequests.push(`${request.method()} ${request.url()}`);
    }
  });

  await check('control: plain /react/?mock=1 selects no session', async () => {
    await page.goto(`${chatUrl}?mock=1`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
    assert.equal(await selectedCards(page).count(), 0);
  });

  await check('cold start selects the addressed session and consumes ?session=', async () => {
    await page.goto(`${chatUrl}?mock=1&session=mock-alpha`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
    await page.locator('[data-session-card-id="mock-alpha"].border-l-accent')
      .waitFor({ state: 'visible', timeout: 15000 });
    // The addressed session's persisted history renders in the chat pane.
    await page.getByText('mock 用户消息', { exact: true }).first()
      .waitFor({ state: 'visible', timeout: 15000 });
    await page.waitForFunction(
      () => new URL(window.location.href).searchParams.get('session') === null,
      undefined,
      { timeout: 15000 },
    );
    // Only the session param is consumed; the mode flag survives.
    assert.equal(new URL(page.url()).search, '?mock=1');
    assert.equal(await page.getByText(MISSING_TOAST).count(), 0);
  });

  await check('unknown session degrades to a toast and selects nothing', async () => {
    await page.goto(`${chatUrl}?mock=1&session=ghost-session-1`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
    await page.getByText(MISSING_TOAST).first().waitFor({ state: 'visible', timeout: 15000 });
    assert.equal(await selectedCards(page).count(), 0);
    await page.waitForFunction(
      () => new URL(window.location.href).searchParams.get('session') === null,
      undefined,
      { timeout: 15000 },
    );
    await page.waitForTimeout(3200); // let the 3s toast auto-dismiss before next case
  });

  await check('malformed session reference is rejected with a toast', async () => {
    await page.goto(
      `${chatUrl}?mock=1&session=${encodeURIComponent('bad ref!')}`,
      { waitUntil: 'domcontentloaded' },
    );
    await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
    await page.getByText(INVALID_TOAST).first().waitFor({ state: 'visible', timeout: 15000 });
    assert.equal(await selectedCards(page).count(), 0);
    await page.waitForFunction(
      () => new URL(window.location.href).searchParams.get('session') === null,
      undefined,
      { timeout: 15000 },
    );
  });

  await check('two tabs resolve their own ?session= independently', async () => {
    const tabA = await context.newPage();
    const tabB = await context.newPage();
    try {
      await Promise.all([
        tabA.goto(`${chatUrl}?mock=1&session=mock-alpha`, { waitUntil: 'domcontentloaded' }),
        tabB.goto(`${chatUrl}?mock=1&session=mock-bravo`, { waitUntil: 'domcontentloaded' }),
      ]);
      await tabA.locator('[data-session-card-id="mock-alpha"].border-l-accent')
        .waitFor({ state: 'visible', timeout: 15000 });
      await tabB.locator('[data-session-card-id="mock-bravo"].border-l-accent')
        .waitFor({ state: 'visible', timeout: 15000 });
      // Neither tab followed the other tab's session.
      assert.equal(await tabA.locator('[data-session-card-id="mock-bravo"].border-l-accent').count(), 0);
      assert.equal(await tabB.locator('[data-session-card-id="mock-alpha"].border-l-accent').count(), 0);
      assert.equal(await sessionParamOf(tabA), null);
      assert.equal(await sessionParamOf(tabB), null);
    } finally {
      await tabA.close();
      await tabB.close();
    }
  });

  await check('no page error and no real-backend request during the run', async () => {
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(foreignBackendRequests, []);
  });

  const failed = results.filter((entry) => !entry.ok);
  const summary = {
    ok: failed.length === 0,
    ranAt: new Date().toISOString(),
    baseURL: chatUrl,
    browserVersion: browser.version(),
    results,
    pageErrors,
    foreignBackendRequests,
  };
  await fs.writeFile(
    path.join(outDir, 'e2e-notification-deeplink.json'),
    `${JSON.stringify(summary, null, 2)}\n`,
    'utf8',
  );
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exitCode = failed.length === 0 ? 0 : 1;
} catch (error) {
  console.error(`E2E aborted: ${error?.stack || error}`);
  try {
    await fs.writeFile(
      path.join(outDir, 'e2e-notification-deeplink.json'),
      `${JSON.stringify({ ok: false, aborted: String(error?.message || error), serverLog }, null, 2)}\n`,
      'utf8',
    );
  } catch {
    // best effort
  }
  process.exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  server.kill();
}
