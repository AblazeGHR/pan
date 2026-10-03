/* global window, document, performance, requestAnimationFrame, PerformanceObserver, setTimeout, fetch, process, Event, console */
/** Isolated production-bundle benchmark; no Pan service or persisted data.
 * Run after `pnpm build`: node e2e/stream-runtime-benchmark.mjs
 * PAN_BENCH_DIST selects another build for the same browser fixture.
 * Covers streaming, inner code scrolling, loaded-window merging, cached
 * Session switching, and cold history clicks with a controlled 20ms HTTP wait.
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
  server = spawn(process.execPath, [vite, 'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort', ...(process.env.PAN_BENCH_DIST ? ['--outDir', path.resolve(process.env.PAN_BENCH_DIST)] : [])], {
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
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('request', request => {
    if (request.url().includes('/assets/')) assetRequests.push(request.url());
  });
  let coldRequests = 0;
  await page.route('**/api/sessions/perf-cold/history?**', async route => {
    coldRequests += 1;
    await sleep(20);
    await route.fulfill({ json: { history: Array.from({ length: 50 }, (_, index) => ({ role: index % 2 ? 'assistant' : 'user', content: `cold-history-${index}`, nativeItemId: `cold-${index}` })), total: 5000, start: 4950, hasMore: true, historyEpoch: 'cold', historyRevision: 1 } });
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
    const session = { ...store.getState().sessions[0], id: 'perf-session', history, historyTotal: history.length, workerStatus: 'running', lastLegalWorkerState: 'running', workerId: 'perf-worker', workerGeneration: 1, workerTaskSeq: 1 };
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
    const codeScroller = document.querySelector('[data-testid="large-code-window"]');
    const codeScrollStarted = performance.now();
    if (codeScroller) {
      codeScroller.scrollTop = codeScroller.scrollHeight;
      codeScroller.dispatchEvent(new Event('scroll', { bubbles: true }));
    }
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await new Promise(resolve => requestAnimationFrame(resolve));
      if (document.querySelector('main')?.textContent?.includes('item239')) break;
    }
    const codeScrollMs = performance.now() - codeScrollStarted;
    sampling = false;
    observer.disconnect();
    const streamElapsedMs = performance.now() - started;
    const finalLength = store.getState().currentMessages.at(-1)?.content.length;
    const tailVisible = document.querySelector('main')?.textContent?.includes('item239') ?? false;
    const clickTimes = [];
    for (let iteration = 0; iteration < 12; iteration += 1) {
      store.setState({ currentSessionId: null, currentMessages: [] });
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const started = performance.now();
      document.querySelector('[data-session-card-id="perf-session"]').click();
      for (let attempt = 0; attempt < 120; attempt += 1) {
        await new Promise(resolve => requestAnimationFrame(resolve));
        if (document.querySelector('main')?.textContent?.includes('history-4999')) break;
      }
      if (!document.querySelector('main')?.textContent?.includes('history-4999')) throw new Error('selected history missing');
      clickTimes.push(performance.now() - started);
    }
    const mergeTimes = [];
    store.getState().applyHistoryPage(session.id, { history, start: 0, total: 5000, hasMore: false, historyEpoch: 'bench', historyRevision: 1 });
    for (let iteration = 0; iteration < 60; iteration += 1) {
      const started = performance.now();
      store.getState().applyHistoryPage(session.id, { history: history.slice(-50), start: 4950, total: 5000, hasMore: true, historyEpoch: 'bench', historyRevision: iteration + 2 });
      mergeTimes.push(performance.now() - started);
    }
    // The mock demo wraps fetch. A blank frame supplies native fetch for this
    // one intercepted HTTP fixture; all other requests stay in the demo.
    const nativeFrame = document.createElement('iframe');
    nativeFrame.style.display = 'none';
    document.body.append(nativeFrame);
    const nativeFetch = nativeFrame.contentWindow.fetch.bind(nativeFrame.contentWindow);
    const demoFetch = window.fetch;
    window.fetch = (input, init) => String(input).includes('/api/sessions/perf-cold/history')
      ? nativeFetch(input, init) : demoFetch(input, init);
    const coldClickTimes = [];
    for (let iteration = 0; iteration < 8; iteration += 1) {
      const coldSession = { ...session, id: 'perf-cold', history: [], historyTotal: 5000, historyTruncated: true };
      store.setState({ sessions: [coldSession], currentSessionId: null, currentMessages: [], sessionTranscripts: {}, liveStreamBuffers: {} });
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const started = performance.now();
      document.querySelector('[data-session-card-id="perf-cold"]').click();
      for (let attempt = 0; attempt < 120; attempt += 1) {
        await new Promise(resolve => requestAnimationFrame(resolve));
        if (document.querySelector('main')?.textContent?.includes('cold-history-49') && !store.getState().initialLoading) break;
      }
      if (!document.querySelector('main')?.textContent?.includes('cold-history-49')) throw new Error('cold history missing');
      coldClickTimes.push(performance.now() - started);
    }
    window.fetch = demoFetch;
    nativeFrame.remove();
    const percentile = (samples, fraction) => {
      const sorted = samples.toSorted((left, right) => left - right);
      return sorted[Math.floor((sorted.length - 1) * fraction)] ?? 0;
    };
    return {
      elapsedMs: streamElapsedMs,
      clickMedianMs: percentile(clickTimes, .5), clickP95Ms: percentile(clickTimes, .95),
      mergeMedianMs: percentile(mergeTimes, .5), mergeP95Ms: percentile(mergeTimes, .95),
      coldClickMedianMs: percentile(coldClickTimes, .5), coldClickP95Ms: percentile(coldClickTimes, .95),
      frameCount: frames.length,
      p95FrameMs: percentile(frames, .95),
      maxFrameMs: Math.max(...frames),
      longTasks: longTasks.length,
      maxLongTaskMs: Math.max(0, ...longTasks),
      p95StoreMs: percentile(storeTimes, .95),
      finalLength,
      tailVisible, codeScrollMs,
    };
  });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('HeapProfiler.collectGarbage');
  measurement.retainedHeapBytes = (await cdp.send('Runtime.getHeapUsage')).usedSize;
  measurement.coldHistoryRequests = coldRequests;
  measurement.domNodes = await page.locator('*').count();
  await cdp.detach();
  assert.equal(coldRequests, 8, 'one history request per cold click');
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
  const importDialog = page.getByRole('dialog', { name: 'Import Session' });
  await importDialog.waitFor({ state: 'visible' });
  await importDialog.getByRole('button', { name: 'Close' }).click();

  await page.getByRole('link', { name: 'Jobs' }).click();
  await page.getByRole('heading', { name: 'Jobs', exact: true }).waitFor({ state: 'visible' });
  assert.ok(page.url().endsWith('/react/jobs'), 'Jobs route opened');
  assert.ok(assetRequests.some(url => /JobsView-.*\.js$/.test(url)),
    'Jobs chunk loads on first navigation');
  assert.deepEqual(pageErrors, [], 'lazy routes raised no browser error');
  console.log('PASS lazy sidebar modals and Jobs route open in Chromium');
} finally {
  await browser?.close();
  if (server && server.exitCode === null) {
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true });
    else server.kill('SIGTERM');
  }
}
