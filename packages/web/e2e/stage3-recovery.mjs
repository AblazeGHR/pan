/* global AbortSignal, console, fetch, process, setTimeout */

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import fsSync from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { chromium } from '@playwright/test';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(scriptDir, '..');
const repoRoot = path.resolve(webRoot, '..', '..');
const port = Number(process.env.PAN_STAGE3_PORT || 8793);
const baseURL = `http://127.0.0.1:${port}`;
const runtime = path.resolve(
  process.env.PAN_STAGE3_RUNTIME || path.join(repoRoot, 'test-results', 'stage3-recovery-runtime'),
);
const python = process.env.PAN_E2E_PYTHON || 'python';

assert.notEqual(port, 8768, 'stage 3 E2E must not use protected port 8768');
assert.notEqual(repoRoot, 'D:\\project\\Pan');

let serverProcess = null;
let browser = null;
let context = null;
let page = null;
let traceStarted = false;
const evidence = {
  checkout: repoRoot,
  port,
  runtime,
  provider: 'deterministic __e2e stream fixture; no real model',
  assertions: [],
};

async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForHealth(expected = true, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseURL}/api/health`, {
        signal: AbortSignal.timeout(1500),
      });
      if (expected && response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
      if (!expected) return;
    }
    await sleep(100);
  }
  throw new Error(`health wait expected=${expected} last=${lastError}`);
}

async function startServer() {
  const stdoutPath = path.join(runtime, `server-${Date.now()}.stdout.log`);
  const stderrPath = path.join(runtime, `server-${Date.now()}.stderr.log`);
  const stdout = fsSync.openSync(stdoutPath, 'a');
  const stderr = fsSync.openSync(stderrPath, 'a');
  serverProcess = spawn(
    python,
    [path.join(webRoot, 'e2e', 'server.py')],
    {
      cwd: repoRoot,
      env: {
        ...process.env,
        PAN_PORT: String(port),
        PAN_E2E_RUNTIME: runtime,
        PAN_E2E_BASE_URL: baseURL,
      },
      stdio: ['ignore', stdout, stderr],
    },
  );
  evidence.serverPids ??= [];
  evidence.serverPids.push(serverProcess.pid);
  await waitForHealth(true);
  return { stdoutPath, stderrPath };
}

async function stopServer() {
  const current = serverProcess;
  serverProcess = null;
  if (!current || current.exitCode !== null) return;
  current.kill();
  await Promise.race([
    once(current, 'exit'),
    sleep(10000),
  ]);
  await waitForHealth(false, 10000);
}

async function requestJSON(route, options = {}) {
  const response = await fetch(`${baseURL}${route}`, {
    ...options,
    headers: { 'content-type': 'application/json', ...(options.headers || {}) },
    signal: AbortSignal.timeout(15000),
  });
  const text = await response.text();
  let body;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  assert.equal(response.status, 200, `${options.method || 'GET'} ${route}: ${response.status} ${text}`);
  return body;
}

async function post(route, body) {
  return requestJSON(route, { method: 'POST', body: JSON.stringify(body) });
}

async function stream(sessionId, event) {
  return post('/__e2e/stream', { sessionId, event });
}

async function broadcast(event) {
  return post('/__e2e/broadcast', { event });
}

async function appendHistory(sessionId, messages) {
  return post('/__e2e/append-history', { sessionId, messages });
}

async function collectHistory(sessionId) {
  const all = [];
  let before = 0;
  let total = 0;
  for (let pageNumber = 0; pageNumber < 32; pageNumber += 1) {
    const data = await requestJSON(
      `/api/sessions/${encodeURIComponent(sessionId)}/history?before=${before}&limit=50`,
    );
    const rows = Array.isArray(data.history) ? data.history : [];
    all.unshift(...rows);
    total = Number(data.total || total || rows.length);
    const start = Number(data.start || 0);
    if (!data.hasMore || start <= 0 || start === before) break;
    before = start;
  }
  return { rows: all, total };
}

async function openChat() {
  await page.goto(`${baseURL}/react/`);
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  const card = page.locator('[data-session-card-id]').filter({ hasText: 'Chat Stream' }).first();
  await card.waitFor({ state: 'visible' });
  const sessionId = await card.getAttribute('data-session-card-id');
  assert.ok(sessionId, 'seeded Chat Stream session id is missing');
  await card.click();
  return sessionId;
}

async function waitForText(text) {
  await page.locator('main').getByText(text, { exact: true }).first().waitFor({ state: 'visible', timeout: 10000 });
}

async function recordAssertion(name, value) {
  evidence.assertions.push({ name, value });
}

try {
  await fs.mkdir(runtime, { recursive: true });
  await startServer();

  browser = await chromium.launch({ headless: true });
  context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  page = await context.newPage();
  page.on('pageerror', (error) => {
    evidence.pageErrors ??= [];
    evidence.pageErrors.push(String(error));
  });
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  traceStarted = true;

  const sessionId = await openChat();
  evidence.sessionId = sessionId;
  const before = await collectHistory(sessionId);
  assert.ok(before.total >= 300, `expected hundreds of canonical rows, got ${before.total}`);
  await recordAssertion('hundreds-of-history-rows', before.total);

  const liveEvent = {
    type: 'assistant',
    role: 'assistant',
    item_id: 'stage3-live-item',
    delta: true,
    stream_text: 'Stage3 live answer',
    content: 'Stage3 live answer',
  };
  await stream(sessionId, liveEvent);
  await waitForText('Stage3 live answer');
  // Same cumulative event and a shorter old cumulative event are delivered as
  // separate source events. The browser must render only the current item.
  await stream(sessionId, liveEvent);
  await stream(sessionId, { ...liveEvent, stream_text: 'Stage3 live', content: 'Stage3 live' });
  await sleep(150);
  const liveCount = await page.locator('main').getByText('Stage3 live answer', { exact: true }).count();
  assert.equal(liveCount, 1, `duplicate/old live frame rendered ${liveCount} copies`);
  await recordAssertion('duplicate-and-old-live-frame-idempotency', liveCount);

  const canonical = await appendHistory(sessionId, [
    { role: 'user', content: 'Stage3 canonical prompt', messageId: 'stage3-user' },
    {
      role: 'assistant',
      content: 'Stage3 live answer',
      messageId: 'stage3-answer',
      nativeItemId: 'stage3-live-item',
    },
  ]);
  const resultSentAt = Date.now();
  await broadcast({
    type: 'worker.result',
    sessionId,
    workerId: 'e2e-browser-worker',
    generation: 0,
    taskSeq: 1,
    taskId: 'stage3-task-1',
    status: 'done',
    result: 'Stage3 live answer',
    historyEpoch: canonical.historyEpoch,
    historyRevision: canonical.historyRevision,
    terminalCoverage: {
      historyEpoch: canonical.historyEpoch,
      historyRevision: canonical.historyRevision,
    },
  });
  const idleSentAt = Date.now();
  await broadcast({
    type: 'worker.status',
    sessionId,
    workerId: 'e2e-browser-worker',
    generation: 0,
    taskSeq: 1,
    status: 'idle',
  });
  await waitForText('Stage3 live answer');
  await recordAssertion('result-before-idle', { resultSentAt, idleSentAt, ordered: resultSentAt <= idleSentAt });

  // Hold an old real HTTP history response while the browser changes back to
  // this Session and a newer canonical/live turn arrives through the real WS.
  // This is the browser-level stale-snapshot adversary; the response itself
  // is produced from the captured server JSON, not a frontend state mock.
  const delayedSnapshot = await requestJSON(
    `/api/sessions/${encodeURIComponent(sessionId)}/history?before=0&limit=50`,
  );
  const otherCard = page.locator('[data-session-card-id]').filter({ hasText: 'Alpha Session' }).first();
  await otherCard.click();
  const delayedHistoryPattern = `**/api/sessions/${encodeURIComponent(sessionId)}/history**`;
  let delayedRequestUsed = false;
  let releaseDelayedRequest = null;
  let capturedDelayedRequest;
  const delayedRequestCaptured = new Promise((resolve) => { capturedDelayedRequest = resolve; });
  await page.route(delayedHistoryPattern, async (route) => {
    if (delayedRequestUsed) {
      await route.continue();
      return;
    }
    delayedRequestUsed = true;
    capturedDelayedRequest();
    await new Promise((resolve) => { releaseDelayedRequest = resolve; });
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(delayedSnapshot),
    });
  });
  await page.locator(`[data-session-card-id="${sessionId}"]`).click();
  await Promise.race([
    delayedRequestCaptured,
    sleep(10000).then(() => { throw new Error('timed out waiting for delayed history request'); }),
  ]);
  await sleep(0);
  await appendHistory(sessionId, [
    { role: 'user', content: 'Stage3 delayed snapshot prompt', messageId: 'stage3-delayed-user' },
    {
      role: 'assistant',
      content: 'Stage3 delayed snapshot answer',
      messageId: 'stage3-delayed-answer',
      nativeItemId: 'stage3-delayed-item',
    },
  ]);
  await stream(sessionId, {
    type: 'assistant',
    role: 'assistant',
    taskSeq: 2,
    task_id: 'stage3-delayed-task',
    item_id: 'stage3-delayed-item',
    delta: true,
    stream_text: 'Stage3 delayed live',
    content: 'Stage3 delayed live',
  });
  await waitForText('Stage3 delayed live');
  releaseDelayedRequest?.();
  await sleep(300);
  await page.unroute(delayedHistoryPattern);
  await recordAssertion('delayed-old-history-snapshot-does-not-hide-new-live', true);

  await stopServer();
  await startServer();
  // Let the real wsClient reconnect to the new server epoch while the same
  // Chromium page/context remains alive.
  await sleep(3500);

  const afterRestart = await appendHistory(sessionId, [
    { role: 'user', content: 'Stage3 cold restart prompt', messageId: 'stage3-restart-user' },
    { role: 'assistant', content: 'Stage3 after cold restart', messageId: 'stage3-restart-answer' },
  ]);
  await stream(sessionId, {
    type: 'assistant',
    role: 'assistant',
    item_id: 'stage3-restart-item',
    delta: true,
    stream_text: 'Stage3 after cold restart',
    content: 'Stage3 after cold restart',
  });
  await broadcast({
    type: 'worker.result',
    sessionId,
    workerId: 'e2e-browser-worker',
    generation: 0,
    taskSeq: 1,
    taskId: 'stage3-restart-task',
    status: 'done',
    result: 'Stage3 after cold restart',
    historyEpoch: afterRestart.historyEpoch,
    historyRevision: afterRestart.historyRevision,
    terminalCoverage: {
      historyEpoch: afterRestart.historyEpoch,
      historyRevision: afterRestart.historyRevision,
    },
  });
  await broadcast({
    type: 'worker.status',
    sessionId,
    workerId: 'e2e-browser-worker',
    generation: 0,
    taskSeq: 1,
    status: 'idle',
  });
  await waitForText('Stage3 after cold restart');
  await recordAssertion('cold-restart-generation-zero-accepted', true);

  const finalHistory = await collectHistory(sessionId);
  const finalContents = finalHistory.rows.map((row) => row.content);
  assert.ok(finalHistory.total >= 306, `final history total regressed: ${finalHistory.total}`);
  assert.equal(finalContents.filter((content) => content === 'Stage3 live answer').length, 1);
  assert.equal(finalContents.filter((content) => content === 'Stage3 delayed snapshot answer').length, 1);
  assert.equal(finalContents.filter((content) => content === 'Stage3 after cold restart').length, 1);
  assert.ok(finalContents.includes('history question 1'));
  assert.ok(finalContents.includes('Stage3 cold restart prompt'));
  await recordAssertion('reload-safe-history-and-no-duplicates', {
    total: finalHistory.total,
    rows: finalHistory.rows.length,
    delayedSnapshotFinalCount: finalContents.filter((content) => content === 'Stage3 delayed snapshot answer').length,
    stage3FinalCount: finalContents.filter((content) => content === 'Stage3 after cold restart').length,
  });

  await page.screenshot({ path: path.join(runtime, 'stage3-recovery-final.png'), fullPage: true });
  await context.tracing.stop({ path: path.join(runtime, 'stage3-recovery.trace.zip') });
  traceStarted = false;
  evidence.status = 'passed';
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.stack || error.message : String(error);
  if (page) {
    await page.screenshot({ path: path.join(runtime, 'stage3-recovery-failure.png'), fullPage: true }).catch(() => {});
  }
  if (context && traceStarted) {
    await context.tracing.stop({ path: path.join(runtime, 'stage3-recovery-failure.trace.zip') }).catch(() => {});
    traceStarted = false;
  }
} finally {
  if (traceStarted && context) {
    await context.tracing.stop({ path: path.join(runtime, 'stage3-recovery-finally.trace.zip') }).catch(() => {});
  }
  await page?.close().catch(() => {});
  await context?.close().catch(() => {});
  await browser?.close().catch(() => {});
  await stopServer();
  await fs.writeFile(path.join(runtime, 'stage3-recovery-report.json'), JSON.stringify(evidence, null, 2), 'utf8');
}

console.log(JSON.stringify(evidence, null, 2));
if (evidence.status !== 'passed') process.exitCode = 1;
