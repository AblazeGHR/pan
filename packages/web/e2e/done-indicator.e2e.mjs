/* global window, process, setTimeout, URL, Event, console */
// Isolated Chromium regression for T-030 (done indicator latency).
//
// Serves the production build from `dist/` on a loopback-only, isolated port and
// drives the REAL app through the REAL path:
//   fake WebSocket frame -> ws.ts dispatch -> useWebSocket handler ->
//   sessionStore merge -> WorkerDot render
// The REST surface is answered by the same Node process so the test can flip the
// "server" state between steps (mimicking a settled backend status).
//
// Safety: loopback only, never touches 8765/8767/8768, never starts a Pan
// service. Port is configurable and defaults to 8766.
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';

const DIST = path.resolve('dist');
const PORT = Number(process.env.PAN_DONE_INDICATOR_E2E_PORT || 8766);
const BASE = `http://127.0.0.1:${PORT}`;

assert.ok(
  fs.existsSync(path.join(DIST, 'index.html')),
  `missing build at ${DIST}; run pnpm build first`,
);
assert.notEqual(PORT, 8765, 'refusing to bind the shared 8765 port');
assert.notEqual(PORT, 8767, 'refusing to bind the shared 8767 port');
assert.notEqual(PORT, 8768, 'refusing to bind the protected 8768 port');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
};

// ── "Server" state the REST endpoints report ──
const session = (workerStatus, workerId = 'w1') => ({
  id: 'A',
  name: 'Alpha',
  adapter: 'cbc',
  workerStatus,
  workerId,
  historyTotal: 1,
  lastMessage: 'seed',
  alwaysThinkingEnabled: false,
  effort: '',
});
let sessionsState = [session('running')];

function json(res, code, body) {
  const payload = JSON.stringify(body);
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(payload);
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, BASE);
  const p = url.pathname;

  if (p === '/api/sessions' && url.searchParams.get('summary') === '1') {
    return json(res, 200, { sessions: sessionsState });
  }
  if (p === '/api/sessions') return json(res, 200, { sessions: sessionsState });
  if (/^\/api\/sessions\/[^/]+\/history$/.test(p)) {
    return json(res, 200, { history: [], total: 0, hasMore: false, start: 0 });
  }
  if (p === '/api/list') return json(res, 200, { workers: [] });
  // Everything else is a benign 404; the stores already handle fetch failures.
  if (p.startsWith('/api/')) return json(res, 404, { error: 'not found' });

  const rel = p.startsWith('/react/') ? p.slice('/react/'.length) : p.replace(/^\/+/, '');
  const candidate = path.join(DIST, rel);
  const file =
    candidate.endsWith('.html') || path.extname(candidate)
      ? candidate
      : path.join(DIST, 'index.html');
  if (!file.startsWith(DIST) || !fs.existsSync(file)) {
    return json(res, 404, { error: 'not found' });
  }
  res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
});

await new Promise((resolve) => server.listen(PORT, '127.0.0.1', resolve));

const results = [];
const browser = await chromium.launch({ headless: true });
let failures = 0;

try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await context.addInitScript(() => {
    class FakeWebSocket {
      constructor(url) {
        this.url = url;
        this.readyState = 0;
        this.onopen = null;
        this.onmessage = null;
        this.onclose = null;
        this.onerror = null;
        window.__wsSockets = window.__wsSockets || [];
        window.__wsSockets.push(this);
        setTimeout(() => {
          this.readyState = 1;
          if (this.onopen) this.onopen({});
        }, 0);
      }
      send() {}
      close() {
        this.readyState = 3;
      }
    }
    FakeWebSocket.CONNECTING = 0;
    FakeWebSocket.OPEN = 1;
    FakeWebSocket.CLOSING = 2;
    FakeWebSocket.CLOSED = 3;
    window.WebSocket = FakeWebSocket;
    window.__emitWs = (payload) => {
      const sockets = window.__wsSockets || [];
      const ws = sockets[sockets.length - 1];
      if (!ws || !ws.onmessage) throw new Error('no live websocket handler');
      ws.onmessage({ data: JSON.stringify(payload) });
    };
  });

  const page = await context.newPage();
  const pageErrors = [];
  const protectedRequests = [];
  page.on('pageerror', (e) => pageErrors.push(e.message));
  page.on('request', (r) => {
    if (/:(8767|8768)\b/.test(r.url())) protectedRequests.push(r.url());
  });

  await page.goto(`${BASE}/react/`, { waitUntil: 'domcontentloaded' });
  const card = page.locator('[data-session-card-id="A"]');
  await card.waitFor({ state: 'visible', timeout: 15000 });

  const dot = card.locator('span.rounded-full').first();
  const dotClass = async () => (await dot.getAttribute('class')) || '';

  async function waitForDot(expected, timeout) {
    const started = Date.now();
    while (Date.now() - started < timeout) {
      if ((await dotClass()).includes(expected)) return Date.now() - started;
      await new Promise((r) => setTimeout(r, 25));
    }
    throw new Error(
      `dot did not reach ${expected} within ${timeout}ms (class=${await dotClass()})`,
    );
  }

  async function runCase(name, body) {
    try {
      const detail = await body();
      results.push({ name, ok: true, detail });
    } catch (error) {
      failures += 1;
      results.push({ name, ok: false, detail: error.message });
    }
  }

  await runCase('baseline: a running worker renders as the accent dot', async () => {
    await waitForDot('bg-accent', 5000);
    return await dotClass();
  });

  await runCase('done event updates the indicator promptly (running -> idle)', async () => {
    sessionsState = [session('idle')];
    const emitAt = Date.now();
    await page.evaluate(() =>
      window.__emitWs({
        type: 'worker.result',
        sessionId: 'A',
        workerId: 'w1',
        status: 'done',
        result: 'finished',
      }),
    );
    const elapsed = await waitForDot('bg-success', 2000);
    assert.ok(elapsed < 1000, `indicator took ${elapsed}ms; expected < 1000ms`);
    return `settled ${elapsed}ms after the frame (${Date.now() - emitAt}ms wall)`;
  });

  await runCase(
    'focus recovery corrects a stale running dot after a missed terminal event',
    async () => {
      // A new turn starts: the backend pushes "running" and agrees via REST.
      sessionsState = [session('running')];
      await page.evaluate(() =>
        window.__emitWs({
          type: 'worker.status',
          sessionId: 'A',
          workerId: 'w1',
          status: 'running',
        }),
      );
      await waitForDot('bg-accent', 2000);

      // The completion event never reaches this client; only the authoritative
      // REST snapshot knows the worker settled. 'A' is also the most recently
      // touched session — the case the old global-counter guard shielded forever.
      sessionsState = [session('idle')];
      await page.evaluate(() => window.dispatchEvent(new Event('focus')));
      const elapsed = await waitForDot('bg-success', 5000);
      return `recovered ${elapsed}ms after focus`;
    },
  );

  await runCase('no requests to protected ports and no page errors', async () => {
    assert.deepEqual(
      protectedRequests,
      [],
      `unexpected protected requests: ${protectedRequests.join(', ')}`,
    );
    assert.deepEqual(pageErrors, [], `page errors: ${pageErrors.join(' | ')}`);
    return 'clean';
  });

  await context.close();
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}

const report = { port: PORT, results };
console.log(JSON.stringify(report, null, 2));
if (failures > 0) {
  console.error(`\n${failures} done-indicator E2E case(s) failed`);
  process.exit(1);
}
console.log('\ndone-indicator E2E passed');
