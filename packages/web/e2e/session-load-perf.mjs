/**
 * Load-perf measurement for the dashboard boot → session list → history path.
 *
 * Measures what a user perceives on the production bundle with real HTTP/WS
 * from a real Pan process: time until the Session list is on screen, time until
 * the first history rows of a clicked Session are readable, and the request
 * count/bytes each phase costs. Runs against an already-running isolated Pan.
 *
 * Usage:
 *   node e2e/session-load-perf.mjs <baseUrl> <outJson> [--runs=3] [--diag]
 *
 * Scenarios (each repeated --runs times, raw samples kept):
 *   cold-fast  fresh browser cache, no throttling
 *   warm-fast  second navigation with the browser HTTP cache populated
 *   cold-weak  fresh browser cache + emulated weak network
 *   warm-weak  second navigation with browser cache + emulated weak network
 *
 * Weak network is Chromium CDP emulation: 150 ms RTT, 1.6 Mbit/s down,
 * 750 kbit/s up. It is real browser network throttling, not an injected delay.
 *
 * Every run keeps one request timeline from navigation start; the phase marks
 * (list visible / history rows readable) refer to the same clock, so the
 * waterfall shows what is still in flight when the user sees data.
 */

import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const BASE = (process.argv[2] || '').replace(/\/$/, '');
const OUT = process.argv[3];
const RUNS = Number((process.argv.find(a => a.startsWith('--runs=')) || '--runs=3').split('=')[1]);
const DIAG = process.argv.includes('--diag');

if (!BASE || !OUT) {
  console.error('usage: node e2e/session-load-perf.mjs <baseUrl> <outJson> [--runs=N] [--diag]');
  process.exit(2);
}

const THROTTLE = {
  offline: false,
  latency: 150,
  downloadThroughput: Math.round(1.6 * 1024 * 1024 / 8), // 1.6 Mbit/s
  uploadThroughput: Math.round(750 * 1024 / 8),          // 750 kbit/s
};

const LIST_SELECTOR = '[data-session-card-id]';
const ROW_SELECTOR = '[data-scroll-anchor-key]';
const STANDARD_SESSION = 'ses-perf-00';
const LARGE_SESSION = 'ses-perf-large';

async function attachRecorder(page) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  const byId = new Map();
  cdp.on('Network.requestWillBeSent', (e) => {
    byId.set(e.requestId, {
      url: e.request.url.split('?')[0] + (e.request.url.includes('?') ? '?' + e.request.url.split('?')[1] : ''),
      type: e.type,
      method: e.request.method,
      startedAt: Date.now(),
    });
  });
  cdp.on('Network.responseReceived', (e) => {
    const row = byId.get(e.requestId);
    if (!row) return;
    row.status = e.response.status;
    row.mime = e.response.mimeType;
    row.fromDiskCache = Boolean(e.response.fromDiskCache);
    row.wireBytes = e.response.encodedDataLength;
  });
  cdp.on('Network.loadingFinished', (e) => {
    const row = byId.get(e.requestId);
    if (!row) return;
    row.wireBytes = e.encodedDataLength;
    row.finishedAt = Date.now();
  });
  cdp.on('Network.loadingFailed', (e) => {
    const row = byId.get(e.requestId);
    if (!row) return;
    row.failed = e.errorText;
    row.finishedAt = Date.now();
  });
  return {
    rows: () => [...byId.values()],
    /**
     * Offsets are milliseconds since the page's navigation start (the same
     * clock as performance.now()), so request rows and DOM marks line up.
     */
    snapshot(wallOrigin, timeOriginWall) {
      return [...byId.values()]
        .map(r => ({
          url: r.url.replace(BASE, ''),
          type: r.type,
          method: r.method,
          status: r.status ?? null,
          bytes: r.wireBytes ?? 0,
          cached: Boolean(r.fromDiskCache),
          failed: r.failed ?? null,
          startMs: r.startedAt - timeOriginWall,
          endMs: r.finishedAt ? r.finishedAt - timeOriginWall : null,
        }))
        .sort((a, b) => a.startMs - b.startMs);
    },
  };
}

const summarize = (rows, from, to) => {
  const scoped = rows.filter(r => r.startMs >= from && (to === null || r.startMs < to));
  return {
    count: scoped.length,
    bytes: scoped.reduce((sum, r) => sum + (r.bytes || 0), 0),
    scriptCount: scoped.filter(r => r.type === 'Script').length,
    tinyCount: scoped.filter(r => (r.bytes || 0) < 3000 && r.type === 'Script').length,
    apiCount: scoped.filter(r => r.url.startsWith('/api/')).length,
    thirdParty: scoped.filter(r => !r.url.startsWith('/') && !r.url.includes('127.0.0.1'))
      .map(r => `${r.url.split('?')[0]}:${r.bytes}`),
    failed: scoped.filter(r => r.failed).map(r => r.url),
  };
};

/**
 * In-page marks. Timestamps are performance.now() values, i.e. milliseconds
 * since navigation start, so they already include the document round trip.
 * Observing DOM mutations is what makes "readable" mean "the row exists with
 * its text", not "Playwright's click call returned".
 */
const IN_PAGE_MARKS = (tags) => {
  const marks = { cards: null, rows: {}, clicks: {} };
  window.__perfMarks = marks;
  const scanRows = () => {
    const pending = marks.pending;
    if (!pending || marks.rows[pending] != null) return;
    const nodes = document.querySelectorAll('[data-scroll-anchor-key]');
    for (const node of nodes) {
      if ((node.textContent || '').includes(pending)) {
        marks.rows[pending] = performance.now();
        return;
      }
    }
  };
  const observer = new MutationObserver(() => {
    if (marks.cards == null && document.querySelector('[data-session-card-id]')) {
      marks.cards = performance.now();
    }
    scanRows();
  });
  observer.observe(document, { childList: true, subtree: true, characterData: true });
};

const TAGS = {
  [STANDARD_SESSION]: `${STANDARD_SESSION}/`,
  [LARGE_SESSION]: `${LARGE_SESSION}/`,
};

async function clickSession(page, sessionId) {
  const tag = TAGS[sessionId];
  const clickAt = await page.evaluate(({ id, tag: marker }) => {
    const node = document.querySelector(`[data-session-card-id="${id}"]`);
    if (!node) throw new Error(`session card not found: ${id}`);
    window.__perfMarks.pending = marker;
    window.__perfMarks.rows[marker] = undefined;
    const at = performance.now();
    window.__perfMarks.clicks[marker] = at;
    node.click();
    return at;
  }, { id: sessionId, tag });
  await page.waitForFunction(
    (marker) => window.__perfMarks.rows[marker] != null,
    tag,
    { timeout: 40000, polling: 16 },
  );
  const rowsAt = await page.evaluate((marker) => {
    window.__perfMarks.pending = null;
    return window.__perfMarks.rows[marker];
  }, tag);
  return { clickAt, rowsAt, ms: Math.max(0, rowsAt - clickAt) };
}

async function paintMarks(page) {
  return page.evaluate(() => {
    const paints = performance.getEntriesByType('paint') || [];
    const fcp = paints.find(p => p.name === 'first-contentful-paint');
    const lcpEntries = performance.getEntriesByType('largest-contentful-paint') || [];
    const nav = performance.getEntriesByType('navigation')[0];
    return {
      fcpMs: fcp ? Math.round(fcp.startTime) : null,
      lcpMs: lcpEntries.length ? Math.round(lcpEntries[lcpEntries.length - 1].startTime) : null,
      domContentLoadedMs: nav ? Math.round(nav.domContentLoadedEventEnd) : null,
      loadMs: nav ? Math.round(nav.loadEventEnd) : null,
    };
  });
}

async function measure(page, recorder, { thinkMs = 0, quick = false, listTimeout = 60000 } = {}) {
  await page.goto(`${BASE}/react/`, { waitUntil: 'commit', timeout: 60000 });
  let listMs = null;
  try {
    await page.waitForFunction(() => window.__perfMarks && window.__perfMarks.cards != null,
                               null, { timeout: listTimeout, polling: 16 });
    listMs = await page.evaluate(() => window.__perfMarks.cards);
  } catch {
    // The list never became available in the budget (e.g. a render-blocking
    // third-party stylesheet that never answers).
  }
  await page.waitForSelector(LIST_SELECTOR, { state: 'visible', timeout: listTimeout }).catch(() => {});
  if (listMs === null) {
    const timeOriginWall = await page.evaluate(() => Date.now() - performance.now());
    return {
      paints: await paintMarks(page),
      marks: { listMs: null },
      listMs: null,
      historyMs: null,
      largeHistoryMs: null,
      switchBackMs: null,
      phases: { nav: summarize(recorder.snapshot(timeOriginWall, timeOriginWall), 0, Infinity) },
      timeline: recorder.snapshot(timeOriginWall, timeOriginWall),
    };
  }
  if (thinkMs > 0) await page.waitForTimeout(thinkMs);

  const historyStep = await clickSession(page, STANDARD_SESSION);
  const largeStep = quick ? null : await clickSession(page, LARGE_SESSION);
  const backStep = quick ? null : await clickSession(page, STANDARD_SESSION);

  // Wall clock of performance.now()===0 for this document, for timeline alignment.
  const timeOriginWall = await page.evaluate(() => Date.now() - performance.now());
  const paints = await paintMarks(page);
  const timeline = recorder.snapshot(timeOriginWall, timeOriginWall);
  return {
    paints,
    marks: { listMs, history: historyStep, large: largeStep, back: backStep },
    listMs,
    historyMs: historyStep ? historyStep.ms : null,
    largeHistoryMs: largeStep ? largeStep.ms : null,
    switchBackMs: backStep ? backStep.ms : null,
    phases: {
      nav: summarize(timeline, 0, Math.ceil(listMs)),
      history: historyStep ? summarize(timeline, historyStep.clickAt, historyStep.rowsAt) : null,
      largeHistory: largeStep ? summarize(timeline, largeStep.clickAt, largeStep.rowsAt) : null,
      switchBack: backStep ? summarize(timeline, backStep.clickAt, backStep.rowsAt) : null,
    },
    timeline,
  };
}

// Records which client code issued each API read (diagnostic runs only).
const FETCH_DIAG_SCRIPT = () => {
  const calls = [];
  const original = window.fetch;
  window.fetch = function patched(input, init) {
    try {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      if (url.includes('/api/sessions') || url.includes('/api/list')) {
        const stack = (new Error().stack || '').split('\n').slice(1, 5)
          .map(line => line.replace(/^\\s+at\\s+/, '').slice(0, 160));
        calls.push({ url, at: Math.round(performance.now()), stack });
      }
    } catch { /* diagnostics must never break the app */ }
    return original.call(this, input, init);
  };
  window.__perfFetchDiag = calls;
};

async function runScenario(browser, scenario) {
  const { throttle, warm } = scenario;
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    serviceWorkers: 'block',
  });
  try {
    const page = await context.newPage();
    const tags = [TAGS[STANDARD_SESSION], TAGS[LARGE_SESSION]];
    await page.addInitScript(IN_PAGE_MARKS, tags);
    if (DIAG) await page.addInitScript(FETCH_DIAG_SCRIPT);
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    if (throttle) await cdp.send('Network.emulateNetworkConditions', THROTTLE);
    await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });

    if (warm) {
      await cdp.send('Network.setCacheDisabled', { cacheDisabled: false });
      await page.goto(`${BASE}/react/`, { waitUntil: 'load', timeout: 60000 });
      await page.waitForSelector(LIST_SELECTOR, { state: 'visible', timeout: 60000 });
      await page.waitForTimeout(600);
    }
    const recorder = await attachRecorder(page);
    const result = await measure(page, recorder, scenario);
    if (DIAG) result.fetchDiag = await page.evaluate(() => window.__perfFetchDiag || []);
    return result;
  } finally {
    await context.close();
  }
}

const scenarios = [
  { name: 'cold-fast', throttle: false, warm: false },
  { name: 'warm-fast', throttle: false, warm: true },
  { name: 'cold-weak', throttle: true, warm: false },
  { name: 'warm-weak', throttle: true, warm: true },
  // Worst case (click the instant the cards appear) is above; this one waits a
  // realistic think time, which is what a deferred chat-graph download needs.
  { name: 'cold-weak-think', throttle: true, warm: false, thinkMs: 1200, quick: true },
];

const browser = await chromium.launch({ headless: true });
const result = { baseUrl: BASE, runs: RUNS, throttle: THROTTLE, scenarios: {} };
try {
  for (const scenario of scenarios) {
    const samples = [];
    for (let run = 0; run < RUNS; run += 1) {
      samples.push(await runScenario(browser, scenario));
    }
    result.scenarios[scenario.name] = samples;
  }
} finally {
  await browser.close();
}

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(result, null, 2), 'utf-8');
const summary = {};
for (const [name, samples] of Object.entries(result.scenarios)) {
  summary[name] = {
    listMs: samples.map(s => s.listMs),
    historyMs: samples.map(s => s.historyMs),
    largeHistoryMs: samples.map(s => s.largeHistoryMs),
    switchBackMs: samples.map(s => s.switchBackMs),
    navRequests: samples.map(s => s.phases.nav.count),
    navBytes: samples.map(s => s.phases.nav.bytes),
  };
}
console.log(JSON.stringify({ out: OUT, scenarios: summary }, null, 2));
