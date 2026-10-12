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
 *   4. Tasks missing from the durable index get no action (no guessing); two
 *      runs of one taskId, and a window holding only one of several runs, stay
 *      unbound instead of being reported as the newest run.
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

/** One fixed history epoch for all seeded Sessions (append-only model). */
const EPOCH = 'mock-history-epoch';

/**
 * Build a Session whose seeded history and durable terminals really correspond:
 * each run appends its dispatch row + final answer, and its terminal records the
 * history revision AFTER that final append (the production ordering), so the
 * run's tail block is provable with the same arithmetic the server uses
 * (covered end = total - (revision - terminalRevision); tail = end - 1).
 * `truncateAfter` serves only a truncated history window while keeping the
 * full history revision/total, modelling a client that has not loaded the
 * newer rows yet.
 */
function buildSession({ id, name, runs, updatedAt, workerStatus = 'idle', reportsToManager = true, managedBy = 'ses_mgr_mock', truncateAfter }) {
  const history = [];
  const terminalResults = [];
  for (const run of runs) {
    history.push(assignRow(run.assignId, run.taskId));
    if (run.answer !== undefined) history.push(answerRow(run.answerId, run.answer));
    if (!run.terminal) continue;
    terminalResults.push({
      taskId: run.taskId,
      taskSeq: run.taskSeq,
      resultCursor: run.taskSeq,
      terminalKey: `terminal:${run.taskId}`,
      status: 'done',
      historyEpoch: EPOCH,
      // Recorded after the run's final row was appended, exactly like
      // _persist_terminal_state: the revision counts the rows this run covered.
      historyRevision: history.length,
    });
  }
  return {
    id,
    name,
    adapter: 'cbc',
    alwaysThinkingEnabled: false,
    effort: '',
    workerStatus,
    ...(managedBy ? { managedBy } : {}),
    reportsToManager,
    historyEpoch: EPOCH,
    historyRevision: history.length,
    terminalResults,
    historyTotal: history.length,
    history: typeof truncateAfter === 'number' ? history.slice(0, truncateAfter) : history,
    updatedAt,
  };
}

const TA_SESSIONS = [
  buildSession({
    id: 'ses_ta_multi', name: 'TA Multi Task', updatedAt: ts(1),
    reportsToManager: false, // unsubscribed: the actions must still work
    runs: [
      { taskId: 'task-1', taskSeq: 1, assignId: 'pan:u1', answer: 'answer 1', answerId: 'pan:a1', terminal: true },
      { taskId: 'task-2', taskSeq: 2, assignId: 'pan:u2', answer: 'answer 2', answerId: 'pan:a2', terminal: true },
      { taskId: 'task-3', taskSeq: 3, assignId: 'pan:u3', answer: 'answer 3', answerId: 'pan:a3', terminal: true },
    ],
  }),
  buildSession({
    id: 'ses_ta_running', name: 'TA Older While Running', updatedAt: ts(2), workerStatus: 'running',
    runs: [
      { taskId: 'task-1', taskSeq: 1, assignId: 'pan:v1', answer: 'old answer 1', answerId: 'pan:v1a', terminal: true },
      { taskId: 'task-2', taskSeq: 2, assignId: 'pan:v2', answer: 'old answer 2', answerId: 'pan:v2a', terminal: true },
      // Running: dispatched, no settled answer and no terminal yet.
      { taskId: 'task-3', taskSeq: 3, assignId: 'pan:v3' },
    ],
  }),
  buildSession({
    id: 'ses_ta_partial', name: 'TA Partial Index', updatedAt: ts(3),
    runs: [
      // No terminal recorded for task-1: it must get no action.
      { taskId: 'task-1', taskSeq: 1, assignId: 'pan:w1', answer: 'answer 1', answerId: 'pan:w1a' },
      { taskId: 'task-2', taskSeq: 2, assignId: 'pan:w2', answer: 'answer 2', answerId: 'pan:w2a', terminal: true },
    ],
  }),
  buildSession({
    // Same taskId dispatched twice: two candidate blocks, two runs — neither may
    // be bound to the other's result, so no action appears at all.
    id: 'ses_ta_rerun', name: 'TA Rerun Same Task', updatedAt: ts(5),
    runs: [
      { taskId: 'task-r', taskSeq: 1, assignId: 'pan:x1', answer: 'first run', answerId: 'pan:x1a', terminal: true },
      { taskId: 'task-r', taskSeq: 2, assignId: 'pan:x2', answer: 'second run', answerId: 'pan:x2a', terminal: true },
    ],
  }),
  buildSession({
    // The client window only holds the older run's rows while the full history
    // (and the newer run's terminal boundary) is longer: the older block must
    // bind to its own run and never to the newer one.
    id: 'ses_ta_window', name: 'TA Window Old Only', updatedAt: ts(6),
    truncateAfter: 2,
    runs: [
      { taskId: 'task-w', taskSeq: 1, assignId: 'pan:y1', answer: 'old run answer', answerId: 'pan:y1a', terminal: true },
      { taskId: 'task-w', taskSeq: 2, assignId: 'pan:y2', answer: 'newer run answer', answerId: 'pan:y2a', terminal: true },
    ],
  }),
  buildSession({
    id: 'ses_ta_solo', name: 'TA No Manager', updatedAt: ts(4), managedBy: null,
    runs: [
      { taskId: 'task-1', taskSeq: 1, assignId: 'pan:u1', answer: 'answer 1', answerId: 'pan:a1', terminal: true },
      { taskId: 'task-2', taskSeq: 2, assignId: 'pan:u2', answer: 'answer 2', answerId: 'pan:a2', terminal: true },
      { taskId: 'task-3', taskSeq: 3, assignId: 'pan:u3', answer: 'answer 3', answerId: 'pan:a3', terminal: true },
    ],
  }),
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
        'ses_ta_multi:task-1:1',
        'ses_ta_multi:task-2:2',
        'ses_ta_multi:task-3:3',
      ]);
      // Each action sits on its own task's final answer row.
      for (const [key, answer] of [
        ['ses_ta_multi:task-1:1', 'answer 1'],
        ['ses_ta_multi:task-2:2', 'answer 2'],
        ['ses_ta_multi:task-3:3', 'answer 3'],
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
        'ses_ta_running:task-1:1',
        'ses_ta_running:task-2:2',
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
      const older = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-1:1"]`);
      await older.waitFor({ state: 'visible' });
      await older.click();
      await older.filter({ hasText: '已汇报' }).waitFor({ state: 'visible' });

      const newest = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-3:3"]`);
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
      assert.equal(await page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-2:2"]`).count(), 1);
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
      assert.deepEqual(await buttonKeys(page), ['ses_ta_partial:task-2:2']);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('same taskId twice: each block binds its own run', async () => {
    const { page, pageErrors } = await openSeededPage();
    try {
      await selectSession(page, 'TA Rerun Same Task');
      await page.locator(REPORT_BUTTON).first().waitFor({ state: 'visible' });
      // Canonical offsets prove each block is its own run's final row, so both
      // actions appear and neither can report the other run's result.
      const keys = await page.$$eval(REPORT_BUTTON, (nodes) => nodes
        .map((node) => node.getAttribute('data-manual-report')).sort());
      assert.deepEqual(keys, ['ses_ta_rerun:task-r:1', 'ses_ta_rerun:task-r:2']);
      // The older block submitted for the newer run is refused server-side.
      const older = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_rerun:task-r:1"]`);
      await setHook(page, 'lose_receipt');
      await older.click();
      await older.filter({ hasText: '重试汇报' }).waitFor({ state: 'visible' });
      const { attempts } = await reportLog(page);
      assert.equal(attempts.length, 1);
      assert.equal(attempts[0].taskId, 'task-r');
      assert.equal(attempts[0].taskSeq, 1);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('truncated window: the older run binds itself, never the newer one', async () => {
    const { page, pageErrors } = await openSeededPage();
    try {
      await selectSession(page, 'TA Window Old Only');
      await page.locator(REPORT_BUTTON).first().waitFor({ state: 'visible' });
      // Only the older run's boundary is recoverable from this window, so its
      // own block is actionable and the newer run's block (outside the window)
      // is not.
      const keys = await page.$$eval(REPORT_BUTTON, (nodes) => nodes
        .map((node) => node.getAttribute('data-manual-report')).sort());
      assert.deepEqual(keys, ['ses_ta_window:task-w:1']);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('unsubscribe: unsubscribed manager still receives reports', async () => {
    const { page, pageErrors, protectedRequests } = await openSeededPage();
    try {
      await selectSession(page, 'TA Multi Task'); // seeded reportsToManager: false
      const button = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-2:2"]`);
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
      const button = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-1:1"]`);
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
      const button = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-2:2"]`);
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
      const button = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-3:3"]`);
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
      const button = page.locator(`${REPORT_BUTTON}[data-manual-report="ses_ta_multi:task-1:1"]`);
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
      assert.deepEqual(await buttonKeys(page), ['ses_ta_partial:task-2:2']);

      // Back on the first session every task's action is still its own.
      await selectSession(page, 'TA Multi Task');
      await page.locator(REPORT_BUTTON).first().waitFor({ state: 'visible' });
      assert.deepEqual(await buttonKeys(page), [
        'ses_ta_multi:task-1:1',
        'ses_ta_multi:task-2:2',
        'ses_ta_multi:task-3:3',
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
