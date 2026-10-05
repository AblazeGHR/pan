/* global window, document, performance, WebSocket, Event, EventTarget, MutationObserver, setTimeout, console, URL */
// Production shell cold-load comparison. Disposable browser contexts and a
// loopback static server only; all API/WS state is synthetic, with no Pan CLI.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import process from 'node:process';
import { chromium } from '@playwright/test';

const dist = path.resolve(process.env.PAN_SIDEBAR_DIST || 'dist');
const baseline = process.env.PAN_SIDEBAR_BASELINE === '1';
const samples = Number(process.env.PAN_SIDEBAR_SAMPLES || 5);
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  const relative = pathname.startsWith('/react/assets/') ? pathname.slice(7) : 'index.html';
  const file = path.resolve(dist, relative);
  if (!file.startsWith(dist + path.sep) || !fs.existsSync(file)) {
    res.writeHead(404); res.end(); return;
  }
  const compressed = /\.(js|css)$/.test(file) && fs.existsSync(file + '.gz');
  res.setHeader('content-type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  res.setHeader('cache-control', 'no-store');
  if (compressed) res.setHeader('content-encoding', 'gzip');
  res.end(fs.readFileSync(compressed ? file + '.gz' : file));
});
await new Promise((resolve, reject) => {
  server.once('error', reject);
  // An OS-selected loopback port cannot claim an occupied protected service.
  server.listen(0, '127.0.0.1', resolve);
});
const base = `http://127.0.0.1:${server.address().port}/react/`;
const sessions = Array.from({ length: 100 }, (_, i) => ({
  id: `cold-${i}`, name: `Cold session ${i}`, adapter: 'codex',
  workdir: '/fixture', workerStatus: null, workerId: null,
  updatedAt: '2026-10-05T00:00:00Z', createdAt: '2026-10-05T00:00:00Z',
  order: i, workspaceIds: [], managedBy: null, alwaysThinkingEnabled: false,
  lastMessage: 'fixture preview', historyTotal: 1, summaryRevision: 1,
}));
const browser = await chromium.launch({ headless: true });
const results = [];
try {
  for (const mode of ['local', 'constrained', 'blocked-chat']) {
    for (let round = 0; round < (mode === 'blocked-chat' ? 1 : samples); round += 1) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const page = await context.newPage();
      const errors = [];
      const assets = [];
      let summaryRequests = 0;
      page.on('pageerror', e => errors.push(e.message));
      page.on('request', r => {
        assert.ok(!/127\.0\.0\.1:(8767|8768)\//.test(r.url()), 'no protected requests');
        if (r.url().includes('/assets/')) assets.push(r.url());
      });
      await page.addInitScript(() => {
        // No reconnect/replay or external service can mask the initial HTTP read.
        window.WebSocket = class extends EventTarget {
          static OPEN = 1; static CLOSED = 3;
          readyState = WebSocket.OPEN;
          constructor() { super(); setTimeout(() => this.onopen?.(new Event('open')), 0); }
          send() {}
          close() { this.readyState = WebSocket.CLOSED; }
        };
        window.__firstCardAt = null;
        new MutationObserver(() => {
          if (window.__firstCardAt === null && document.querySelector('[data-session-card-id]')) {
            window.__firstCardAt = performance.now();
          }
        }).observe(document, { childList: true, subtree: true });
        const original = window.fetch.bind(window);
        window.fetch = (...args) => {
          if (String(args[0]).includes('/api/sessions?summary=1')) window.__summaryAt = performance.now();
          return original(...args);
        };
      });
      await page.route('**/api/**', async route => {
        const url = new URL(route.request().url());
        let json = { adapters: [] };
        if (url.pathname === '/api/sessions') { summaryRequests += 1; json = { sessions }; }
        else if (url.pathname === '/api/workers' || url.pathname === '/api/list') json = { workers: [] };
        else if (url.pathname === '/api/workspaces') json = { workspaces: [], activeWorkspaceId: null };
        else if (url.pathname === '/api/adapters') json = { adapters: [] };
        else if (url.pathname === '/api/main/startup-recovery') json = { state: 'no_candidates', candidateSnapshot: [] };
        else if (url.pathname.endsWith('/history')) json = { history: [{ role: 'user', content: 'cold fixture history', nativeItemId: 'fixture-row' }], total: 1, start: 0, hasMore: false, historyEpoch: 'fixture', historyRevision: 1 };
        else if (url.pathname.endsWith('/queue')) json = { items: [], queueRevision: 1 };
        else if (/\/api\/sessions\/cold-\d+$/.test(url.pathname)) json = sessions[Number(url.pathname.split('-').at(-1))];
        await route.fulfill({ json });
      });
      if (mode === 'blocked-chat') {
        await page.route('**/assets/ChatView-*.js', async route => {
          await new Promise(resolve => setTimeout(resolve, 1800));
          await route.continue();
        });
      }
      const cdp = await context.newCDPSession(page);
      if (mode === 'constrained') {
        await cdp.send('Network.enable');
        await cdp.send('Network.emulateNetworkConditions', {
          offline: false, latency: 40, downloadThroughput: 500 * 1024,
          uploadThroughput: 500 * 1024,
        });
        await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
      }
      await page.goto(base + '?panE2E=1', { waitUntil: 'commit' });
      const card = page.locator('[data-session-card-id="cold-0"]');
      await card.waitFor({ timeout: 30000 }).catch(async error => {
        console.error(JSON.stringify({ errors, assets, body: await page.locator('body').innerText() }, null, 2));
        throw error;
      });
      const timing = await page.evaluate(() => ({
        firstCardMs: window.__firstCardAt, summaryStartMs: window.__summaryAt,
      }));
      if (!baseline) {
        assert.ok(!assets.some(url => /monaco-vendor/.test(url)), 'editor runtime excluded from chat startup');
        if (mode === 'blocked-chat') assert.ok(timing.firstCardMs < 1500, 'sidebar renders while chat chunk is blocked');
      }
      const clickedAt = Date.now();
      await card.click();
      await page.locator('main').getByText('cold fixture history', { exact: true }).waitFor({ timeout: 10000 });
      const clickMs = Date.now() - clickedAt;
      const historyVisibleMs = await page.evaluate(() => performance.now());
      await page.locator('[contenteditable="true"]').first().waitFor();
      if (!baseline) {
        const editorResponse = page.waitForResponse(response => /\/EditorView-.*\.js$/.test(response.url()));
        await page.getByRole('link', { name: 'Editor', exact: true }).click();
        await editorResponse;
        await page.waitForURL('**/react/editor');
        await page.locator('main').getByText('Cold session 0', { exact: true }).waitFor();
        assert.ok(assets.some(url => /EditorView-/.test(url)), 'editor route chunk loaded on demand');
        await page.getByRole('link', { name: 'Chat', exact: true }).click();
        await page.locator('main').getByText('cold fixture history', { exact: true }).waitFor();
      }
      assert.deepEqual(errors, [], 'no page errors');
      assert.equal(summaryRequests, 1, 'exactly one initial summary request');
      results.push({ mode, round, ...timing, clickMs, historyVisibleMs, summaryRequests });
      await context.close();
    }
  }
  console.log(JSON.stringify({ baseline, results }, null, 2));
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
