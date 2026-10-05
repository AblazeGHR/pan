/* global window, document, performance, WebSocket, Event, EventTarget, WheelEvent, requestAnimationFrame, setTimeout, console, URL */
// Production bundle, native Chromium geometry, disposable loopback/API/WS
// fixture. Never connects to an existing Pan service or uses user messages.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import process from 'node:process';
import { chromium } from '@playwright/test';

const dist = path.resolve(process.env.PAN_OLDER_DIST || 'dist');
const baseline = process.env.PAN_OLDER_BASELINE === '1';
const samples = Number(process.env.PAN_OLDER_SAMPLES || 3);
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  const file = path.resolve(dist, pathname.startsWith('/react/assets/') ? pathname.slice(7) : 'index.html');
  if (!file.startsWith(dist + path.sep) || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
  const gzip = /\.(js|css)$/.test(file) && fs.existsSync(file + '.gz');
  res.setHeader('content-type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  res.setHeader('cache-control', 'no-store');
  if (gzip) res.setHeader('content-encoding', 'gzip');
  res.end(fs.readFileSync(gzip ? file + '.gz' : file));
});
await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
const base = `http://127.0.0.1:${server.address().port}/react/`;
const rows = Array.from({ length: 5000 }, (_, i) => ({ role: i % 7 === 0 ? 'assistant' : 'user', content: `Older row ${i}\n\n${'Variable height paragraph. '.repeat(i % 8 + 1)}`, messageId: `older-${i}` }));
const sessions = ['a', 'b'].map((id, order) => ({ id, order, name: `Older ${id}`, adapter: 'codex', workdir: '/fixture', workerStatus: 'idle', workerId: null, alwaysThinkingEnabled: false, effort: '', historyTotal: rows.length, historyEpoch: 'history', historyRevision: 4, summaryRevision: 4, workspaceIds: [], managedBy: null, createdAt: '2026-10-05T00:00:00Z', updatedAt: '2026-10-05T00:00:00Z', lastMessage: 'fixture' }));
const browser = await chromium.launch({ headless: true });
const results = [];
async function fixture(rate = 1) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  let holdSummary = false, releaseSummary = () => {};
  let backgroundDelay = 200;
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => assert.ok(!/127\.0\.0\.1:(8767|8768)\//.test(r.url()), 'no protected requests'));
  await page.addInitScript(() => {
    window.WebSocket = class extends EventTarget {
      static OPEN = 1; static CLOSED = 3;
      readyState = WebSocket.OPEN;
      constructor() { super(); setTimeout(() => this.onopen?.(new Event('open')), 0); }
      send() {} close() { this.readyState = WebSocket.CLOSED; }
    };
    window.__reads = [];
    const original = window.fetch.bind(window);
    window.fetch = (url, init) => {
      if (String(url).includes('/history?')) {
        const record = { url: String(url), background: init?.priority === 'low', aborted: false, at: performance.now() };
        window.__reads.push(record);
        init?.signal?.addEventListener('abort', () => { record.aborted = true; });
      }
      return original(url, init);
    };
  });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    let json = { adapters: [] };
    if (url.pathname === '/api/sessions') {
      if (holdSummary) await new Promise(resolve => { releaseSummary = resolve; });
      json = { sessions };
    } else if (url.pathname.endsWith('/history')) {
      const before = Number(url.searchParams.get('before')) || rows.length;
      const limit = Number(url.searchParams.get('limit')) || 50;
      const start = Math.max(0, before - limit);
      await new Promise(resolve => setTimeout(resolve, before === rows.length ? 200 : backgroundDelay));
      json = { history: rows.slice(start, before), start, total: rows.length, hasMore: start > 0, historyEpoch: 'history', historyRevision: 4 };
    } else if (url.pathname === '/api/workspaces') json = { workspaces: [], activeWorkspaceId: null };
    else if (url.pathname === '/api/workers' || url.pathname === '/api/list') json = { workers: [] };
    else if (url.pathname === '/api/adapters') json = { adapters: [] };
    else if (url.pathname === '/api/main/startup-recovery') json = { state: 'no_candidates', candidateSnapshot: [] };
    else if (url.pathname.endsWith('/queue')) json = { items: [], queueRevision: 1 };
    else if (/\/api\/sessions\/[ab]$/.test(url.pathname)) json = sessions.find(s => s.id === url.pathname.at(-1));
    await route.fulfill({ json }).catch(() => {}); // cancelled speculative request
  });
  if (rate > 1) await (await context.newCDPSession(page)).send('Emulation.setCPUThrottlingRate', { rate });
  await page.goto(base + '?panE2E=1');
  await page.locator('[data-session-card-id="a"]').click().catch(async error => {
    console.error(JSON.stringify({ errors, body: await page.locator('body').innerText() })); throw error;
  });
  await page.waitForFunction(() => window.__panSessionStore.getState().currentMessages.length === 50);
  return { context, page, errors, hold: () => { holdSummary = true; }, release: () => { holdSummary = false; releaseSummary(); }, slow: () => { backgroundDelay = 1500; } };
}
try {
  for (const rate of [1, 4]) for (let round = 0; round < samples; round++) {
    const f = await fixture(rate), { page } = f;
    const cdp = await f.context.newCDPSession(page);
    await page.waitForTimeout(400);
    await cdp.send('HeapProfiler.collectGarbage');
    const heapBeforeIdle = (await cdp.send('Runtime.getHeapUsage')).usedSize;
    const scroller = page.locator('.chat-view-stage .overflow-auto').first();
    await page.waitForTimeout(3800);
    if (!baseline) await page.waitForFunction(() => window.__reads.filter(r => r.background).length === 3, null, { timeout: 10000 }).catch(async error => {
      console.error(await page.evaluate(() => {
        const s = window.__panSessionStore.getState();
        return { selected: s.currentSessionId, initial: s.initialLoading, history: s.historyLoading, sessionsLoading: s.sessionsLoading, sessions: s.sessions, window: s.sessionTranscripts[s.currentSessionId]?.window, reads: window.__reads, visible: document.visibilityState, resources: performance.getEntriesByType('resource').map(r => ({ name: r.name, at: r.responseEnd })) };
      })); throw error;
    });
    const idle = await page.evaluate(() => ({ count: window.__panSessionStore.getState().currentMessages.length, before: window.__panSessionStore.getState().historyLoadEnd, reads: window.__reads }));
    assert.equal(idle.count, 50, 'speculation never appends/render old messages');
    assert.equal(idle.before, 4950);
    assert.equal(idle.reads.filter(r => r.background).length, baseline ? 0 : 3, 'bounded three-page prefetch');
    await cdp.send('HeapProfiler.collectGarbage');
    const heapAfterIdle = (await cdp.send('Runtime.getHeapUsage')).usedSize;
    if (!baseline) assert.ok(await page.evaluate(() => window.__panSessionStore.getState().hasPrefetchedOlderMessages()));
    // Set up a near-top viewport without triggering a page load, then deliver
    // an upward wheel gesture and its native scroll event. Mark the frame at
    // the top before the original anchor-preserving loader applies the page.
    await scroller.evaluate(el => { el.scrollTop = 250; });
    await page.waitForTimeout(450);
    await scroller.evaluate(el => {
      const unsubscribe = window.__panSessionStore.subscribe(state => {
        if (state.historyLoadEnd !== 4900) return;
        unsubscribe();
        requestAnimationFrame(() => requestAnimationFrame(() => { window.__olderPaintAt = performance.now(); }));
      });
      el.addEventListener('scroll', () => {
        if (el.scrollTop <= 200 && !window.__olderClick) {
          const top = el.getBoundingClientRect().top;
          const row = [...el.querySelectorAll('[data-message-identity]')].find(r => r.getBoundingClientRect().bottom > top);
          window.__olderClick = { at: performance.now(), identity: row?.dataset.messageIdentity, offset: row?.getBoundingClientRect().top - top };
        }
      });
      el.dispatchEvent(new WheelEvent('wheel', { deltaY: -400, bubbles: true }));
      el.scrollTop = 0;
    });
    await page.waitForFunction(() => window.__olderPaintAt > 0);
    const value = await scroller.evaluate(el => {
      const click = window.__olderClick;
      const row = [...el.querySelectorAll('[data-message-identity]')].find(r => r.dataset.messageIdentity === click.identity);
      return { ms: window.__olderPaintAt - click.at, anchorError: row ? row.getBoundingClientRect().top - el.getBoundingClientRect().top - click.offset : null, messages: window.__panSessionStore.getState().currentMessages.length, renderedRows: el.querySelectorAll('[data-message-identity]').length, heap: performance.memory?.usedJSHeapSize };
    });
    assert.equal(value.messages, 100);
    assert.ok(value.anchorError !== null && Math.abs(value.anchorError) <= 4, `stable prepend anchor: ${JSON.stringify(value)}`);
    await page.waitForTimeout(750);
    assert.equal(await page.evaluate(() => window.__panSessionStore.getState().currentMessages.length), 100, 'restore cannot chain extra pages');
    assert.deepEqual(f.errors, []);
    results.push({ rate, round, ...value, heapBeforeIdle, heapAfterIdle, idleHeapDelta: heapAfterIdle - heapBeforeIdle });
    await f.context.close();
  }
  if (!baseline) {
    const f = await fixture(), { page } = f;
    f.slow();
    await page.waitForFunction(() => window.__reads.some(r => r.background));
    f.hold();
    await page.evaluate(() => { void window.__panSessionStore.getState().loadSessions(); });
    await page.waitForFunction(() => window.__reads.some(r => r.background && r.aborted));
    await page.waitForTimeout(2200);
    assert.equal(await page.evaluate(() => window.__reads.filter(r => r.background).length), 1, 'no speculative retries while foreground body is pending');
    assert.equal(await page.evaluate(() => window.__panSessionStore.getState().hasPrefetchedOlderMessages()), false, 'aborted response discarded');
    f.release();
    await page.waitForFunction(() => window.__reads.filter(r => r.background).length > 1);
    await page.locator('[data-session-card-id="b"]').click();
    await page.waitForFunction(() => window.__panSessionStore.getState().currentSessionId === 'b' && window.__panSessionStore.getState().currentMessages.length === 50);
    assert.equal(await page.evaluate(() => window.__panSessionStore.getState().hasPrefetchedOlderMessages()), false, 'old Session cache unavailable');
    await page.evaluate(() => {
      const store = window.__panSessionStore;
      store.setState({ sessions: store.getState().sessions.map(s => ({ ...s, workerStatus: 'running' })) });
    });
    const reads = await page.evaluate(() => window.__reads.length);
    await page.waitForTimeout(3800);
    assert.equal(await page.evaluate(() => window.__reads.length), reads, 'worker activity suppresses speculation');
    assert.equal(await page.evaluate(() => window.__panSessionStore.getState().historyLoadEnd), 4950);
    assert.deepEqual(f.errors, []);
    results.push({ scenario: 'foreground-abort-session-switch-worker', passed: true });
    await f.context.close();
  }
  console.log(JSON.stringify({ baseline, samples, results }, null, 2));
} finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
