/* eslint-disable no-undef */
// Real Chromium/Codex acceptance for T-055's per-session live stream buffer.
// Run against e2e/server.py with PAN_PORT=8799 and the production build.  The
// test intentionally uses the real /api/send, Codex worker and dashboard /ws;
// only the stale-running assertion uses the disposable __e2e injection route.
import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';

const baseURL = process.env.PAN_E2E_BASE_URL || 'http://127.0.0.1:8799';
const browser = await chromium.launch({ headless: true });
const timeline = {
  lastDeltaAt: null,
  finalItemAt: null,
  resultAt: null,
  idleAt: null,
};
const frames = [];

function recordFrame(raw) {
  try {
    const frame = typeof raw === 'string'
      ? JSON.parse(raw)
      : (typeof raw?.payload === 'string' ? JSON.parse(raw.payload) : raw);
    if (!frame || typeof frame !== 'object') return;
    const at = Date.now();
    frames.push({ at, frame });
    if (frame.type === 'worker.result' && frame.sessionId === sessionId) timeline.resultAt ??= at;
    if (frame.type === 'worker.status' && frame.sessionId === sessionId && frame.status === 'idle') timeline.idleAt ??= at;
    if (frame.type !== 'worker.stream' || frame.sessionId !== sessionId) return;
    const event = frame.event || {};
    if (event.delta === true || event.type === 'codex.content_part') {
      timeline.lastDeltaAt = Date.now();
    }
    if (event.final === true || event.type === 'codex.item.completed') {
      timeline.finalItemAt = Date.now();
    }
  } catch {
    // Ignore non-JSON protocol frames.
  }
}

async function jsonRequest(path, init = {}) {
  const response = await fetch(`${baseURL}${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init.headers || {}) },
  });
  const body = await response.json();
  assert.equal(response.ok, true, `${path}: HTTP ${response.status} ${JSON.stringify(body)}`);
  assert.equal(body.error, undefined, `${path}: ${JSON.stringify(body)}`);
  return body;
}

async function waitForPage(page, predicate, timeout = 120_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await page.waitForTimeout(100);
  }
  throw new Error(`Timed out waiting for ${timeout}ms`);
}

const codex = await jsonRequest('/api/sessions', {
  method: 'POST',
  body: JSON.stringify({
    name: `T055-Codex-${Date.now()}`,
    adapter: 'codex',
    model: 'gpt-5.6-luna',
    effort: 'low',
    outputMode: 'stream',
  }),
});
const sessionId = codex.id;
assert.ok(sessionId, 'Codex session creation did not return an id');

const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
page.on('websocket', (socket) => {
  socket.on('framereceived', recordFrame);
});

try {
  await page.goto(`${baseURL}/react/`, { waitUntil: 'domcontentloaded' });
  // Force the first authoritative session load after the API-created fixture;
  // this avoids racing the app's initial summary request with session.create.
  await page.evaluate(() => localStorage.clear());
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2_000);
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'attached' });
  const codexCard = page.locator(`[data-session-card-id="${sessionId}"]`);
  await codexCard.waitFor({ state: 'attached' });
  await codexCard.scrollIntoViewIfNeeded();
  await codexCard.click();
  await page.getByTestId('rich-text-composer').waitFor({ state: 'visible' });

  const sendResponse = await jsonRequest('/api/send', {
    method: 'POST',
    body: JSON.stringify({
      sessionId,
      text: 'Reply with exactly T055-CODEX-FINAL and nothing else.',
      source: 'user',
      clientMessageId: `t055-${Date.now()}`,
    }),
  });
  assert.ok(sendResponse, 'Codex send returned no response');

  await waitForPage(page, () => timeline.lastDeltaAt !== null, 120_000);
  const bravo = page.locator('[data-session-card-id]').filter({ hasText: 'Bravo Session' }).first();
  await bravo.click();
  await page.waitForTimeout(80);
  await page.locator(`[data-session-card-id="${sessionId}"]`).click();

  await waitForPage(page, () => timeline.resultAt !== null && timeline.idleAt !== null, 120_000);
  const resultFrame = frames.find(({ frame }) => frame.type === 'worker.result' && frame.sessionId === sessionId);
  const idleFrame = frames.find(({ frame }) => frame.type === 'worker.status' && frame.sessionId === sessionId && frame.status === 'idle');
  assert.ok(resultFrame, 'missing worker.result frame');
  assert.ok(idleFrame, 'missing worker.status idle frame');
  timeline.resultAt = resultFrame.at;
  timeline.idleAt = idleFrame.at;
  assert.ok(timeline.lastDeltaAt !== null, 'missing Codex delta frame');
  assert.ok(timeline.finalItemAt !== null, 'missing Codex final item frame');
  assert.ok(timeline.lastDeltaAt <= timeline.finalItemAt, 'final item precedes last delta');
  assert.ok(timeline.finalItemAt <= timeline.resultAt, 'result precedes final item');
  assert.ok(timeline.resultAt <= timeline.idleAt, 'idle precedes result');
  assert.ok(timeline.idleAt - timeline.resultAt <= 2_000, 'result did not settle to idle within 2s');

  const historyBody = await jsonRequest(`/api/sessions/${encodeURIComponent(sessionId)}/history`);
  const history = historyBody.history || [];
  const assistants = history.filter((message) => message.role === 'assistant');
  assert.equal(assistants.length, 1, `expected one canonical assistant, got ${assistants.length}`);
  assert.match(assistants[0].content, /T055-CODEX-FINAL/);
  assert.equal(history.some((message) => message.role === 'system' || message.content === '[DONE] Task completed'), false);
  assert.equal(history.some((message) => message.type === 'worker.stream' || message.delta === true), false);
  await page.getByText('T055-CODEX-FINAL', { exact: false }).first().waitFor({ state: 'visible' });

  const workerStatusFrame = frames.find(({ frame }) => frame.type === 'worker.status' && frame.sessionId === sessionId && frame.status === 'running');
  if (workerStatusFrame?.frame.workerId) {
    await jsonRequest('/__e2e/broadcast', {
      method: 'POST',
      body: JSON.stringify({ event: {
        type: 'worker.status',
        sessionId,
        workerId: workerStatusFrame.frame.workerId,
        generation: workerStatusFrame.frame.generation,
        taskSeq: workerStatusFrame.frame.taskSeq,
        status: 'running',
      } }),
    });
    await page.waitForTimeout(250);
    const card = page.locator(`[data-session-card-id="${sessionId}"]`);
    assert.ok(await card.locator('[title="idle"]').count() > 0, 'delayed old running changed the card back to running');
  }

  console.log(JSON.stringify({
    port: new URL(baseURL).port,
    model: 'gpt-5.6-luna',
    effort: 'low',
    sessionId,
    timeline,
    intervalsMs: {
      lastDeltaToFinalItem: timeline.finalItemAt - timeline.lastDeltaAt,
      finalItemToResult: timeline.resultAt - timeline.finalItemAt,
      resultToIdle: timeline.idleAt - timeline.resultAt,
    },
    historyAssistantCount: assistants.length,
    staleRunningChecked: Boolean(workerStatusFrame?.frame.workerId),
    frameCount: frames.filter(({ frame }) => frame.sessionId === sessionId).length,
  }, null, 2));
} finally {
  await context.close();
  await browser.close();
}
