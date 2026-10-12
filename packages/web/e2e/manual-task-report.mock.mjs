/* global URL, console, document, process, window, fetch, setTimeout */
/**
 * Isolated-browser E2E for the explicit per-task manual report.
 *
 * Scope (all local, no real Pan service, no real manager/QQ Session):
 *   1. Visibility: one action per completed task, on that task's own tail text
 *      block — earlier tasks are NOT excluded; the newest running task adds no
 *      action and hides nothing.
 *   2. Routing: the click POSTs the bound (session, task, taskSeq, message)
 *      identity to /api/sessions/{id}/manual-report — the existing report route
 *      target — and each task is delivered as its own report.
 *   3. Cold load: the session list projection alone (terminal identities, no
 *      result bodies) is enough to bind actions for every indexed task.
 *   4. Tasks missing from the durable index get no action (no guessing).
 *   5. Unsubscribe: an unsubscribed manager still receives reports and the
 *      subscription is never toggled.
 *   6. Double click / concurrency: one attempt, one delivery.
 *   7. Definite failure: the action re-arms and a retry delivers once.
 *   8. Uncertain outcome (transport lost before send): retry delivers once.
 *   9. Sent-but-unconfirmed (transport lost after send): the retry is
 *      deduplicated by identity — still exactly one delivery for that task.
 *  10. Session switch re-binds the actions to the newly selected Session.
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
const runtime = process.env.PAN_MANUAL_REPORT_E2E_RUNTIME
  || path.join(webRoot, 'test-results', 'manual-task-report-browser');
await fs.mkdir(runtime, { recursive: true });

const REPORT_BUTTON = 'button[data-manual-report]';

// ── seeded mock sessions (localStorage `pan:mockSessions`) ──

const ts = (minutesAgo) => new Date(Date.now() - minutesAgo * 60_000).toISOString();

function assignRow(messageId, taskId) {
  return {
    role: 'user', content: `do ${taskId}`, messageId,
    source: 'agent', taskIdSource: 'assign', taskId, ts: ts(30),
  };
}

function answerRow(messageId, content) {
  return { role: 'assistant', content, messageId, ts: ts(29) };
}

/** Durable terminal identity row (no result body), as the summary projects it. */
function terminal(taskId, taskSeq) {
  return {
    taskId, taskSeq, resultCursor: taskSeq,
    terminalKey: `terminal:${taskId}`, status: 'done',
  };
}

const MULTI_TASKS = [
  assignRow('pan:u1', 'task-1'), answerRow('pan:a1', 'answer 1'),
  assignRow('pan:u2', 'task-2'), answerRow('pan:a2', 'answer 2'),
  assignRow('pan:u3', 'task-3'), answerRow('pan:a3', 'answer 3'),
];

// task-3 is running: it has a dispatch row but no settled answer and no
// terminal yet, so only the older tasks are actionable.
const OLDER_WHILE_RUNNING = [
  assignRow('pan:v1', 'task-1'), answerRow('pan:v1a', 'old answer 1'),
  assignRow('pan:v2', 'task-2'), answerRow('pan:v2a', 'old answer 2'),
  assignRow('pan:v3', 'task-3'),
];

const TA_SESSIONS = [
  {
    id: 'ses_ta_multi',
    name: 'TA Multi Task',
    adapter: 'cbc',
    alwaysThinkingEnabled: false,
    effort: '',
    workerStatus: 'idle',
    managedBy: 'ses_mgr_mock',
    reportsToManager: false, // unsubscribed: the actions must still work
    terminalResults: [terminal('task-1', 1), terminal('task-2', 2), terminal('task-3', 3)],
    historyTotal: MULTI_TASKS.length,
    history: MULTI_TASKS,
    updatedAt: ts(1),
  },
  {
    id: 'ses_ta_running',
    name: 'TA Older While Running',
    adapter: 'cbc',
    alwaysThinkingEnabled: false,
    effort: '',
    workerStatus: 'running',
    managedBy: 'ses_mgr_mock',
    terminalResults: [terminal('task-1', 1), terminal('task-2', 2)],
    historyTotal: OLDER_WHILE_RUNNING.length,
    history: OLDER_WHILE_RUNNING,
    updatedAt: ts(2),
  },
  {
    id: 'ses_ta_partial',
    name: 'TA Partial Index',
    adapter: 'cbc',
    alwaysThinkingEnabled: false,
    effort: '',
    workerStatus: 'idle',
    managedBy: 'ses_mgr_mock',
    // task-1's terminal is outside the index: no action may appear for it.
    terminalResults: [terminal('task-2', 2)],
    historyTotal: 4,
    history: [
      assignRow('pan:w1', 'task-1'), answerRow('pan:w1a', 'answer 1'),
      assignRow('pan:w2', 'task-2'), answerRow('pan:w2a', 'answer 2'),
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
    terminalResults: [terminal('task-1', 1), terminal('task-2', 2), terminal('task-3', 3)],
    historyTotal: MULTI_TASKS.length,
    history: MULTI_TASKS,
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

  const buttonKeys = (page) => page.$$eval(REPORT_BUTTON, (nodes) => nodes
    .map((node) => node.getAttribute('data-manual-report'))
    .sort());

  await check('visibility: one action per completed task, on its own tail block', async () => {
    const { page, pageErrors, protectedRequests } = await openSeededPage();
    try {
      await selectSession(page, 'TA Multi Task');
      await page.locator(REPORT_BUTTON).first().waitFor({ state: 'visible' });
      // Bound purely from the list projection (terminal identities) + history:
      // a cold load with no per-Session detail read.
      assert.deepEqual(await buttonKeys(page), [
        'ses_ta_multi:task-1:1:pan:a1',
        'ses_ta_multi:task-2:2:pan:a2',
        'ses_ta_multi:task-3:3:pan:a3',
      ]);
      // Each action sits on its own task's final answer row.
      for (const [key, answer] of [
        ['ses_ta_multi:task-1:1:pan:a1', 'answer 1'],
        ['ses_ta_multi:task-2:2:pan:a2', 'answer 2'],
        ['ses_ta_multi:task-3:3:pan:a3', 'answer 3'],
      ]) {
        const row = await page.locator(`${REPORT_BUTTON}[data-manual-report="${key}"]`)
          .evaluate((node) => node.closest('.message-row-assistant')?.textContent ?? '');
        assert.match(row, new RegExp(answer));
      }
      assert.deepEqual(protectedRequests, []);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('running newest task: older tasks stay actionable, newest adds none', async () => {
    const { page, pageErrors } = await openSeededPage();
    try {
      await selectSession(page, 'TA Older While Running');
      await page.locator(REPORT_BUTTON).first().waitFor({ state: 'visible' });
      assert.deepEqual(await buttonKeys(page), [
        'ses_ta_running:task-1:1:pan:v1a',
        'ses_ta_running:task-2:2:pan:v2a',
      ]);
      const { attempts } = await reportLog(page);
      assert.equal(attempts.length, 0, 'no report may be sent without a click');
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('routing: each task is delivered as its own report', async () => {
    const { page, pageErrors, protectedRequests } = await openSeededPage();
    try {
      await selectSession(page, 'TA Multi Task');
      const older = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-1:1:pan:a1"]`);
      await older.waitFor({ state: 'visible' });
      await older.click();
      await older.filter({ hasText: '已汇报' }).waitFor({ state: 'visible' });

      const newest = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-3:3:pan:a3"]`);
      await newest.click();
      await newest.filter({ hasText: '已汇报' }).waitFor({ state: 'visible' });

      const { attempts, deliveries } = await reportLog(page);
      assert.deepEqual(
        attempts.map((attempt) => [attempt.taskId, attempt.taskSeq, attempt.messageId, attempt.outcome]),
        [
          ['task-1', 1, 'pan:a1', 'delivered'],
          ['task-3', 3, 'pan:a3', 'delivered'],
        ],
      );
      assert.equal(deliveries.length, 2, 'each task keeps its own report');
      // The middle task stays untouched and actionable.
      assert.equal(await page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-2:2:pan:a2"]`).count(), 1);
      assert.deepEqual(protectedRequests, []);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('index gate: a task without a durable terminal gets no action', async () => {
    const { page, pageErrors } = await openSeededPage();
    try {
      await selectSession(page, 'TA Partial Index');
      await page.locator(REPORT_BUTTON).first().waitFor({ state: 'visible' });
      assert.deepEqual(await buttonKeys(page), ['ses_ta_partial:task-2:2:pan:w2a']);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('unsubscribe: unsubscribed manager still receives reports', async () => {
    const { page, pageErrors, protectedRequests } = await openSeededPage();
    try {
      await selectSession(page, 'TA Multi Task'); // seeded reportsToManager: false
      const button = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-2:2:pan:a2"]`);
      await button.waitFor({ state: 'visible' });
      await button.click();
      await button.filter({ hasText: '已汇报' }).waitFor({ state: 'visible' });
      const { attempts, deliveries } = await reportLog(page);
      assert.equal(attempts.length, 1);
      assert.equal(attempts[0].outcome, 'delivered');
      assert.equal(deliveries.length, 1);
      const session = await seededSession(page, 'ses_ta_multi');
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
      await selectSession(page, 'TA Multi Task');
      const button = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-1:1:pan:a1"]`);
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
      await selectSession(page, 'TA Multi Task');
      await setHook(page, 'reject');
      const button = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-2:2:pan:a2"]`);
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
      await selectSession(page, 'TA Multi Task');
      await setHook(page, 'drop');
      const button = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-3:3:pan:a3"]`);
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
      await selectSession(page, 'TA Multi Task');
      await setHook(page, 'lose_receipt');
      const button = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-1:1:pan:a1"]`);
      await button.waitFor({ state: 'visible' });
      await button.click();
      await button.filter({ hasText: '重试汇报' }).waitFor({ state: 'visible' });
      await setHook(page, null);
      await button.click();
      await button.filter({ hasText: '已汇报' }).waitFor({ state: 'visible' });
      const { attempts, deliveries } = await reportLog(page);
      // Two attempts, but the retry replayed the existing delivery: exactly one
      // report exists for that task, never a second.
      assert.deepEqual(attempts.map((attempt) => attempt.outcome), ['dropped_after_delivery', 'replayed']);
      assert.equal(deliveries.length, 1, 'a retry after an unconfirmed send must not double-send');
      assert.equal(attempts[0].messageId, attempts[1].messageId);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('session switch: actions re-bind to the selected session', async () => {
    const { page, pageErrors } = await openSeededPage();
    try {
      await selectSession(page, 'TA Multi Task');
      await page.locator(REPORT_BUTTON).first().waitFor({ state: 'visible' });
      assert.equal((await buttonKeys(page)).length, 3);

      await selectSession(page, 'TA Partial Index');
      await page.locator(REPORT_BUTTON).first().waitFor({ state: 'visible' });
      assert.deepEqual(await buttonKeys(page), ['ses_ta_partial:task-2:2:pan:w2a']);

      // Back on the first session every task's action is still its own.
      await selectSession(page, 'TA Multi Task');
      await page.locator(REPORT_BUTTON).first().waitFor({ state: 'visible' });
      assert.deepEqual(await buttonKeys(page), [
        'ses_ta_multi:task-1:1:pan:a1',
        'ses_ta_multi:task-2:2:pan:a2',
        'ses_ta_multi:task-3:3:pan:a3',
      ]);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('manager-less session hides every action', async () => {
    const { page, pageErrors } = await openSeededPage();
    try {
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
