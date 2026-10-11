/* global process, console, fetch, performance, requestAnimationFrame */
// Real production UI/API, synthetic Sessions, owned port 5190. CDP shapes the
// actual HTTP traffic; no MockBackend and no production service are used.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import { spawn, spawnSync } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { chromium } from '@playwright/test';

const web = path.resolve(import.meta.dirname, '..');
const port = 5190;
const base = `http://127.0.0.1:${port}`;
const samples = Number(process.env.PAN_NETWORK_SAMPLES || 3);
const count = Number(process.env.PAN_NETWORK_SESSIONS || 200);
assert.ok(Number.isInteger(samples) && samples > 0 && samples <= 10);
assert.ok(Number.isInteger(count) && count > 0 && count <= 1000);
const output = path.join(web, 'test-results', `network-list-${Date.now()}`);
fs.mkdirSync(output, { recursive: true });
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith('PAN_')));
const python = process.env.PAN_E2E_PYTHON || 'D:/project/Pan/.venv/Scripts/python.exe';
const results = [];
let server;
let browser;
let log;
function stopServer() {
  if (server?.pid && server.exitCode === null) {
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    else server.kill();
  }
  server = null;
  if (log !== undefined) fs.closeSync(log);
  log = undefined;
}
try {
  await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', reject);
    probe.listen(port, '127.0.0.1', () => probe.close(resolve));
  });
  browser = await chromium.launch({ headless: true });
  for (const compression of [false, true]) {
    const runtime = path.join(output, compression ? 'compressed' : 'plain');
    fs.mkdirSync(runtime, { recursive: true });
    log = fs.openSync(path.join(runtime, 'server.log'), 'w');
    server = spawn(python, ['e2e/network-list.server.py'], {
      cwd: web, windowsHide: true, stdio: ['ignore', log, log],
      env: { ...env, PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime,
        PAN_NETWORK_COMPRESS: compression ? '1' : '0', PAN_NETWORK_SESSIONS: String(count) },
    });
    let ready = false;
    for (let i = 0; i < 200; i++) {
      if (server.exitCode !== null) throw new Error(`Server exited ${server.exitCode}: ${runtime}`);
      try {
        const response = await fetch(`${base}/api/sessions?summary=1`);
        const body = await response.text();
        ready = response.ok;
        if (ready) fs.writeFileSync(path.join(runtime, 'summary.json'), body);
      } catch { /* startup */ }
      if (ready) break;
      await sleep(100);
    }
    assert.ok(ready);
    for (const scenario of ['local-warm', 'network-warm', 'network-cold']) {
      for (let sample = 0; sample < samples; sample++) {
        const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
        const page = await context.newPage();
        const cdp = await context.newCDPSession(page);
        await cdp.send('Network.enable');
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        if (scenario !== 'network-cold') {
          await page.goto(`${base}/react/`, { waitUntil: 'networkidle' });
          await page.locator('[data-session-card-id]').first().waitFor();
        }
        if (scenario.startsWith('network')) await cdp.send('Network.emulateNetworkConditions', {
          offline: false, latency: 80, downloadThroughput: 125000, uploadThroughput: 125000,
        });
        const responses = [];
        const summaryIds = new Set();
        const wireResponses = [];
        cdp.on('Network.requestWillBeSent', event => {
          if (event.request.url.endsWith('/api/sessions?summary=1')) summaryIds.add(event.requestId);
        });
        cdp.on('Network.responseReceivedExtraInfo', event => {
          if (summaryIds.has(event.requestId)) wireResponses.push({ status: event.statusCode });
        });
        page.on('response', response => {
          if (response.url().endsWith('/api/sessions?summary=1')) responses.push(response);
        });
        await page.goto(`${base}/react/`, { waitUntil: 'domcontentloaded' });
        await page.locator('[data-session-card-id]').first().waitFor({ timeout: 30000 });
        const visibleMs = await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() =>
          requestAnimationFrame(() => resolve(performance.now())))));
        await page.waitForLoadState('networkidle');
        const requests = await page.evaluate(() => performance.getEntriesByType('resource')
          .filter(entry => entry.name.endsWith('/api/sessions?summary=1'))
          .map(entry => ({ startMs: entry.startTime, durationMs: entry.duration,
            transferBytes: entry.transferSize,
            encodedBytes: entry.encodedBodySize, decodedBytes: entry.decodedBodySize })));
        assert.ok(requests.length > 0);
        for (const response of responses) {
          const body = await response.json();
          assert.equal(body.sessions.length, count);
          assert.equal(response.headers()['content-encoding'] === 'gzip', compression);
        }
        assert.deepEqual(errors, []);
        if (compression && scenario !== 'network-cold') assert.ok(wireResponses.some(response => response.status === 304));
        results.push({ compression, scenario, sample, visibleMs, requests, wireResponses });
        console.log(JSON.stringify(results.at(-1)));
        if (scenario === 'network-warm') {
          const history = await page.evaluate(async () => {
            const { sessions } = await (await fetch('/api/sessions?summary=1')).json();
            const id = sessions.find(session => session.name === 'Network Session 0000').id;
            const url = `/api/sessions/${id}/history?limit=50`;
            const started = performance.now();
            const response = await fetch(url);
            const body = await response.json();
            const entry = performance.getEntriesByType('resource').filter(item => item.name.endsWith(url)).at(-1);
            return { elapsedMs: performance.now() - started, count: body.history.length,
              first: body.history[0].content, last: body.history.at(-1).content,
              total: body.total, encoding: response.headers.get('content-encoding'),
              cacheControl: response.headers.get('cache-control'), encodedBytes: entry.encodedBodySize,
              decodedBytes: entry.decodedBodySize };
          });
          assert.equal(history.count, 50);
          assert.equal(history.total, 50);
          assert.ok(history.first.startsWith('Network history row 0\n'));
          assert.ok(history.last.startsWith('Network history row 49\n'));
          assert.equal(history.encoding === 'gzip', compression);
          assert.equal(history.cacheControl, 'no-cache, no-store, must-revalidate');
          const { first, last, ...metrics } = history;
          assert.ok(first.length === last.length - 1);
          results.push({ compression, scenario: 'network-history', sample, ...metrics });
          console.log(JSON.stringify(results.at(-1)));
        }
        await context.close();
      }
    }
    stopServer();
    // A separate browser also retires the old server's HTTP connection pool.
    // This keeps the two transport variants isolated on Windows Chromium.
    await browser.close();
    browser = null;
    if (!compression) browser = await chromium.launch({ headless: true });
    await sleep(300);
  }
  fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ count, samples,
    network: { rttMs: 80, bytesPerSecond: 125000 }, results }, null, 2));
  console.log(`Evidence: ${output}`);
} finally {
  await browser?.close();
  stopServer();
}
