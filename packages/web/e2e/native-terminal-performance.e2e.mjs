/* global window, document, EventTarget, Event, WebSocket, performance, requestAnimationFrame, PerformanceObserver */
// Production bundle + real xterm + eight concurrent synthetic stream buffers.
// Owned ephemeral static server; all API/WS traffic is intercepted, no provider.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import process from 'node:process';
import console from 'node:console';
import { Buffer } from 'node:buffer';
import { setTimeout } from 'node:timers';
import { URL } from 'node:url';
import { chromium } from '@playwright/test';

const dist = path.resolve(process.env.PAN_NATIVE_DIST || 'dist');
const baseline = process.env.PAN_NATIVE_BASELINE === '1';
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  const file = path.resolve(dist, pathname.startsWith('/react/assets/') ? pathname.slice(7) : 'index.html');
  if (!file.startsWith(dist + path.sep) || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
  res.setHeader('content-type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  res.end(fs.readFileSync(file));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ headless: true });
const results = [];
const sessions = Array.from({ length: 9 }, (_, i) => ({ id: `native-${i}`, name: `Native ${i}`, adapter: 'codex',
  nativeControl: true, workerId: `worker-${i}`, workerStatus: 'running', lastLegalWorkerState: 'running',
  workerGeneration: 1, workerTaskSeq: 1, alwaysThinkingEnabled: false, effort: '', historyTotal: 0,
  historyEpoch: 'native', historyRevision: 1, workspaceIds: [], history: [] }));
try {
  for (const latency of [0, 80]) for (let sample = 0; sample < 3; sample++) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage(); page.setDefaultTimeout(15000);
    const errors = [], requests = [];
    let payload = Buffer.alloc(0), total = 0, inflight = 0, maxInflight = 0;
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', message => { if (message.type() === 'error') console.error(message.text()); });
    page.on('request', r => assert.ok(!/127\.0\.0\.1:(8767|8768)\//.test(r.url())));
    await page.addInitScript(() => {
      window.WebSocket = class extends EventTarget {
        static OPEN = 1; static CLOSED = 3; readyState = WebSocket.OPEN;
        constructor() { super(); setTimeout(() => this.onopen?.(new Event('open')), 0); }
        send() {} close() { this.readyState = WebSocket.CLOSED; }
      };
    });
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url()); let json = {};
      if (url.pathname.includes('/native-control')) {
        const state = { workerId: 'worker-0', generation: 1, mode: 'native', error: null, enabled: true, pendingEnabled: null };
        let result = state;
        if (url.pathname.endsWith('/output')) {
          const cursor = Number(url.searchParams.get('cursor'));
          inflight++; maxInflight = Math.max(maxInflight, inflight);
          requests.push({ at: Date.now(), cursor });
          await new Promise(resolve => setTimeout(resolve, latency));
          const chunk = payload.subarray(cursor, Math.min(cursor + 65536, total));
          result = { ...state, cursor: String(cursor + chunk.length), gap: false, data: chunk.toString('base64') };
          inflight--;
        }
        json = { ok: true, result };
      } else if (url.pathname === '/api/sessions') json = { sessions };
      else if (/\/api\/sessions\/native-\d$/.test(url.pathname)) json = sessions.find(s => url.pathname.endsWith(s.id));
      else if (url.pathname.endsWith('/history')) json = { history: [], total: 0, start: 0, hasMore: false, historyEpoch: 'native', historyRevision: 1 };
      else if (url.pathname.endsWith('/queue')) json = { items: [], queueRevision: 1 };
      else if (url.pathname === '/api/workspaces') json = { workspaces: [], activeWorkspaceId: null };
      else if (url.pathname === '/api/adapters') json = { adapters: [] };
      else if (url.pathname === '/api/cli/status') json = { adapters: [], hasAvailable: true };
      else if (url.pathname === '/api/workers' || url.pathname === '/api/list') json = { workers: [] };
      else if (url.pathname === '/api/main/startup-recovery') json = { state: 'no_candidates', candidateSnapshot: [] };
      await route.fulfill({ json }).catch(() => {});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/react/?panE2E=1`);
    await page.locator('[data-session-card-id="native-0"]').click().catch(async error => {
      console.error(JSON.stringify({ errors, body: await page.locator('body').innerText() })); throw error;
    });
    await page.getByTestId('native-terminal').waitFor();
    await page.evaluate(() => {
      window.__frames = []; window.__tasks = []; window.__stores = []; window.__pumping = true;
      let previous = performance.now(), step = 0;
      const frame = now => { if (!window.__pumping) return; window.__frames.push(now - previous); previous = now; requestAnimationFrame(frame); };
      requestAnimationFrame(frame);
      new PerformanceObserver(list => window.__tasks.push(...list.getEntries().map(e => e.duration))).observe({ type: 'longtask' });
      const pump = () => {
        if (!window.__pumping) return;
        step++;
        const store = window.__panSessionStore;
        for (let i = 1; i < 9; i++) {
          const start = performance.now();
          assertAccepted(store.getState().applyLiveStream(`native-${i}`, [{ role: 'assistant', content: `Agent ${i} delta ${step} ` + 'parallel output '.repeat(step), nativeItemId: `item-${i}` }],
            { workerId: `worker-${i}`, generation: 1, taskSeq: 1, executionSeq: 1 }));
          window.__stores.push(performance.now() - start);
          if (step % 25 === 0) store.getState().updateSession(`native-${i}`, { lastMessage: `Agent ${i} summary ${step}` });
        }
        setTimeout(pump, 20);
      };
      function assertAccepted(accepted) { if (!accepted) throw new Error('background stream rejected'); }
      pump();
    });
    await page.waitForTimeout(1800);
    const idleStart = requests.length;
    await page.waitForTimeout(2000);
    const idleRequests = requests.length - idleStart;
    payload = Buffer.from(('\x1b[2J\x1b[Hparallel ANSI 中文\r\n').repeat(25000) + 'NATIVE-CATCHUP-END'); total = payload.length;
    const started = Date.now();
    await page.waitForFunction(() => document.querySelector('[data-testid="native-terminal"]')?.textContent.includes('NATIVE-CATCHUP-END'), null, { timeout: 15000 });
    const catchupMs = Date.now() - started;
    assert.equal(maxInflight, 1, 'output reads stay serialized');
    assert.equal(await page.evaluate(() => window.__panSessionStore.getState().currentMessages.length), 0, 'background streams leave selected native history unchanged');
    await page.evaluate(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }); document.dispatchEvent(new Event('visibilitychange')); });
    await page.waitForTimeout(300);
    const hiddenStart = requests.length;
    await page.waitForTimeout(900);
    const hiddenRequests = requests.length - hiddenStart;
    if (!baseline) assert.equal(hiddenRequests, 0, 'hidden output polling pauses');
    payload = Buffer.concat([payload, Buffer.from('\r\nNATIVE-RESUME-END')]); total = payload.length;
    await page.evaluate(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' }); document.dispatchEvent(new Event('visibilitychange')); });
    const resumed = Date.now();
    await page.waitForFunction(() => document.querySelector('[data-testid="native-terminal"]')?.textContent.includes('NATIVE-RESUME-END'));
    const resumeMs = Date.now() - resumed;
    const metrics = await page.evaluate(() => {
      window.__pumping = false;
      const percentile = values => values.sort((a,b) => a-b)[Math.floor(values.length * .95)] ?? 0;
      return { frameP95Ms: percentile(window.__frames), storeP95Ms: percentile(window.__stores), longTasks: window.__tasks.length, maxLongTaskMs: Math.max(0, ...window.__tasks), backgroundBuffers: Object.keys(window.__panSessionStore.getState().liveStreamBuffers).length };
    });
    assert.equal(metrics.backgroundBuffers, 8);
    assert.ok(metrics.frameP95Ms < 34, 'terminal catch-up keeps 95% of frames within two 60Hz frame intervals');
    assert.ok(metrics.storeP95Ms < 5, 'background stream projections stay within the per-update budget');
    assert.deepEqual(errors, []);
    results.push({ latency, sample, bytes: total, idleRequests, hiddenRequests, catchupMs, resumeMs, ...metrics });
    console.log(JSON.stringify(results.at(-1)));
    await context.close();
  }
  console.log(JSON.stringify({ baseline, dist, results }, null, 2));
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
