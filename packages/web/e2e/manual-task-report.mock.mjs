/* global console, process, window, fetch, setTimeout, localStorage */
/**
 * Isolated-browser E2E for the explicit manual report (any completed run).
 *
 * The report entry lives in the shared block menu, opened from a row's left
 * gutter (`button[data-message-bookmark-gutter]` → `[data-message-block-menu]`
 * → `[data-manual-report]`).  All interactions below go through that path.
 *
 * Scope (all local, no real Pan service, no real manager/QQ Session):
 *   1. Visibility: one action per completed run, on that run's own tail text
 *      block — earlier runs are NOT excluded; the newest running run adds no
 *      action and hides nothing.  Plain conversation runs (taskId null) are
 *      included when their durable terminal carries taskSeq/resultCursor.
 *   2. Routing: the click POSTs the bound run identity (session, taskId|null,
 *      taskSeq, resultCursor, messageId) to /api/sessions/{id}/manual-report —
 *      each run is delivered as its own report, and a plain run routes
 *      `taskId: null` plus its durable sequence.
 *   3. Cold load: the session list projection alone (terminal identities, no
 *      result bodies) is enough to bind actions for every indexed run.
 *   4. Runs missing from the durable index get no action (no guessing); two
 *      runs of one taskId, and a window holding only one of several runs, stay
 *      unbound instead of being reported as the newest run.  A plain run whose
 *      terminal carries no durable identity gets no action either.
 *   5. Unsubscribe: an unsubscribed manager still receives reports and the
 *      subscription is never toggled.
 *   6. Double click / concurrency: one attempt, one delivery.
 *   7. Definite failure: the action re-arms and a retry delivers once.
 *   8. Uncertain outcome (transport lost before send): retry delivers once.
 *   9. Sent-but-unconfirmed (transport lost after send): the retry is
 *      deduplicated by identity — still exactly one delivery for that run.
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

const GUTTER = 'button[data-message-bookmark-gutter]';
const MENU = '[data-message-block-menu]';
const REPORT_ITEM = `${MENU} [data-manual-report]`;

// ── seeded mock sessions (localStorage `pan:mockSessions`) ──

const ts = (minutesAgo) => new Date(Date.now() - minutesAgo * 60_000).toISOString();

function assignRow(messageId, taskId) {
  return {
    role: 'user', content: `do ${taskId}`, messageId,
    source: 'agent', taskIdSource: 'assign', taskId, ts: ts(30),
  };
}

/** A browser-typed user row: opens a plain-conversation run segment. */
function plainUserRow(messageId, content) {
  return { role: 'user', content, messageId, ts: ts(30) };
}

function answerRow(messageId, content) {
  return { role: 'assistant', content, messageId, ts: ts(29) };
}

/** One fixed history epoch for all seeded Sessions (append-only model). */
const EPOCH = 'mock-history-epoch';

/**
 * Build a Session whose seeded history and durable terminals really correspond:
 * each run appends its opening row + final answer, and its terminal records the
 * history revision AFTER that final append (the production ordering), so the
 * run's tail block is provable with the same arithmetic the server uses
 * (covered end = total - (revision - terminalRevision); tail = end - 1).
 * `plain: true` seeds a plain conversation run (`taskId: null`, browser user
 * row) instead of a formal assign.  `truncateAfter` serves only a truncated
 * history window while keeping the full history revision/total.
 */
function buildSession({ id, name, runs, updatedAt, workerStatus = 'idle', reportsToManager = true, managedBy = 'ses_mgr_mock', truncateAfter }) {
  const history = [];
  const terminalResults = [];
  for (const run of runs) {
    history.push(run.plain
      ? plainUserRow(run.assignId, run.content ?? 'typed by the user')
      : assignRow(run.assignId, run.taskId));
    if (run.answer !== undefined) history.push(answerRow(run.answerId, run.answer));
    if (!run.terminal) continue;
    terminalResults.push({
      taskId: run.plain ? null : run.taskId,
      taskSeq: run.taskSeq,
      // Legacy rows without a durable cursor omit it; the mock (like the
      // server) then resolves a cursor-only report through taskSeq equality.
      ...(run.noCursor ? {} : { resultCursor: run.resultCursor ?? run.taskSeq }),
      terminalKey: `terminal:${run.plain ? 'plain' : run.taskId}`,
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
  buildSession({
    // Plain conversation runs: one with both durable identities, one whose
    // terminal only carries a resultCursor (legacy), one whose terminal has no
    // durable identity at all (must stay unbound), plus a formal task run to
    // prove plain and task runs bind side by side.
    id: 'ses_ta_plain', name: 'TA Plain Chats', updatedAt: ts(7),
    runs: [
      { plain: true, content: '普通提问一', assignId: 'pan:p1', taskSeq: 1, answer: '普通回答一', answerId: 'pan:p1a', terminal: true },
      { plain: true, content: '普通提问二', assignId: 'pan:p2', taskSeq: 2, noCursor: true, answer: '普通回答二', answerId: 'pan:p2a', terminal: true },
      { plain: true, content: '普通提问三', assignId: 'pan:p3', taskSeq: null, answer: '普通回答三', answerId: 'pan:p3a', terminal: true },
      { taskId: 'task-p', taskSeq: 9, assignId: 'pan:p4', answer: 'task answer', answerId: 'pan:p4a', terminal: true },
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

  /** Open each row's block menu in DOM order and collect the report binding
   *  it offers (empty for rows without one). */
  const buttonKeys = async (page) => {
    const keys = [];
    // Cold loads bind from the (async) session list: settle on the first
    // gutter before scanning, so an empty scan never masquerades as "no
    // bindings".
    await page.locator(GUTTER).first().waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
    for (const gutter of await page.locator(GUTTER).all()) {
      await gutter.click();
      const item = page.locator(REPORT_ITEM);
      if (await item.count()) keys.push(await item.first().getAttribute('data-manual-report'));
      await page.keyboard.press('Escape');
    }
    return keys.sort();
  };

  /** Open block menus in row order until one offers `key`, leaving it open.
   *  Returns the bound row's text so callers can prove block↔run pairing. */
  const openReportMenu = async (page, key) => {
    await page.locator(GUTTER).first().waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
    for (const gutter of await page.locator(GUTTER).all()) {
      await gutter.click();
      // The menu renders synchronously on click, but a preceding report may
      // still be flushing its state; settle on the menu element first.
      await page.waitForSelector(MENU, { state: 'visible', timeout: 3000 }).catch(() => {});
      const item = page.locator(`${REPORT_ITEM}[data-manual-report="${key}"]`);
      if (await item.count()) {
        const rowText = await gutter.evaluate((node) => node.closest('.message-row-assistant')?.textContent ?? '');
        return { item: item.first(), rowText };
      }
      await page.keyboard.press('Escape');
    }
    throw new Error(`no report entry for ${key}`);
  };

  /** Activate the report entry for `key` through the real user path. */
  const clickReport = async (page, key) => {
    const { item } = await openReportMenu(page, key);
    await item.click();
  };

  /** Re-open the menu for `key` and read the entry's current state. */
  const reportState = async (page, key) => {
    const { item } = await openReportMenu(page, key);
    const state = { text: (await item.textContent()) ?? '', disabled: await item.isDisabled(), title: await item.getAttribute('title') };
    await page.keyboard.press('Escape');
    return state;
  };

  const waitForReportState = async (page, key, pattern) => {
    let lastError = '';
    for (let attempt = 0; attempt < 60; attempt += 1) {
      try {
        const state = await reportState(page, key);
        if (pattern.test(state.text)) return state;
        lastError = `state=${state.text}`;
      } catch (error) {
        // A transient re-render can hide the entry for one probe; retry.
        lastError = String(error?.message || error);
        await page.keyboard.press('Escape').catch(() => {});
      }
      await page.waitForTimeout(100);
    }
    throw new Error(`report entry ${key} never reached ${pattern} (last: ${lastError})`);
  };

  await check('visibility: one action per completed task, on its own tail block', async () => {
    const { page, pageErrors, protectedRequests } = await openSeededPage();
    try {
      await selectSession(page, 'TA Multi Task');
      await page.locator(GUTTER).first().waitFor({ state: 'visible' });
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
        const { rowText } = await openReportMenu(page, key);
        assert.match(rowText, new RegExp(answer));
        await page.keyboard.press('Escape');
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
      await page.locator(GUTTER).first().waitFor({ state: 'visible' });
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

  await check('plain runs: a taskId-null run binds and routes with its durable sequence', async () => {
    const { page, pageErrors, protectedRequests } = await openSeededPage();
    try {
      await selectSession(page, 'TA Plain Chats');
      await page.locator(GUTTER).first().waitFor({ state: 'visible' });
      // The two durable plain runs and the task run bind; the identity-less
      // plain run (no taskSeq, no resultCursor) offers no action at all.
      assert.deepEqual(await buttonKeys(page), [
        'ses_ta_plain:-:1',
        'ses_ta_plain:-:2',
        'ses_ta_plain:task-p:9',
      ]);
      const { rowText } = await openReportMenu(page, 'ses_ta_plain:-:1');
      assert.match(rowText, /普通回答一/);
      await page.keyboard.press('Escape');

      await clickReport(page, 'ses_ta_plain:-:1');
      await waitForReportState(page, 'ses_ta_plain:-:1', /已汇报/);
      const { attempts, deliveries } = await reportLog(page);
      assert.deepEqual(
        attempts.map((attempt) => [attempt.taskId, attempt.taskSeq, attempt.resultCursor, attempt.messageId, attempt.outcome]),
        [[null, 1, 1, 'pan:p1a', 'delivered']],
      );
      assert.equal(deliveries.length, 1);
      // The legacy row without a durable cursor still routes (cursor derived
      // from its taskSeq) and the button text stays plain.
      await clickReport(page, 'ses_ta_plain:-:2');
      await waitForReportState(page, 'ses_ta_plain:-:2', /已汇报/);
      const after = await reportLog(page);
      const legacyAttempt = after.attempts.at(-1);
      assert.equal(legacyAttempt.taskId, null);
      assert.equal(legacyAttempt.taskSeq, 2);
      assert.equal(legacyAttempt.resultCursor, null, 'a legacy row without a cursor sends none');
      assert.equal(legacyAttempt.messageId, 'pan:p2a');
      assert.equal(legacyAttempt.outcome, 'delivered');
      assert.deepEqual(protectedRequests, []);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('routing: each task is delivered as its own report', async () => {
    const { page, pageErrors, protectedRequests } = await openSeededPage();
    try {
      await selectSession(page, 'TA Multi Task');
      await clickReport(page, 'ses_ta_multi:task-1:1');
      await waitForReportState(page, 'ses_ta_multi:task-1:1', /已汇报/);
      await clickReport(page, 'ses_ta_multi:task-3:3');
      await waitForReportState(page, 'ses_ta_multi:task-3:3', /已汇报/);

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
      const middle = await reportState(page, 'ses_ta_multi:task-2:2');
      assert.match(middle.text, /手动汇报/);
      assert.equal(middle.disabled, false);
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
      await page.locator(GUTTER).first().waitFor({ state: 'visible' });
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
      await page.locator(GUTTER).first().waitFor({ state: 'visible' });
      // Canonical offsets prove each block is its own run's final row, so both
      // actions appear and neither can report the other run's result.
      assert.deepEqual(await buttonKeys(page), ['ses_ta_rerun:task-r:1', 'ses_ta_rerun:task-r:2']);
      // The older block submitted for the newer run is refused server-side.
      await setHook(page, 'lose_receipt');
      await clickReport(page, 'ses_ta_rerun:task-r:1');
      await waitForReportState(page, 'ses_ta_rerun:task-r:1', /重试汇报/);
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
      await page.locator(GUTTER).first().waitFor({ state: 'visible' });
      // Only the older run's boundary is recoverable from this window, so its
      // own block is actionable and the newer run's block (outside the window)
      // is not.
      assert.deepEqual(await buttonKeys(page), ['ses_ta_window:task-w:1']);
      assert.deepEqual(pageErrors, []);
    } finally {
      await page.close();
    }
  });

  await check('unsubscribe: unsubscribed manager still receives reports', async () => {
    const { page, pageErrors, protectedRequests } = await openSeededPage();
    try {
      await selectSession(page, 'TA Multi Task'); // seeded reportsToManager: false
      await clickReport(page, 'ses_ta_multi:task-2:2');
      await waitForReportState(page, 'ses_ta_multi:task-2:2', /已汇报/);
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
      // The user clicks the entry, then opens the menu again and clicks it a
      // second time as fast as possible: the single-flight/sent guard must
      // keep this to one attempt and one delivery.
      await clickReport(page, 'ses_ta_multi:task-1:1');
      const { item } = await openReportMenu(page, 'ses_ta_multi:task-1:1');
      if (!(await item.isDisabled())) await item.click();
      await page.keyboard.press('Escape');
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
      await clickReport(page, 'ses_ta_multi:task-2:2');
      await waitForReportState(page, 'ses_ta_multi:task-2:2', /重试汇报/);
      await setHook(page, null);
      await clickReport(page, 'ses_ta_multi:task-2:2');
      await waitForReportState(page, 'ses_ta_multi:task-2:2', /已汇报/);
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
      await clickReport(page, 'ses_ta_multi:task-3:3');
      const retryState = await waitForReportState(page, 'ses_ta_multi:task-3:3', /重试汇报/);
      assert.match(retryState.title ?? '', /幂等/, 'uncertain state must explain the idempotent retry');
      await setHook(page, null);
      await clickReport(page, 'ses_ta_multi:task-3:3');
      await waitForReportState(page, 'ses_ta_multi:task-3:3', /已汇报/);
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
      await clickReport(page, 'ses_ta_multi:task-1:1');
      await waitForReportState(page, 'ses_ta_multi:task-1:1', /重试汇报/);
      await setHook(page, null);
      await clickReport(page, 'ses_ta_multi:task-1:1');
      await waitForReportState(page, 'ses_ta_multi:task-1:1', /已汇报/);
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
      await page.locator(GUTTER).first().waitFor({ state: 'visible' });
      assert.equal((await buttonKeys(page)).length, 3);

      await selectSession(page, 'TA Partial Index');
      await page.locator(GUTTER).first().waitFor({ state: 'visible' });
      assert.deepEqual(await buttonKeys(page), ['ses_ta_partial:task-2:2']);

      // Back on the first session every task's action is still its own.
      await selectSession(page, 'TA Multi Task');
      await page.locator(GUTTER).first().waitFor({ state: 'visible' });
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
      assert.equal(await page.locator(REPORT_ITEM).count(), 0, 'no manager means no report target');
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
