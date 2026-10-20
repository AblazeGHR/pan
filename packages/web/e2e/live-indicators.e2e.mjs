import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import process from 'node:process';
import { chromium, expect } from '@playwright/test';

const root = path.resolve(import.meta.dirname, '../../..');
const runtime = path.resolve(import.meta.dirname, '../test-results', `live-indicators-${Date.now()}`);
const python = process.env.PAN_E2E_PYTHON || 'python';
await fs.mkdir(runtime, { recursive: true });
const port = await new Promise((resolve, reject) => {
  const socket = net.createServer(); socket.on('error', reject);
  socket.listen(0, '127.0.0.1', () => { const { port } = socket.address(); socket.close(() => resolve(port)); });
});
assert.ok(![8767, 8768].includes(port));
const base = `http://127.0.0.1:${port}`;
// The parent can be a deployed Pan Worker: retain only tool/OS settings.
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith('PAN_') && key.toUpperCase() !== 'PYTHONPATH'));
Object.assign(env, { PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime, PYTHONPATH: root });
let server, browser;
const evidence = { checkout: root, runtime, port, checks: [], events: [], errors: [], forbiddenReads: [] };
async function api(route, body, method = 'POST') {
  const res = await fetch(base + route, { method, ...(body === undefined ? {} : {
    headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  }) });
  assert.equal(res.status, 200, `${route} -> ${res.status}`);
  const data = await res.json(); assert.ok(!data.error, `${route}: ${JSON.stringify(data)}`); return data;
}
async function ready() {
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(base + '/api/health')).ok) return; } catch { /* owned server startup */ }
    await new Promise(r => setTimeout(r, 100));
  }
  throw Error('isolated server did not start');
}
try {
  server = spawn(python, [path.join(import.meta.dirname, 'live-indicators.server.py')], {
    cwd: root, windowsHide: true, env, stdio: ['ignore', 'pipe', 'pipe'],
  });
  evidence.pid = server.pid;
  const log = createWriteStream(path.join(runtime, 'server.log'));
  server.stdout.pipe(log); server.stderr.pipe(log);
  await ready();
  const sessions = (await api('/api/sessions?summary=1', undefined, 'GET')).sessions;
  const id = name => sessions.find(s => s.name === name).id;
  const A = id('Live Selected'), B = id('Live Background B'), C = id('Live Background C');
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on('pageerror', e => evidence.errors.push(e.message));
  let snapshotReady;
  const snapshot = new Promise(resolve => { snapshotReady = resolve; });
  page.on('websocket', ws => ws.on('framereceived', ({ payload }) => {
    const e = JSON.parse(String(payload));
    evidence.events.push({ type: e.type, sessionId: e.sessionId, jobId: e.jobId,
      status: e.job?.status, target: e.job?.target, summary: e.session && {
        revision: e.session.summaryRevision, preview: e.session.lastDisplayPreview, historyRevision: e.session.historyRevision,
      } });
    if (e.type === 'resync.snapshot') snapshotReady();
  }));
  await page.goto(`${base}/react/?panE2E=1`, { waitUntil: 'networkidle' });
  await snapshot;
  const card = sid => page.locator(`[data-session-card-id="${sid}"]`);
  await card(A).click();
  await page.waitForLoadState('networkidle');
  const initialUrl = page.url();
  assert.ok(!initialUrl.includes('/jobs'));
  // Reject repair reads after hydrate: no refresh, Session switch or Jobs visit
  // can hide a missing ingestion path. Node-side API observations are separate.
  await page.route('**/api/sessions?**', route => { evidence.forbiddenReads.push(route.request().url()); return route.fulfill({ status: 503, body: '{}' }); });
  await page.route('**/api/jobs', route => {
    if (route.request().method() === 'GET') { evidence.forbiddenReads.push(route.request().url()); return route.fulfill({ status: 503, body: '{}' }); }
    return route.continue();
  });
  const historyReads = [];
  page.on('request', request => { if ([B, C].some(sid => request.url().includes(`/sessions/${sid}/history`))) historyReads.push(request.url()); });
  const preview = sid => card(sid).getByTestId('session-preview-line');
  const j = sid => card(sid).getByTestId('session-job-badge');
  const badge = page.getByTestId('jobs-running-badge').first();
  const append = (sid, messages) => api('/__e2e/append-history', { sessionId: sid, messages });
  const broadcast = event => api('/__e2e/broadcast', { event });
  const receipt = { role: 'user', source: 'agent', content: '////by agent : owned sender', queueItemIds: ['owned-B'], messageId: 'owned-B-input' };
  await append(B, [receipt]);
  await broadcast({ type: 'queue.item_delivered', sessionId: B, queueItemIds: ['owned-B'], messages: [receipt] });
  await expect(preview(B)).toContainText('////by agent');
  await append(C, [{ role: 'system', source: 'automation', content: 'C system notice current' }]);
  await broadcast({ type: 'session.updated', sessionId: C });
  await expect(preview(C)).toContainText('C system notice current');
  await append(B, [{ role: 'assistant', content: 'B assistant current' }]);
  await broadcast({ type: 'worker.stream', sessionId: B, workerId: 'owned-stream', generation: 0, taskSeq: 1,
    event: { type: 'assistant', item_id: 'B-reply', content: 'B assistant current' } });
  await expect(preview(B)).toContainText('B assistant current');
  await broadcast({ type: 'queue.item_delivered', sessionId: B, queueItemIds: ['owned-B'], messages: [receipt] });
  await expect(preview(B)).toContainText('B assistant current');
  await expect(preview(C)).toContainText('C system notice current');
  evidence.checks.push('unselected agent receipt + assistant + delayed duplicate receipt, concurrent system Session, list/history repair blocked');
  await page.screenshot({ path: path.join(runtime, 'background-previews.png'), fullPage: true });

  const scheduled = await api('/api/session-message-jobs', { targetSessionId: B, source: 'automation', text: 'owned scheduled context', schedule: { type: 'interval', intervalSeconds: 3600 } });
  await expect(j(B)).toHaveAttribute('data-job-state', 'scheduled');
  await api(`/api/background-jobs/${scheduled.jobId}`, { targetSessionIds: [C] }, 'PATCH');
  await expect(j(B)).toHaveCount(0); await expect(j(C)).toHaveAttribute('data-job-state', 'scheduled');
  evidence.checks.push('agent-facing schedule create and legacy target edit move yellow J while Jobs unopened');

  const processJob = await api('/api/background-jobs', { targetSessionId: B, argv: [python, '-c', 'import time; time.sleep(6)'], cwd: runtime });
  assert.ok(processJob.jobId);
  await expect(j(B)).toHaveAttribute('data-job-state', 'running'); await expect(badge).toHaveText('1');
  await api(`/api/jobs/${processJob.jobId}`, { target: { sessionId: C } }, 'PATCH');
  await expect(j(B)).toHaveCount(0);
  await expect(j(C)).toHaveAttribute('data-job-state', 'running');
  await page.screenshot({ path: path.join(runtime, 'job-running.png'), fullPage: true });
  await expect(j(C)).toHaveAttribute('data-job-state', 'scheduled', { timeout: 20000 });
  await expect(badge).toHaveCount(0);
  await expect(j(C)).toHaveAttribute('data-job-state', 'scheduled');
  const terminal = await api(`/api/background-jobs/${processJob.jobId}`, undefined, 'GET');
  assert.equal(terminal.status, 'completed');
  // Re-publish the old running fact with a NEW transport cursor: durable time
  // must win even though the transport sees a later frame.
  await broadcast({ type: 'job.updated', jobId: processJob.jobId,
    job: { ...processJob, source: { type: 'agent' }, target: { sessionId: B }, status: 'running' } });
  await expect(j(B)).toHaveCount(0); await expect(badge).toHaveCount(0);
  await api(`/api/background-jobs/${scheduled.jobId}/cancel`, {});
  await expect(j(C)).toHaveCount(0);
  assert.equal(page.url(), initialUrl);
  assert.deepEqual(historyReads, [], 'background Sessions must never be selected/hydrated');
  evidence.checks.push('agent-facing process start/terminal update blue J and nav running count; running precedence; stale retry does not regress; cancellation clears yellow J');
  evidence.historyReads = historyReads;
  evidence.initialUrl = initialUrl; evidence.finalUrl = page.url();
  evidence.workers = await api('/__e2e/worker-count', undefined, 'GET');
  assert.equal(evidence.workers.count, 0, 'no provider Worker may be started');
  assert.deepEqual(evidence.errors, []);
  await page.screenshot({ path: path.join(runtime, 'terminal.png'), fullPage: true });
  console.log('PASS', JSON.stringify({ runtime, port, pid: server.pid, checks: evidence.checks }));
} catch (error) {
  evidence.failure = error.stack || String(error);
  throw error;
} finally {
  await browser?.close();
  if (server && server.exitCode === null) {
    const exited = once(server, 'exit');
    await api('/__e2e/shutdown', {});
    await exited;
  }
  evidence.portReleased = await new Promise(resolve => { const s = net.createServer(); s.on('error', () => resolve(false)); s.listen(port, '127.0.0.1', () => s.close(() => resolve(true))); });
  await fs.writeFile(path.join(runtime, 'evidence.json'), JSON.stringify(evidence, null, 2));
  assert.ok(evidence.portReleased, 'owned listener must be released');
}
