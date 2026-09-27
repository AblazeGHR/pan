/* global window, document, performance, requestAnimationFrame, PerformanceObserver, setTimeout, fetch, process, Event, console */
/** Isolated production-bundle benchmark; no Pan service or persisted data.
 * Run after `pnpm build`: node e2e/stream-runtime-benchmark.mjs
 */
import assert from 'node:assert/strict';
import net from 'node:net';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { chromium } from '@playwright/test';

const port = 5184;
const base = `http://127.0.0.1:${port}/react/?mock=1&panE2E=1`;
const root = path.resolve(import.meta.dirname, '..');
const vite = path.join(root, 'node_modules', 'vite', 'bin', 'vite.js');
let server;
let browser;

async function assertFreePort() {
  await new Promise((resolve, reject) => {
    const socket = net.createServer();
    socket.once('error', reject);
    socket.listen(port, '127.0.0.1', () => socket.close(resolve));
  });
}

try {
  await assertFreePort();
  server = spawn(process.execPath, [vite, 'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: root, windowsHide: true, stdio: 'ignore',
  });
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (server.exitCode !== null) throw new Error(`Vite preview exited: ${server.exitCode}`);
    try {
      const response = await fetch(base);
      ready = response.ok;
      if (ready) break;
    } catch { /* still starting */ }
    await sleep(100);
  }
  assert.ok(ready, 'owned Vite preview became ready');
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const assetRequests = [];
  page.on('request', request => {
    if (request.url().includes('/assets/')) assetRequests.push(request.url());
  });
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.locator('[data-session-card-id]').first().waitFor();
  assert.equal(assetRequests.filter(url => /AppSettingsModal-.*\.js$/.test(url)).length, 0,
    'settings modal is absent from initial requests');
  const measurement = await page.evaluate(async () => {
    const store = window.__panSessionStore;
    const history = Array.from({ length: 5000 }, (_, index) => ({
      role: index % 2 ? 'assistant' : 'user',
      content: `history-${index}`,
      nativeItemId: `history-${index}`,
    }));
    const session = { ...store.getState().sessions[0], id: 'perf-session', history, historyTotal: history.length };
    store.setState({
      sessions: [session], currentSessionId: session.id, currentMessages: history,
      sessionTranscripts: {}, liveStreamBuffers: {}, initialLoading: false,
      historyLoadEnd: 0, hasMoreMessages: false,
    });
    await new Promise(resolve => setTimeout(resolve, 500));
    const scroller = document.querySelector('main div.overflow-auto');
    if (!scroller) throw new Error('chat scroller missing');
    scroller.scrollTop = scroller.scrollHeight;
    scroller.dispatchEvent(new Event('scroll', { bubbles: true }));
    await new Promise(resolve => setTimeout(resolve, 200));
    const frames = [];
    const longTasks = [];
    let sampling = true;
    let previousFrame = performance.now();
    const tick = now => {
      if (!sampling) return;
      frames.push(now - previousFrame);
      previousFrame = now;
      requestAnimationFrame(tick);
    };
    const observer = new PerformanceObserver(list => {
      longTasks.push(...list.getEntries().map(entry => entry.duration));
    });
    observer.observe({ type: 'longtask' });
    requestAnimationFrame(tick);
    const storeTimes = [];
    let content = '```typescript\n';
    const started = performance.now();
    for (let index = 0; index < 240; index += 1) {
      content += `const item${index} = { value: ${index}, text: 'streamed markdown and syntax highlighting' };\n`;
      const before = performance.now();
      store.getState().applyLiveStream(session.id, [{
        role: 'assistant', content, nativeItemId: 'perf-live-item',
      }], { workerId: 'perf-worker', generation: 1, taskSeq: 1 });
      storeTimes.push(performance.now() - before);
      await new Promise(resolve => setTimeout(resolve, 5));
    }
    await new Promise(resolve => setTimeout(resolve, 300));
    sampling = false;
    observer.disconnect();
    const percentile = (samples, fraction) => {
      const sorted = samples.toSorted((left, right) => left - right);
      return sorted[Math.floor((sorted.length - 1) * fraction)] ?? 0;
    };
    return {
      elapsedMs: performance.now() - started,
      frameCount: frames.length,
      p95FrameMs: percentile(frames, .95),
      maxFrameMs: Math.max(...frames),
      longTasks: longTasks.length,
      maxLongTaskMs: Math.max(0, ...longTasks),
      p95StoreMs: percentile(storeTimes, .95),
      finalLength: store.getState().currentMessages.at(-1)?.content.length,
      tailVisible: document.querySelector('main')?.textContent?.includes('item239') ?? false,
    };
  });
  console.log(JSON.stringify(measurement, null, 2));
  assert.ok(measurement.finalLength > 10000, 'all cumulative deltas reached the store');
  assert.ok(measurement.tailVisible, 'streaming Markdown was visible during the benchmark');

  await page.getByTitle('App settings').click();
  const settings = page.getByRole('dialog', { name: 'App Settings' });
  await settings.waitFor({ state: 'visible' });
  assert.ok(assetRequests.some(url => /AppSettingsModal-.*\.js$/.test(url)),
    'settings chunk loads on first open');
  await settings.getByRole('button', { name: 'Close' }).click();

  await page.getByTitle('New with settings').click();
  const newSession = page.getByRole('dialog', { name: 'New Session' });
  await newSession.waitFor({ state: 'visible' });
  await newSession.getByRole('button', { name: 'Close' }).click();

  await page.getByTitle('Import session').click();
  await page.getByRole('dialog', { name: 'Import Session' }).waitFor({ state: 'visible' });
  console.log('PASS lazy sidebar modals open in Chromium');
} finally {
  await browser?.close();
  if (server && server.exitCode === null) {
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true });
    else server.kill('SIGTERM');
  }
}
