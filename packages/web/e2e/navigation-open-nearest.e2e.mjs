/* global window, document, performance, PerformanceObserver, requestAnimationFrame, process, fetch, URL, console */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import { spawn, execFileSync } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';

const root = path.resolve(process.argv[3] || path.resolve(import.meta.dirname, '../../..'));
const mode = process.argv[2] || 'fixed';
const pagingOnly = mode.startsWith('paging');
const output = path.join(root, 'packages/web/test-results', `navigation-open-${mode}-${Date.now()}`);
const runtime = await fs.mkdtemp(path.join(os.tmpdir(), 'pan-nav-browser-'));
await fs.mkdir(output, { recursive: true });
const port = await new Promise(resolve => {
  const probe = net.createServer();
  probe.listen(0, '127.0.0.1', () => { const p = probe.address().port; probe.close(() => resolve(p)); });
});
assert.ok(![8767, 8768].includes(port));
const base = `http://127.0.0.1:${port}`;
const python = process.env.PAN_E2E_PYTHON || 'D:/project/Pan-main/.venv/Scripts/python.exe';
const server = spawn(python, [path.join(import.meta.dirname, 'navigationServer.py')], {
  cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime, PAN_NAV_CHECKOUT: root },
});
let logs = '';
server.stdout.on('data', value => { logs += value; });
server.stderr.on('data', value => { logs += value; });
const report = { mode, sha: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root }).toString().trim(),
  root, runtime, port, scenarios: [], errors: [], console: [], protectedRequests: [], passed: false };
let browser, serverProcessIdentity;
async function poll(read, check, timeout = 15000) {
  for (const end = Date.now() + timeout; Date.now() < end;) {
    const value = await read(); if (check(value)) return value; await delay(40);
  }
  throw new Error('Condition timed out');
}
async function snapshot(page) {
  return page.evaluate(() => {
    const store = window.__panSessionStore.getState();
    const transcript = store.sessionTranscripts[store.currentSessionId];
    const el = document.querySelector('.chat-view-stage .overflow-auto');
    const bounds = el.getBoundingClientRect();
    const visible = [...el.querySelectorAll('[data-scroll-anchor-key][data-index]')].filter(row => {
      const r = row.getBoundingClientRect(); return r.bottom > bounds.top && r.top < bounds.bottom;
    });
    const indices = [...transcript.window.rows].filter(([, m]) => visible.some(row => row.dataset.scrollAnchorKey.includes(`:${m.messageId}:`))).map(([i]) => i);
    const start = Math.min(...indices), end = Math.max(...indices), center = (start + end) / 2;
    // Fixture every tenth row is navigable; the rest are ordinary assistant text.
    const step = window.__navCandidateStep ?? 10;
    const candidates = window.__navExpectedCandidates ?? Array.from({ length: Math.ceil(transcript.window.total / step) }, (_, i) => i * step);
    const distance = i => Math.max(start - i, i - end, 0);
    candidates.sort((a, b) => distance(a) - distance(b) || Math.abs(a - center) - Math.abs(b - center) || b - a);
    const selected = document.querySelector('.message-navigation-marker.is-viewport-target');
    const list = document.querySelector('.message-navigation-list');
    const sr = selected?.getBoundingClientRect(), lr = list?.getBoundingClientRect();
    return { start, end, expected: candidates[0], scrollTop: el.scrollTop, selected: selected ? transcript.window.total - 1 - Number(selected.dataset.fromEnd) : null,
      visible: Boolean(sr && lr && sr.bottom > lr.top && sr.top < lr.bottom),
      height: el.clientHeight, contentHeight: el.scrollHeight, firstRowTop: visible[0]?.getBoundingClientRect().top - bounds.top,
      indexStatus: document.querySelector('.message-navigation-rail')?.dataset.indexStatus,
      railTop: list?.scrollTop, metrics: window.__navMetrics };
  });
}
async function seed(context, sessionId, count) {
  const messages = Array.from({ length: count }, (_, i) => ({ role: count === 10000 || i % 10 === 0 ? 'user' : 'assistant',
    content: `NAV ${i} ` + 'canonical transcript paragraph '.repeat(8) }));
  assert.equal((await context.request.post(`${base}/__e2e/append-history`, { data: { sessionId, messages } })).status(), 200);
}
try {
  await poll(async () => { try { return (await fetch(`${base}/api/sessions?summary=1`)).ok; } catch { return false; } }, Boolean);
  serverProcessIdentity = await (await fetch(`${base}/__e2e/identity`)).json();
  browser = await chromium.launch({ headless: true });
  report.chromium = browser.version();
  const api = await browser.newContext();
  const sessions = (await (await api.request.get(`${base}/api/sessions?summary=1`)).json()).sessions;
  const small = sessions.find(s => s.name === 'Alpha Session'), large = sessions.find(s => s.name === 'Bravo Session'), fault = sessions.find(s => s.name === 'Charlie Session');
  const dense = sessions.find(s => s.name === 'Dense Navigation');
  await seed(api, dense.id, 10000);
  await seed(api, small.id, 200); await seed(api, large.id, 6000);
  const faultMessages = Array.from({ length: 600 }, (_, i) => ({ role: [545, 580].includes(i) ? 'user' : 'assistant', content: (i === 545 ? '@@@@by qq: fixture\n' : '') + `FAULT ${i} ` + 'canonical paragraph '.repeat(8) }));
  await api.request.post(`${base}/__e2e/append-history`, { data: { sessionId: fault.id, messages: faultMessages } });
  await api.request.put(`${base}/api/settings/ui`, { data: { showMessageNavigationRail: true, mergeConsecutiveNonBodyBlocks: false,
    showTaskAgent: true, showMetaAgent: true, keepScrollOnSessionSwitch: true, historyPageSize: 200 } });
  await api.close();
  for (const [session, count, fraction, coldWindow = false] of (mode === 'faults' || pagingOnly ? [] : [[dense, 10000, 0.5], [large, 6000, 0.5], [large, 6000, 0], [large, 6000, 1], [small, 200, 0.5], [small, 200, 0], [small, 200, 1], ...(mode === 'baseline' ? [] : [[large, 6000, 1, true]])])) {
    const context = await browser.newContext({ viewport: { width: 1120, height: 900 } });
    await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
    const page = await context.newPage();
    page.on('pageerror', error => report.errors.push(error.message));
    page.on('request', request => { if (/:(8767|8768)(\/|$)/.test(request.url())) report.protectedRequests.push(request.url()); });
    page.on('console', message => { if (['error', 'warning'].includes(message.type())) report.console.push({ type: message.type(), text: message.text() }); });
    await page.addInitScript(() => {
      window.__navMetrics = { longTasks: [], frames: [] };
      new PerformanceObserver(list => window.__navMetrics.longTasks.push(...list.getEntries().map(e => ({ start: e.startTime, duration: e.duration })))).observe({ type: 'longtask', buffered: true });
    });
    await page.goto(`${base}/react/?panE2E=1`);
    if (count === 10000) await page.evaluate(() => { window.__navCandidateStep = 1; });
    await page.locator('[data-session-card-id]').filter({ hasText: session.name }).first().click();
    await page.waitForFunction(() => window.__panSessionStore.getState().currentMessages.length > 0 && !window.__panSessionStore.getState().historyLoading);
    if (!coldWindow) await page.evaluate(async count => {
      const s = window.__panSessionStore.getState();
      await s.ensureMessageLoaded(count - 1, count);
    }, count);
    const scroller = page.locator('.chat-view-stage .overflow-auto').first();
    await scroller.hover(); await page.mouse.wheel(0, -600); await delay(200);
    await scroller.evaluate((el, fraction) => { el.scrollTop = (el.scrollHeight - el.clientHeight) * fraction; }, fraction);
    await delay(700);
    // Set up a stable reading viewport before measuring the opening itself.
    // This is test fixture settling, never a product-side wait.
    let previousGeometry = '', stableSince = Date.now();
    await poll(async () => {
      const geometry = await scroller.evaluate(el => JSON.stringify([el.scrollTop, el.scrollHeight, el.clientHeight]));
      if (geometry !== previousGeometry) { previousGeometry = geometry; stableSince = Date.now(); }
      return Date.now() - stableSince;
    }, stableMs => stableMs >= 650, 5000);
    const before = await snapshot(page);
    assert.ok(Number.isFinite(before.start));
    const requests = [];
    let bytes = 0;
    page.on('response', async r => { if (r.url().includes('/history?')) { try { bytes += (await r.body()).length; } catch { /* Request may have been cancelled with its context. */ } } });
    await page.route('**/api/sessions/*/history?*', async route => { requests.push(route.request().url()); await delay(180); await route.continue(); });
    await page.evaluate(() => {
      window.__navMetrics.longTasks = [];
      const start = performance.now(); let last = start;
      function frame(now) { window.__navMetrics.frames.push(now - last); last = now; if (now - start < 2500) requestAnimationFrame(frame); }
      requestAnimationFrame(frame);
    });
    const started = performance.now();
    await page.locator('.message-navigation-dock__handle').hover();
    let selected = null;
    try { selected = await poll(() => snapshot(page), s => s.selected === s.expected && s.visible, 3500); } catch { /* Request may have been cancelled with its context. */ }
    const firstMs = selected ? performance.now() - started : null;
    if (mode !== 'baseline') await delay(350);
    const after = await snapshot(page);
    const scenario = { name: session.name, count, fraction, coldWindow, loadedRows: await page.evaluate(() => window.__panSessionStore.getState().sessionTranscripts[window.__panSessionStore.getState().currentSessionId].window.rows.size), before, after, firstMs, requests: requests.length, bytes, selectedCorrect: after.selected === before.expected, bodyDelta: after.scrollTop - before.scrollTop };
    report.scenarios.push(scenario);
    await fs.writeFile(path.join(output, "evidence.json"), JSON.stringify(report, null, 2));
    await page.screenshot({ path: path.join(output, `${count}-${fraction}${coldWindow ? "-cold" : ""}-open.png`) });
    if (mode !== 'baseline') {
      assert.ok(selected, JSON.stringify(scenario)); assert.ok(Math.abs(scenario.bodyDelta) <= 1, 'opening must not move body');
      assert.ok(Number(await page.locator('.message-navigation-rail').getAttribute('data-rendered-targets')) <= 200, 'bounded marker DOM');
      assert.ok(firstMs < 250, 'local canonical first location budget 250ms, including hover and observation overhead');
      scenario.maxFrameMs = Math.max(0, ...after.metrics.frames);
      scenario.longTasks = after.metrics.longTasks;
      assert.ok(scenario.maxFrameMs < 50, 'opening frame gap must stay below the 50ms acceptance budget');
      assert.equal(scenario.longTasks.length, 0, 'no browser-reported long task in the opening observation window');
      await page.mouse.move(5, 5); await delay(160);
      const cachedRequests = requests.length, reopened = performance.now();
      await page.locator('.message-navigation-dock__handle').hover();
      await poll(() => snapshot(page), s => s.selected === s.expected && s.visible);
      scenario.cachedMs = performance.now() - reopened;
      scenario.cachedRequests = requests.length - cachedRequests;
      assert.equal(scenario.cachedRequests, 0);
      assert.ok(scenario.cachedMs < 250, 'cached expansion budget 250ms');
    }
    await context.tracing.stop({ path: path.join(output, `${count}-${fraction}${coldWindow ? "-cold" : ""}-trace.zip`) });
    await context.close();
  }


  if (pagingOnly || mode === 'fixed') {
    const sparse = sessions.find(session => session.name === 'Sparse Navigation');
    const sparseOffsets = [0,4800,9600,14400,19200,23999];
    await apiSeedSparse();
    async function apiSeedSparse() {
      const context = await browser.newContext();
      await context.request.post(`${base}/__e2e/append-history`, { data: { sessionId: sparse.id, messages: Array.from({ length: 24000 }, (_, i) => ({ role: sparseOffsets.includes(i) ? 'user' : 'assistant', content: `SPARSE ${i}` })) } });
      await context.close();
    }
    for (const [session,total,expected] of [[dense,10000,Array.from({ length: 10000 }, (_,i) => i)],[sparse,24000,sparseOffsets]]) {
      const context = await browser.newContext({ viewport: { width: 1120, height: 900 } });
      await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
      const scenario = { test: 'continuous-paging', session: session.name, total, expectedCount: expected.length, pages: [], batches: [], requests: [], bytes: 0 };
      report.scenarios.push(scenario);
      try {
        // Independent real-API identity oracle. Preparation reads are separate
        // from application navigation request/byte measurements below.
        const identities = new Map();
        if (session === dense) {
          for (let before = 200; before <= total; before += 200) {
            const response = await context.request.get(`${base}/api/sessions/${session.id}/history?before=${before}&limit=200`);
            const body = await response.json();
            body.history.forEach((message,i) => identities.set(body.start+i,message.messageId));
          }
        } else {
          for (const offset of expected) {
            const response = await context.request.get(`${base}/api/sessions/${session.id}/history?before=${offset+1}&limit=1`);
            const body = await response.json(); identities.set(offset,body.history[0].messageId);
          }
        }
        const page = await context.newPage();
        page.on('pageerror', error => report.errors.push(error.message));
        page.on('request', request => { if (/:(8767|8768)(\/|$)/.test(request.url())) report.protectedRequests.push(request.url()); });
        await page.goto(`${base}/react/?panE2E=1`);
        await page.locator('[data-session-card-id]').filter({ hasText: session.name }).first().click();
        await page.waitForFunction(() => window.__panSessionStore.getState().currentMessages.length > 0 && !window.__panSessionStore.getState().historyLoading);
        if (session === sparse) await page.evaluate(() => window.__panSessionStore.getState().ensureMessageLoaded(11999,24000));
        await page.evaluate(() => window.__panSessionStore.setState({ hasMoreMessages: false }));
        const scroller = page.locator('.chat-view-stage .overflow-auto').first();
        await scroller.hover(); await page.mouse.wheel(0,-120); await delay(200);
        await scroller.evaluate((element,sparse) => { element.scrollTop = sparse ? 0 : element.scrollHeight-element.clientHeight; },session === sparse);
        await delay(700);
        const before = await scroller.evaluate(element => element.scrollTop);
        const reads = [];
        page.on('response', response => { if (response.url().includes('/history?')) reads.push((async () => { try { scenario.bytes += (await response.body()).length; } catch { /* canceled owned response */ } })()); });
        await page.route(`**/api/sessions/${session.id}/history?*`, async route => { scenario.requests.push(route.request().url()); await delay(5); await route.continue(); });
        await page.locator('.message-navigation-dock__handle').hover();
        const status = () => page.locator('.message-navigation-rail').getAttribute('data-index-status');
        const settled = () => poll(status, value => value !== 'loading' && value !== 'idle');
        let partials = 0;
        await settled();
        while (await status() === 'partial') {
          assert.ok(++partials < 30,'nearest partial must converge after cache eviction');
          const requestStart = scenario.requests.length;
          await page.getByRole('button',{name:'Load more nearby navigation'}).click(); await settled();
          scenario.batches.push({ type:'nearest-continue',requests:scenario.requests.length-requestStart });
          assert.ok(scenario.requests.length-requestStart <= 6);
        }
        scenario.nearestPartials = partials;
        if (session === sparse) assert.ok(partials > 0,'sparse fixture must cross automatic page budget');
        const readPage = () => page.locator('.message-navigation-marker').evaluateAll(markers => markers.map(marker => ({ offset:Number(marker.dataset.canonicalOffset),messageId:marker.dataset.messageId })));
        let current = await readPage();
        assert.ok(current.length > 0 && current.length <= 200);
        const validate = rows => {
          const start = expected.indexOf(rows[0].offset), end = expected.indexOf(rows.at(-1).offset);
          assert.ok(start >= 0 && end >= start);
          assert.deepEqual(rows.map(row => row.offset),expected.slice(start,end+1),'no gap within a displayed target page');
          for (const row of rows) assert.equal(row.messageId,identities.get(row.offset),`canonical identity ${row.offset}`);
          assert.ok(rows.length <= 200);
        };
        // Preserve offsets before checking new data-message-id, so the old
        // application reproduces the actual skipped-boundary failure first.
        scenario.pages.push({ direction:'initial',rows:current });
        const visited = new Set(current.map(row => row.offset));
        const move = async direction => {
          const name = direction === -1 ? 'Load earlier navigation' : 'Load later navigation';
          const button = page.getByRole('button',{name});
          if (!await button.count()) return false;
          const previous = current;
          const requestStart = scenario.requests.length;
          await button.click(); await settled();
          let continuations = 0;
          while (await status() === 'partial') {
            assert.ok(++continuations < 60,'sparse directional cursor must make bounded progress');
            const batchStart = scenario.requests.length;
            await page.getByRole('button',{name:'Load more nearby navigation'}).click(); await settled();
            scenario.batches.push({ type:'directional-continue',direction,requests:scenario.requests.length-batchStart });
            assert.ok(scenario.requests.length-batchStart <= 6);
          }
          current = await readPage();
          if (JSON.stringify(current.map(row => row.offset)) === JSON.stringify(previous.map(row => row.offset))) {
            assert.equal(await button.count(),0,'unchanged page must have reached a proven canonical end'); return false;
          }
          scenario.pages.push({ direction,rows:current,requests:scenario.requests.length-requestStart,continuations });
          const oldFirst = expected.indexOf(previous[0].offset), oldLast = expected.indexOf(previous.at(-1).offset);
          if (direction === 1) assert.equal(current[0].offset,expected[oldLast+1],'later page must begin at the adjacent target');
          else assert.equal(current.at(-1).offset,expected[oldFirst-1],'earlier page must end at the adjacent target');
          validate(current); current.forEach(row => visited.add(row.offset));
          return true;
        };
        for (let guard=0; await move(-1); guard++) assert.ok(guard < 100);
        assert.equal(current[0].offset,expected[0]);
        for (let guard=0; await move(1); guard++) assert.ok(guard < 100);
        assert.equal(current.at(-1).offset,expected.at(-1));
        assert.deepEqual([...visited].sort((a,b)=>a-b),expected,'every canonical target must be reachable');
        // Reverse again after >12-page eviction: no hidden dependency on cache.
        for (let guard=0; await move(-1); guard++) assert.ok(guard < 100);
        assert.equal(current[0].offset,expected[0]);
        scenario.baseVisitedCount = visited.size;
        scenario.pagingBodyDelta = (await scroller.evaluate(element=>element.scrollTop))-before;
        assert.equal(scenario.pagingBodyDelta,0,'directional paging must not move chat body');
        if (session === dense) {
          for (let guard=0; await move(1); guard++) assert.ok(guard < 100);
          await context.request.post(`${base}/__e2e/append-history`,{data:{sessionId:session.id,messages:[0,1,2].map(i=>({role:'user',content:`APPEND ${i}`}))}});
          const response = await context.request.get(`${base}/api/sessions/${session.id}/history?before=${total+3}&limit=3`);
          const body = await response.json(); body.history.forEach((message,i)=>identities.set(total+i,message.messageId));
          expected.push(total,total+1,total+2);
          await page.evaluate(() => window.__panSessionStore.getState().refreshCurrentSessionHistory());
          await poll(()=>page.locator('.message-navigation-rail').getAttribute('data-history-total'),v=>Number(v)===total+3);
          assert.equal(await move(1),true); assert.deepEqual(current.map(row=>row.offset),[total,total+1,total+2]);
          assert.equal(await move(-1),true);
          assert.equal(current.at(-1).offset,total-1);
          scenario.appendVerified = true;
        }
        scenario.bodyDelta = (await scroller.evaluate(element=>element.scrollTop))-before;
        // Only paging is included here; explicit history refresh for append is
        // allowed to restore chat geometry and is excluded from this assertion.
        if (session === sparse) assert.equal(scenario.bodyDelta,0);
        await Promise.allSettled(reads);
        scenario.visitedCount = visited.size; scenario.passed = true;
        await page.screenshot({path:path.join(output,`${session === dense ? 'dense' : 'sparse'}-paging.png`)});
      } finally {
        await context.tracing.stop({path:path.join(output,`${session === dense ? 'dense' : 'sparse'}-paging-trace.zip`)});
        await context.close();
        await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(report,null,2));
      }
    }
  }

  if (mode !== 'baseline' && !pagingOnly) {
    for (const test of ['retry', 'retry-click', 'automatic', 'user-takeover', 'session-switch', 'close', 'epoch', 'empty-viewport', 'filter', 'desktop-click', 'mobile']) {
      const mobile = test === 'mobile';
      const context = await browser.newContext({ viewport: { width: mobile ? 390 : 1120, height: mobile ? 844 : 900 }, isMobile: mobile, hasTouch: mobile });
      await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
      const page = await context.newPage();
      page.on('pageerror', error => report.errors.push(error.message));
    page.on('request', request => { if (/:(8767|8768)(\/|$)/.test(request.url())) report.protectedRequests.push(request.url()); });
    page.on('console', message => { if (['error', 'warning'].includes(message.type())) report.console.push({ type: message.type(), text: message.text() }); });
      await page.goto(`${base}/react/?panE2E=1`);
      if (mobile) await page.getByRole('button', { name: '打开侧边栏' }).click();
      await page.locator('[data-session-card-id]').filter({ hasText: fault.name }).first().click();
      if (mobile) await page.getByTestId('mobile-sidebar-close').click();
      await page.waitForFunction(() => window.__panSessionStore.getState().currentMessages.length > 0 && !window.__panSessionStore.getState().historyLoading);
      await page.evaluate(() => { window.__panSessionStore.setState({ hasMoreMessages: false }); window.__navExpectedCandidates = [545, 580]; });
      const scroller = page.locator('.chat-view-stage .overflow-auto').first();
      await scroller.hover(); await page.mouse.wheel(0, -120); await delay(100);
      await scroller.evaluate(el => { el.scrollTop = 0; }); await delay(300);
      const before = await snapshot(page);
      assert.ok(before.start >= 550 && before.start < 570, `fixture must have incomplete nearby coverage: ${JSON.stringify(before)}`);
      let responseBytes = 0; const responseReads = [];
      page.on('response', response => { if (response.url().includes('/history?') && new URL(response.url()).searchParams.get('limit') === '200') responseReads.push((async () => { try { responseBytes += (await response.body()).length; } catch { /* Aborted generations have no complete response body. */ } })()); });
      let calls = 0, active = 0, maxActive = 0, fail = ['retry', 'retry-click', 'close'].includes(test);
      const requests = [];
      await page.route(`**/api/sessions/${fault.id}/history?*`, async route => {
        if (new URL(route.request().url()).searchParams.get('limit') !== '200') return route.continue();
        calls++; active++; maxActive = Math.max(active, maxActive); requests.push(route.request().url());
        try {
          if (fail || (test === 'automatic' && calls === 1)) await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Injected temporary navigation outage' }) });
          else { await delay(['session-switch', 'user-takeover', 'epoch', 'filter'].includes(test) ? 650 : 180); await route.continue(); }
        } catch { /* An aborted owned request cannot deliver a late page. */ }
        finally { active--; }
      });
      const open = async () => {
        if (mobile) await page.getByTestId('mobile-message-navigation-toggle').click();
        else { await page.mouse.move(5, 5); await delay(100); await page.locator('.message-navigation-dock__handle').hover(); }
      };
      const hiddenViewport = test === 'empty-viewport' ? await page.addStyleTag({ content: '.chat-view-stage .overflow-auto { display: none !important; }' }) : null;
      await open();
      if (hiddenViewport) {
        await delay(120); assert.equal(await page.locator('.is-viewport-target').count(), 0);
        await hiddenViewport.evaluate(el => el.remove());
      }
      await poll(() => calls, n => n > 0);
      const result = { test, before, requests, maxActive: 0 };
      const faultStarted = performance.now();
      if (['retry', 'retry-click'].includes(test)) {
        // A source-less scroll event from programmatic restoration must not cancel retry.
        await scroller.evaluate(el => { el.scrollTop += 1; });
        const retry = page.getByRole('button', { name: 'Retry message navigation' });
        await retry.waitFor(); assert.equal(calls, 3);
        await delay(800); assert.equal(calls, 3);
        assert.ok(await page.locator('.message-navigation-marker').count() > 0);
        await page.screenshot({ path: path.join(output, 'retry-error.png') });
        fail = false;
        if (test === 'retry-click') await retry.dblclick();
        else { await retry.focus(); await page.keyboard.press('Enter'); }
        await poll(() => snapshot(page), value => value.selected === 545 && value.visible);
        assert.equal(calls, 4); assert.equal(maxActive, 1);
        assert.equal(await retry.count(), 0);
      } else if (test === 'empty-viewport') {
        await poll(() => snapshot(page), value => value.selected === 545 && value.visible);
        assert.equal(calls, 1);
      } else if (test === 'automatic') {
        await poll(() => snapshot(page), value => value.selected === 545 && value.visible);
        assert.equal(calls, 2); assert.equal(maxActive, 1);
      } else if (test === 'user-takeover') {
        await scroller.hover(); await page.mouse.wheel(0, 120); await delay(850);
        assert.equal((await snapshot(page)).selected, null); assert.equal(calls, 1);
        await page.mouse.move(5, 5); await delay(150); await open();
        await poll(() => snapshot(page), value => value.selected === value.expected && value.visible);
      } else if (test === 'session-switch') {
        await page.locator('[data-session-card-id]').filter({ hasText: large.name }).first().click();
        await page.waitForFunction(id => window.__panSessionStore.getState().currentSessionId === id, large.id);
        await delay(850);
        assert.equal(await page.locator('.message-navigation-marker[title^="FAULT"]').count(), 0);
      } else if (test === 'close') {
        await page.locator('.message-navigation-dock__handle').click();
        await page.waitForFunction(() => document.querySelector('.message-navigation-dock')?.dataset.expanded === 'false');
        await delay(850); assert.equal(calls, 1);
        assert.equal(await page.locator('.message-navigation-dock').getAttribute('data-expanded'), 'false');
        fail = false; await open(); await poll(() => snapshot(page), value => value.selected === 545 && value.visible);
      } else if (test === 'epoch') {
        await context.request.post(`${base}/__e2e/replace-history`, { data: { sessionId: fault.id, messages: faultMessages.map(m => ({ ...m, content: m.content.replace('FAULT', 'NEW-EPOCH') })) } });
        await page.evaluate(() => window.__panSessionStore.getState().refreshCurrentSessionHistory());
        await delay(850);
        assert.equal(await page.locator('.message-navigation-marker[title^="FAULT"]').count(), 0);
        await page.mouse.move(5, 5); await delay(150); await open();
        await poll(() => snapshot(page), value => value.selected === value.expected && value.visible);
        // Restore the isolated fixture for the following mobile test.
        await context.request.post(`${base}/__e2e/replace-history`, { data: { sessionId: fault.id, messages: faultMessages } });
      } else if (test === 'filter') {
        await page.getByTitle('App settings', { exact: true }).click();
        const settings = page.getByRole('dialog', { name: 'App Settings' });
        await settings.getByRole('tab', { name: 'Appearance' }).click();
        await settings.getByRole('switch', { name: 'Show QQ messages' }).click();
        await settings.getByRole('button', { name: 'Close', exact: true }).click();
        await page.evaluate(() => { window.__navExpectedCandidates = [580]; });
        await open(); await poll(() => snapshot(page), value => value.selected === 580 && value.visible);
        assert.equal(await page.locator('.message-navigation-marker[title^="FAULT 545"]').count(), 0);
        // Restore the isolated settings through the same UI.
        await page.getByTitle('App settings', { exact: true }).click();
        await settings.getByRole('tab', { name: 'Appearance' }).click();
        await settings.getByRole('switch', { name: 'Show QQ messages' }).click();
        await settings.getByRole('button', { name: 'Close', exact: true }).click();
      } else if (test === 'desktop-click') {
        await poll(() => snapshot(page), value => value.selected === 545 && value.visible);
        await page.evaluate(() => window.__panSessionStore.setState({ hasMoreMessages: true }));
        await page.locator('.message-navigation-marker.is-viewport-target').click();
        await poll(() => snapshot(page), value => value.start <= 545 && value.end >= 545);
        assert.equal(await page.locator('.message-navigation-jump-error').count(), 0);
      } else if (test === 'mobile') {
        await poll(() => snapshot(page), value => value.selected === 545 && value.visible);
        const target = page.locator('.message-navigation-marker.is-viewport-target');
        const rect = await target.boundingBox(); assert.ok(rect);
        const cdp = await context.newCDPSession(page);
        const point = { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, id: 1 };
        const bodyBefore = await scroller.evaluate(el => el.scrollTop);
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [point] }); await delay(500);
        assert.equal(await page.locator('[data-preview-mode="scrub"]').count(), 1);
        const other = await page.locator('.message-navigation-marker[data-canonical-offset="580"]').boundingBox();
        assert.ok(other);
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: other.x + other.width / 2, y: other.y + other.height / 2, id: 1 }] });
        await poll(() => page.locator('[data-preview-mode="scrub"]').textContent(), text => text?.includes('FAULT 580'));
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await delay(120);
        assert.equal(await scroller.evaluate(el => el.scrollTop), bodyBefore);
        assert.equal(await page.locator('[data-preview-mode="scrub"]').count(), 0);
        await page.evaluate(() => window.__panSessionStore.setState({ hasMoreMessages: true }));
        await target.tap();
        await poll(() => snapshot(page), value => value.start <= 545 && value.end >= 545);
        assert.equal(await page.locator('.message-navigation-jump-error').count(), 0);
        const dockBounds = await page.locator('.message-navigation-dock').boundingBox();
        result.dockBounds = dockBounds;
        assert.ok(dockBounds.x >= 0 && dockBounds.x + dockBounds.width <= 391);
      }
      result.availableMs = performance.now() - faultStarted;
      result.after = await snapshot(page); await Promise.allSettled(responseReads); result.calls = calls; result.bytes = responseBytes; result.maxActive = maxActive;
      report.scenarios.push(result);
      await page.screenshot({ path: path.join(output, `${test}.png`) });
      await context.tracing.stop({ path: path.join(output, `${test}-trace.zip`) });
      await context.close();
      await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(report, null, 2));
    }
  }
  assert.deepEqual(report.errors, []); assert.deepEqual(report.protectedRequests, []); report.passed = true;
} catch (error) { report.failure = error.stack; process.exitCode = 1; }
finally {
  await browser?.close();
  const identity = JSON.parse(await fs.readFile(path.join(runtime, 'server-identity.json'), 'utf8'));
  report.serverIdentity = { ...identity, launcherPid: server.pid }; assert.equal(identity.port, port); assert.equal(path.resolve(identity.checkout), root);
  assert.equal(identity.pid, serverProcessIdentity.pid);
  const stop = 'import json,sys,psutil; v=json.loads(sys.argv[1]); p=psutil.Process(v["pid"]); assert abs(p.create_time()-v["processCreatedAt"])<0.001; assert any(c.laddr.port==v["port"] and c.status=="LISTEN" for c in p.net_connections(kind="inet")); p.terminate()';
  execFileSync(python, ['-c', stop, JSON.stringify(serverProcessIdentity)], { windowsHide: true });
  await poll(async () => new Promise(resolve => { const socket = net.connect({ host: '127.0.0.1', port }); socket.once('connect', () => { socket.destroy(); resolve(false); }); socket.once('error', () => resolve(true)); }), Boolean, 5000);
  report.cleanup = { portFree: true, ownedPid: identity.pid };
  await fs.writeFile(path.join(output, 'server.log'), logs);
  await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ output, ...report }));
}
