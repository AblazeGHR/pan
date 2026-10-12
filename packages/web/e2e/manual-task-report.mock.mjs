/* global URL, console, document, process, window, fetch, setTimeout */
/**
 * Isolated-browser E2E for the explicit manual task report (task tail block).
 *
 * Scope (all local, no real Pan service, no real manager/QQ Session):
 *   1. Visibility: exactly one action, on the latest task's tail text block;
 *      earlier task blocks carry none.
 *   2. Routing: the click POSTs the bound (session, task, message) identity to
 *      /api/sessions/{id}/manual-report — the existing report route target.
 *   3. Unsubscribe: a Session whose manager is unsubscribed still shows and
 *      delivers the action, and no subscription toggle is ever called.
 *   4. Double click / concurrency: exactly one request, one delivery.
 *   5. Definite failure: the action re-arms and a retry delivers once.
 *   6. Uncertain outcome (transport lost before send): retry delivers once.
 *   7. Sent-but-unconfirmed (transport lost after send): retry is deduplicated
 *      by identity — still exactly one delivery.
 *   8. Session switch re-binds the action to the newly selected Session.
 *
 * Runs against the vite dev server on a dynamic port with `?mock=1`: the mock
 * layer patches fetch in-page, so the page never talks to a real backend and
 * no 8767/8768 request can occur. The browser uses an ephemeral profile.
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
const runtime = process.env.PAN_MANUAL_REPORT_E2E_RUNTIME
  || path.join(webRoot, 'test-results', 'manual-task-report-browser');
await fs.mkdir(runtime, { recursive: true });

const REPORT_BUTTON = 'button[data-manual-report]';

// ── seeded mock sessions (localStorage `pan:mockSessions`) ──

const ts = (minutesAgo) => new Date(Date.now() - minutesAgo * 60_000).toISOString();

function assignRow(messageId, taskId, content) {
  return { role: 'user', content, messageId, source: 'agent', taskIdSource: 'assign', taskId, ts: ts(30) };
}

function answerRow(messageId, content) {
  return { role: 'assistant', content, messageId, ts: ts(29) };
}

const TA_SESSIONS = [
  {
    id: 'ses_ta_report',
    name: 'TA Report Target',
    adapter: 'cbc',
    alwaysThinkingEnabled: false,
    effort: '',
    workerStatus: 'idle',
    managedBy: 'ses_mgr_mock',
    reportsToManager: false, // unsubscribed: the action must still work
    // Summary projection shape: terminal identity only, never the result body.
    lastResult: { status: 'done', taskId: 'task-2', taskSeq: 2, workerId: 'worker-mock', terminalKey: 'terminal:task-2', resultCursor: 2 },
    historyTotal: 4,
    history: [
      assignRow('pan:u1', 'task-1', 'do task 1'),
      answerRow('pan:a1', 'answer 1'),
      assignRow('pan:u2', 'task-2', 'do task 2'),
      answerRow('pan:a2', 'answer 2'),
    ],
    updatedAt: ts(1),
  },
  {
    id: 'ses_ta_plain',
    name: 'TA Plain Turn',
    adapter: 'cbc',
    alwaysThinkingEnabled: false,
    effort: '',
    workerStatus: 'idle',
    managedBy: 'ses_mgr_mock',
    lastResult: { status: 'done', taskId: null, taskSeq: 3, workerId: 'worker-mock', terminalKey: 'terminal:plain', resultCursor: 3 },
    historyTotal: 2,
    history: [
      { role: 'user', content: 'plain question', messageId: 'pan:p1', ts: ts(20) },
      answerRow('pan:p2', 'plain answer'),
    ],
    updatedAt: ts(2),
  },
  {
    id: 'ses_ta_busy',
    name: 'TA Busy',
    adapter: 'cbc',
    alwaysThinkingEnabled: false,
    effort: '',
    workerStatus: 'running',
    managedBy: 'ses_mgr_mock',
    lastResult: { status: 'done', taskId: 'task-2', taskSeq: 2, workerId: 'worker-mock', terminalKey: 'terminal:task-2', resultCursor: 2 },
    historyTotal: 4,
    history: [
      assignRow('pan:u1', 'task-1', 'do task 1'),
      answerRow('pan:a1', 'answer 1'),
      assignRow('pan:u2', 'task-2', 'do task 2'),
      answerRow('pan:a2', 'answer 2'),
    ],
    updatedAt: ts(3),
  },
  {
    id: 'ses_ta_solo',
    name: 'TA No Manager',
    adapter: 'cbc',
    alwaysThinkingEnabled: false,
    effort: '',
    workerStatus: 'idle',
    lastResult: { status: 'done', taskId: 'task-2', taskSeq: 2, workerId: 'worker-mock', terminalKey: 'terminal:task-2', resultCursor: 2 },
    historyTotal: 4,
    history: [
      assignRow('pan:u1', 'task-1', 'do task 1'),
      answerRow('pan:a1', 'answer 1'),
      assignRow('pan:u2', 'task-2', 'do task 2'),
      answerRow('pan:a2', 'answer 2'),
    ],
    updatedAt: ts(4),
  },
];

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
const appUrl = `${base}/?mock=1`;

const viteBin = path.join(webRoot, 'node_modules', 'vite', 'bin', 'vite.js');
const server = spawn(
  process.execPath,
  [viteBin, '--port', String(port), '--strictPort', '--host', '127.0.0.1'],
  { cwd: webRoot, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true },
);
let serverLog = '';
server.stdout.on('data', (chunk) => { serverLog += chunk; });
server.stderr.on('data', (chunk) => { serverLog += chunk; });

async function waitForServer() {
  for (let attempt = 0; attempt < 480; attempt += 1) {
    try {
      const res = await fetch(appUrl);
      if (res.ok) return;
    } catch {
      // retry
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`vite dev server did not become ready\n--- vite log ---\n${serverLog}`);
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

/** The mock short-circuits fetch in-page, so requests are observed through the
 *  mock's own attempt/delivery log rather than the network layer. */
const reportLog = (page) => page.evaluate(() => ({
  attempts: (window.__panMockManualReportLog?.attempts ?? []).map((attempt) => ({ ...attempt })),
  deliveries: (window.__panMockManualReportLog?.deliveries ?? []).map((delivery) => ({ ...delivery })),
}));

/** The seeded Session objects as the mock DB holds them (localStorage mirror). */
const seededSession = (page, sessionId) => page.evaluate((id) => {
  try {
    const raw = localStorage.getItem('pan:mockSessions');
    const sessions = raw ? JSON.parse(raw) : [];
    return sessions.find((session) => session.id === id) ?? null;
  } catch {
    return null;
  }
}, sessionId);

let browser;
try {
  await waitForServer();
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });

  async function openSeededPage() {
    const page = await context.newPage();
    const pageErrors = [];
    const protectedRequests = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('request', (request) => {
      if (request.url().includes(':8768') || request.url().includes(':8767')) {
        protectedRequests.push(`${request.method()} ${request.url()}`);
      }
    });
    await page.goto(appUrl, { waitUntil: 'domcontentloaded' });
    await page.evaluate((sessions) => {
      localStorage.setItem('pan:mockSessions', JSON.stringify(sessions));
    }, TA_SESSIONS);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
    return { page, pageErrors, protectedRequests };
  }

  async function selectSession(page, name) {
    await page.locator('[data-session-card-id]').filter({ hasText: name }).first().click();
  }

  const setHook = (page, mode) => page.evaluate((m) => {
    window.__panMockManualReport = m ? { mode: m } : undefined;
  }, mode);

  await check('visibility: one action on the latest task tail block only', async () => {
    const { page, pageErrors, protectedRequests } = await openSeededPage();
    try {
      await selectSession(page, 'TA Report Target');
      await page.locator(REPORT_BUTTON).first().waitFor({ state: 'visible' });
      const buttons = page.locator(REPORT_BUTTON);
      assert.equal(await buttons.count(), 1, 'exactly one tail block may carry the action');
      assert.equal(await buttons.first().getAttribute('data-manual-report'), 'ses_ta_report:pan:a2');
      // The bound row is the latest task's final answer, not the earlier one.
      const boundRow = await buttons.first().evaluate((node) => node.closest('.message-row-assistant')?.textContent ?? '');
      assert.match(boundRow, /answer 2/);
      assert.doesNotMatch(boundRow, /answer 1/);
      assert.deepEqual(protectedRequests, []);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('routing: click delivers the bound identity to the report route', async () => {
    const { page, pageErrors, protectedRequests } = await openSeededPage();
    try {
      await selectSession(page, 'TA Report Target');
      const button = page.locator(REPORT_BUTTON).first();
      await button.waitFor({ state: 'visible' });
      await button.click();
      await page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_report:pan:a2"]`).filter({ hasText: '已汇报' }).waitFor({ state: 'visible' });
      const { attempts, deliveries } = await reportLog(page);
      assert.equal(attempts.length, 1, `expected one attempt, got ${JSON.stringify(attempts)}`);
      assert.deepEqual(
        { sessionId: attempts[0].sessionId, taskId: attempts[0].taskId, messageId: attempts[0].messageId, outcome: attempts[0].outcome },
        { sessionId: 'ses_ta_report', taskId: 'task-2', messageId: 'pan:a2', outcome: 'delivered' },
      );
      assert.equal(deliveries.length, 1);
      assert.deepEqual(protectedRequests, []);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('unsubscribe: unsubscribed manager still receives the report', async () => {
    const { page, pageErrors, protectedRequests } = await openSeededPage();
    try {
      await selectSession(page, 'TA Report Target'); // seeded reportsToManager: false
      const button = page.locator(REPORT_BUTTON).first();
      await button.waitFor({ state: 'visible' });
      await button.click();
      await button.filter({ hasText: '已汇报' }).waitFor({ state: 'visible' });
      const { attempts, deliveries } = await reportLog(page);
      assert.equal(attempts.length, 1);
      assert.equal(attempts[0].outcome, 'delivered');
      assert.equal(deliveries.length, 1);
      // Nothing in this path may toggle the manager subscription.
      const session = await seededSession(page, 'ses_ta_report');
      assert.equal(session?.reportsToManager, false, 'manual report must not change the subscription');
      assert.deepEqual(protectedRequests, []);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('double click: one attempt and one delivery', async () => {
    const { page, pageErrors } = await openSeededPage();
    try {
      await selectSession(page, 'TA Report Target');
      const button = page.locator(REPORT_BUTTON).first();
      await button.waitFor({ state: 'visible' });
      await button.click({ clickCount: 2, delay: 30 });
      await button.filter({ hasText: '已汇报' }).waitFor({ state: 'visible' });
      await page.waitForTimeout(300);
      const { attempts, deliveries } = await reportLog(page);
      assert.equal(attempts.length, 1, `double click must issue one attempt, got ${attempts.length}`);
      assert.equal(deliveries.length, 1);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('definite failure: re-arms and a retry delivers once', async () => {
    const { page, pageErrors } = await openSeededPage();
    try {
      await selectSession(page, 'TA Report Target');
      await setHook(page, 'reject');
      const button = page.locator(REPORT_BUTTON).first();
      await button.waitFor({ state: 'visible' });
      await button.click();
      await button.filter({ hasText: '重试汇报' }).waitFor({ state: 'visible' });
      await setHook(page, null);
      await button.click();
      await button.filter({ hasText: '已汇报' }).waitFor({ state: 'visible' });
      const { attempts, deliveries } = await reportLog(page);
      assert.deepEqual(attempts.map((attempt) => attempt.outcome), ['rejected', 'delivered']);
      assert.equal(deliveries.length, 1, 'a rejected attempt must not deliver');
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('uncertain outcome: retry after a lost transport delivers once', async () => {
    const { page, pageErrors } = await openSeededPage();
    try {
      await selectSession(page, 'TA Report Target');
      await setHook(page, 'drop');
      const button = page.locator(REPORT_BUTTON).first();
      await button.waitFor({ state: 'visible' });
      await button.click();
      await button.filter({ hasText: '重试汇报' }).waitFor({ state: 'visible' });
      const title = await button.getAttribute('title');
      assert.match(title ?? '', /幂等/, 'uncertain state must explain the idempotent retry');
      await setHook(page, null);
      await button.click();
      await button.filter({ hasText: '已汇报' }).waitFor({ state: 'visible' });
      const { attempts, deliveries } = await reportLog(page);
      assert.deepEqual(attempts.map((attempt) => attempt.outcome), ['dropped', 'delivered']);
      assert.equal(deliveries.length, 1);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('sent-but-unconfirmed: retry is deduplicated, no double send', async () => {
    const { page, pageErrors } = await openSeededPage();
    try {
      await selectSession(page, 'TA Report Target');
      await setHook(page, 'lose_receipt');
      const button = page.locator(REPORT_BUTTON).first();
      await button.waitFor({ state: 'visible' });
      await button.click();
      await button.filter({ hasText: '重试汇报' }).waitFor({ state: 'visible' });
      await setHook(page, null);
      await button.click();
      await button.filter({ hasText: '已汇报' }).waitFor({ state: 'visible' });
      const { attempts, deliveries } = await reportLog(page);
      // Two attempts were issued, but the retry replayed the existing delivery:
      // exactly one report exists, never a second.
      assert.deepEqual(attempts.map((attempt) => attempt.outcome), ['dropped_after_delivery', 'replayed']);
      assert.equal(deliveries.length, 1, 'a retry after an unconfirmed send must not double-send');
      assert.equal(attempts[0].messageId, attempts[1].messageId);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('session switch: the action re-binds to the selected session', async () => {
    const { page, pageErrors } = await openSeededPage();
    try {
      await selectSession(page, 'TA Report Target');
      await page.locator(REPORT_BUTTON).first().waitFor({ state: 'visible' });
      await selectSession(page, 'TA Plain Turn');
      const plainButton = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_plain:pan:p2"]`);
      await plainButton.waitFor({ state: 'visible' });
      assert.equal(await page.locator(REPORT_BUTTON).count(), 1);
      await plainButton.click();
      await plainButton.filter({ hasText: '已汇报' }).waitFor({ state: 'visible' });
      const { attempts, deliveries } = await reportLog(page);
      assert.equal(attempts.length, 1);
      assert.deepEqual(
        { sessionId: attempts[0].sessionId, taskId: attempts[0].taskId, messageId: attempts[0].messageId },
        { sessionId: 'ses_ta_plain', taskId: null, messageId: 'pan:p2' },
      );
      assert.equal(deliveries.length, 1);
      // Back on the first session the earlier binding is still its own tail.
      await selectSession(page, 'TA Report Target');
      await page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_report:pan:a2"]`).first().waitFor({ state: 'visible' });
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('busy session and manager-less session hide the action', async () => {
    const { page, pageErrors } = await openSeededPage();
    try {
      await selectSession(page, 'TA Busy');
      await page.getByTestId('rich-text-composer').waitFor({ state: 'visible' });
      await page.waitForTimeout(300);
      assert.equal(await page.locator(REPORT_BUTTON).count(), 0, 'running worker must hide the action');
      await selectSession(page, 'TA No Manager');
      await page.getByTestId('rich-text-composer').waitFor({ state: 'visible' });
      await page.waitForTimeout(300);
      assert.equal(await page.locator(REPORT_BUTTON).count(), 0, 'no manager means no report target');
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });
} finally {
  if (browser) await browser.close();
  server.kill();
}

await fs.writeFile(path.join(runtime, 'results.json'), JSON.stringify({
  baseURL: appUrl,
  results,
}, null, 2));

const failed = results.filter((result) => !result.ok);
if (failed.length > 0) {
  console.error(`${failed.length} manual-report browser check(s) failed`);
  process.exit(1);
}
console.log(`All ${results.length} manual-report browser checks passed`);
