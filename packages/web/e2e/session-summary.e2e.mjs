import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { execFileSync, spawn } from 'node:child_process';
import { once } from 'node:events';
import process from 'node:process';
import console from 'node:console';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium, expect } from '@playwright/test';

const root = path.resolve(import.meta.dirname, '../../..');
const runtime = path.resolve(import.meta.dirname, '../test-results', `summary-${Date.now()}`);
await fs.mkdir(runtime, { recursive: true });
const port = await new Promise(resolve => {
  const s = net.createServer();
  s.listen(0, '127.0.0.1', () => { const { port } = s.address(); s.close(() => resolve(port)); });
});
assert.ok(![8767, 8768].includes(port));
const base = `http://127.0.0.1:${port}`;
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith('PAN_') && key.toUpperCase() !== 'PYTHONPATH'));
Object.assign(env, { PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime, PYTHONPATH: root });
let server, browser, releaseHistory;
const evidence = { root, runtime, port, checks: [], events: [], errors: [] };
evidence.sourceSha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim();
const assets = path.join(import.meta.dirname, '../dist/assets');
const entry = (await fs.readdir(assets)).find(name => /^index-.*\.js$/.test(name));
evidence.bundle = entry;
evidence.bundleHasFix = (await fs.readFile(path.join(assets, entry), 'utf8')).includes('summaryHistoryRevision');
console.log('BUILD', JSON.stringify({ runtime, bundle: evidence.bundle, bundleHasFix: evidence.bundleHasFix }));
async function api(route, body, method = 'POST') {
  const response = await globalThis.fetch(base + route, { method, ...(body === undefined ? {} : {
    headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  }) });
  assert.equal(response.status, 200, route);
  const result = await response.json(); assert.ok(!result.error, JSON.stringify(result)); return result;
}
async function ready() {
  for (let i = 0; i < 100; i++) {
    try { if ((await globalThis.fetch(base + '/api/health')).ok) return; } catch { /* owned startup */ }
    await delay(100);
  }
  throw Error('owned server startup timed out');
}
try {
  server = spawn(process.env.PAN_E2E_PYTHON || 'python', [path.join(import.meta.dirname, 'session-summary.server.py')], {
    cwd: root, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  });
  evidence.launcherPid = server.pid;
  const log = createWriteStream(path.join(runtime, 'server.log'));
  server.stdout.pipe(log); server.stderr.pipe(log);
  await ready();
  const initial = (await api('/api/sessions?summary=1', undefined, 'GET')).sessions;
  const A = initial.find(s => s.name === 'Summary A').id, B = initial.find(s => s.name === 'Summary B').id;
  browser = await chromium.launch({ headless: true });
  evidence.chromiumVersion = browser.version();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on('pageerror', error => evidence.errors.push(error.message));
  page.on('websocket', ws => ws.on('framereceived', ({ payload }) => {
    const e = JSON.parse(String(payload));
    evidence.events.push({ type: e.type, sessionId: e.sessionId, event: e.event?.type,
      delta: e.event?.delta, status: e.status, taskSeq: e.taskSeq, generation: e.generation,
      revision: e.session?.summaryRevision, historyRevision: e.session?.historyRevision,
      preview: e.session?.lastDisplayPreview });
  }));
  const card = sid => page.locator(`[data-session-card-id="${sid}"]`);
  const preview = sid => card(sid).getByTestId('session-preview-line');
  const watchPreview = () => preview(B).evaluate(node => {
    globalThis.summaryPreviewTimeline = [node.textContent];
    const observer = new globalThis.MutationObserver(() => {
      globalThis.summaryPreviewTimeline.push(node.textContent);
    });
    observer.observe(node, { childList: true, subtree: true, characterData: true });
  });
  const collectPreview = async () => {
    const timeline = await page.evaluate(() => globalThis.summaryPreviewTimeline ?? []);
    (evidence.previewTimelines ??= []).push(timeline);
    for (const token of ['probe-one', 'probe-interrupt', 'probe-force']) {
      const streamed = timeline.findIndex(text => text.includes(`${token} streaming`));
      if (streamed >= 0) assert.ok(!timeline.slice(streamed + 1).includes(token), `${token} preview regressed: ${timeline}`);
    }
  };
  const broadcast = event => api('/__e2e/broadcast', { event });
  const append = messages => api('/__e2e/append-history', { sessionId: B, messages });
  await page.goto(`${base}/react/?panE2E=1`, { waitUntil: 'networkidle' });
  await card(A).click();
  const receipt = { role: 'user', source: 'agent', content: 'MA send old input', queueItemIds: ['owned-q'] };
  await append([receipt]);
  await broadcast({ type: 'queue.item_delivered', sessionId: B, queueItemIds: ['owned-q'], messages: [receipt] });
  await expect(preview(B)).toContainText('MA send old input');
  // Hold the event-triggered list read before the server handles it. Persist
  // a newer answer without a corresponding WS event, then let HTTP reconcile
  // it against the cached MA event patch. Both payloads are real server views.
  let listCaptured, listRelease;
  const listStarted = new Promise(resolve => { listCaptured = resolve; });
  const listGate = new Promise(resolve => { listRelease = resolve; });
  let listHeld = false;
  await page.route('**/api/sessions?**', async route => {
    if (listHeld) return route.continue();
    listHeld = true; listCaptured(); await listGate;
    const response = await route.fetch();
    evidence.newerHttpSummary = await response.json();
    await route.fulfill({ response });
  });
  await broadcast({ type: 'session.updated', sessionId: B });
  await listStarted;
  await append([{ role: 'assistant', content: 'Current assistant answer' }]);
  listRelease();
  await page.waitForTimeout(700);
  await expect(preview(B)).toContainText('Current assistant answer');
  await page.unroute('**/api/sessions?**');
  evidence.checks.push('newer real HTTP summary wins over cached older MA WS patch');
  let capturedHistory;
  const captured = new Promise(resolve => { capturedHistory = resolve; });
  const release = new Promise(resolve => { releaseHistory = resolve; });
  let held = false;
  const historyPattern = `**/api/sessions/${B}/history?**`;
  await page.route(historyPattern, async route => {
    if (held) return route.continue();
    held = true;
    const response = await route.fetch();
    evidence.heldHistory = await response.json();
    capturedHistory(); await release;
    await route.fulfill({ response });
  });
  await card(B).click();
  await captured;
  await append([{ role: 'assistant', content: 'Current assistant answer' }]);
  await broadcast({ type: 'worker.stream', sessionId: B,
    event: { type: 'assistant', content: 'Current assistant answer' } });
  await expect(preview(B)).toContainText('Current assistant answer');
  releaseHistory();
  await page.waitForLoadState('networkidle');
  await expect(preview(B)).toContainText('Current assistant answer');
  await page.unroute(historyPattern);
  evidence.checks.push('older real history HTTP response released after newer assistant WS cannot restore MA preview');
  await card(A).click(); await card(B).click();
  await expect(preview(B)).toContainText('Current assistant answer');
  await page.reload({ waitUntil: 'networkidle' });
  await expect(preview(B)).toContainText('Current assistant answer');
  evidence.checks.push('Session switch and cold production-page hydration retain durable assistant preview');

  // Keep the target unselected: selected-history recovery can incidentally
  // repaint a broken card from the live chat projection and hide regression.
  await card(A).click();
  await watchPreview();

  // Real Send enqueues while paused. Queued text must not be represented as
  // already handed to the provider or included in canonical history.
  const before = (await api('/api/sessions?summary=1', undefined, 'GET')).sessions.find(s => s.id === B);
  await api('/api/send', { sessionId: B, source: 'agent', text: 'probe-one', clientMessageId: 'owned-one' });
  const queued = (await api('/api/sessions?summary=1', undefined, 'GET')).sessions.find(s => s.id === B);
  assert.equal(queued.historyTotal, before.historyTotal);
  assert.equal(queued.lastDisplayPreview, before.lastDisplayPreview);
  await expect(preview(B)).toContainText('Current assistant answer');
  await api(`/api/sessions/${B}/worker/restart`, {});
  await api(`/api/sessions/${B}/queue/paused`, { paused: false }, 'PATCH');
  await expect(preview(B)).toContainText('probe-one streaming', { timeout: 20000 });
  // A fresh event cursor with an unchanged durable history is still the old
  // projection; it cannot overwrite the live suffix.
  await broadcast({ type: 'session.updated', sessionId: B });
  // Allow the actual WS handler and its existing debounced HTTP reconciliation
  // to finish. An immediate assertion can accidentally run before the frame.
  await page.waitForTimeout(700);
  await expect(preview(B)).toContainText('probe-one streaming');
  await api('/__e2e/disconnect', {});
  await expect.poll(() => evidence.events.filter(e => e.type === 'resync.snapshot').length).toBeGreaterThan(1);
  await expect(preview(B)).toContainText('probe-one streaming');
  await fs.writeFile(path.join(runtime, 'probe-one.complete'), 'go');
  await expect(preview(B)).toContainText('probe-one answer');
  evidence.checks.push('real agent Send: paused enqueue excludes body from context, handoff streams, duplicate projection and reconnect do not regress, terminal updates');
  await api('/api/send', { sessionId: B, source: 'agent', text: 'probe-interrupt', clientMessageId: 'owned-interrupt' });
  await expect(preview(B)).toContainText('probe-interrupt streaming');
  await api('/api/send', { sessionId: B, source: 'agent', text: 'probe-force', force: true, clientMessageId: 'owned-force' });
  await expect(preview(B)).toContainText('probe-force streaming', { timeout: 20000 });
  await fs.writeFile(path.join(runtime, 'probe-force.complete'), 'go');
  await expect(preview(B)).toContainText('probe-force answer');
  await collectPreview();
  await card(A).click(); await card(B).click();
  await page.reload({ waitUntil: 'networkidle' });
  await expect(preview(B)).toContainText('probe-force answer');
  const final = (await api('/api/sessions?summary=1', undefined, 'GET')).sessions.find(s => s.id === B);
  assert.equal(final.lastDisplayPreview, 'probe-force answer');
  assert.equal(final.lastMessage, final.lastDisplayPreview);
  evidence.finalSummary = final;
  evidence.checks.push('real agent Force while running restarts fake CLI, streams replacement, terminal survives switch and cold refresh');
  // Canonical body deliberately differs from display-time link projection.
  const rawBody = '  # Copy original\n\n[notes](notes.md#L2)\n\n```ts\nconst raw = "<tag>";\n```\n'
    + '完整正文 '.repeat(500) + '\nCOPY-END  ';
  await append([{ role: 'assistant', content: rawBody }]);
  await broadcast({ type: 'worker.stream', sessionId: B, event: { type: 'assistant', content: rawBody } });
  await expect(preview(B)).toContainText('Copy original');
  await append([{ role: 'assistant', content: ' \n' }, { role: 'thinking', content: 'private thought' },
    { role: 'tool', content: 'tool output' }]);
  await broadcast({ type: 'session.updated', sessionId: B });
  await expect(preview(B)).toContainText('Copy original');
  await page.reload({ waitUntil: 'networkidle' });
  await expect(preview(B)).toContainText('Copy original');
  const detail = await api(`/api/sessions/${B}`, undefined, 'GET');
  const row = detail.history.find(m => m.content.includes('COPY-END'));
  assert.ok(row?.messageId);
  assert.notEqual(row.content, rawBody, 'fixture must exercise display link rewriting');
  await card(B).click();
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  const gutter = page.locator(`[data-message-bookmark-gutter="${row.messageId}"]`);
  await gutter.click();
  await expect(page.getByRole('menuitem', { name: 'Add bookmark', exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(runtime, 'copy-menu.png'), fullPage: true });
  await page.getByRole('menuitem', { name: '复制全文', exact: true }).click();
  // Windows clipboard uses CRLF for line endings; preserve all other bytes.
  await expect.poll(async () => (await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n')).toBe(rawBody);
  const clipboard = await page.evaluate(() => navigator.clipboard.readText());
  assert.equal(clipboard.replace(/\r\n/g, '\n'), rawBody);
  evidence.copy = { messageId: row.messageId, length: clipboard.length, canonicalExact: true,
    newlineNormalization: 'Windows clipboard CRLF to LF for comparison only',
    displayWasProjected: row.content !== rawBody };
  evidence.checks.push('full original Markdown copied through actual Chromium clipboard, including rewritten-link source, code and trailing whitespace');
  evidence.checks.push('empty assistant + thinking/tool cannot erase completed assistant preview in real WS or cold load');
  assert.deepEqual(evidence.errors, []);
  await page.screenshot({ path: path.join(runtime, 'final.png'), fullPage: true });
  console.log('PASS', JSON.stringify({ runtime, port, checks: evidence.checks }));
} catch (error) {
  evidence.failure = error.stack || String(error); throw error;
} finally {
  releaseHistory?.();
  await browser?.close();
  if (server && server.exitCode === null) {
    const exited = once(server, 'exit');
    await api('/__e2e/shutdown', {}); await exited;
  }
  evidence.portReleased = await new Promise(resolve => {
    const s = net.createServer(); s.on('error', () => resolve(false));
    s.listen(port, '127.0.0.1', () => s.close(() => resolve(true)));
  });
  await fs.writeFile(path.join(runtime, 'evidence.json'), JSON.stringify(evidence, null, 2));
  assert.ok(evidence.portReleased);
}
