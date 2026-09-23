/* global window, document, performance, requestAnimationFrame, PerformanceObserver, fetch, console */
/** Real production bundle + HTTP/WS + real Workers + deterministic CLI.
 * Run: node e2e/frontend-full.e2e.mjs (after building packages/web).
 * Artifacts stay in test-results/full-<timestamp>, including cold-restart data.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn, spawnSync } from 'node:child_process';
import process from 'node:process';
import { Buffer } from 'node:buffer';
import { setTimeout } from 'node:timers';
import { chromium } from '@playwright/test';

const root = path.resolve(import.meta.dirname, '../../..');
const runtime = path.resolve(import.meta.dirname, '../test-results', `full-${Date.now()}`);
const python = process.env.PAN_E2E_PYTHON || 'D:/project/Pan/.venv/Scripts/python.exe';
const port = 8765;
const base = `http://127.0.0.1:${port}`;
const evidence = { root, runtime, port, stages: [], processes: [], errors: [], requests: [], frames: [] };
await fs.mkdir(runtime, { recursive: true });
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function poll(read, test, label, timeout = 15000) {
  let last;
  for (const end = Date.now() + timeout; Date.now() < end; await sleep(80)) {
    last = await read();
    if (test(last)) return last;
  }
  throw new Error(`${label}: ${JSON.stringify(last).slice(0, 2500)}`);
}
async function freePort() {
  await new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once('error', reject);
    s.listen(port, '127.0.0.1', () => s.close(resolve));
  });
}
async function api(route, body) {
  const r = await fetch(base + route, body ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) } : {});
  assert.equal(r.status, 200, route);
  const data = await r.json();
  assert.ok(!data.error, `${route}: ${JSON.stringify(data)}`);
  return data;
}
let server, browser, page;
async function start() {
  await freePort();
  server = spawn(python, [path.join(import.meta.dirname, 'frontend-full.server.py')], {
    cwd: root, windowsHide: true, env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  const logs = [];
  server.stdout.on('data', b => logs.push(b));
  server.stderr.on('data', b => logs.push(b));
  server.on('exit', () => fs.writeFile(path.join(runtime, `server-${server?.pid}.log`), Buffer.concat(logs)));
  await poll(async () => { try { return await api('/api/sessions?summary=1'); } catch { return null; } }, x => !!x, 'server ready', 30000);
  const identity = JSON.parse(await fs.readFile(path.join(runtime, 'server-identity.json')));
  assert.equal(path.resolve(identity.checkout), root);
  assert.equal(identity.port, port);
  evidence.processes.push({ launcher: server.pid, ...identity });
  console.log('server', identity);
}
async function stop() {
  if (!server || server.exitCode !== null) return;
  // Only the process tree launched by this harness; never search/kill by port.
  if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true });
  else server.kill('SIGTERM');
  await poll(() => Promise.resolve(server.exitCode), x => x !== null, 'owned server exit');
}
const projection = rows => rows.filter(m => !(m.role === 'system' && /^\[(DONE|ERROR|CANCELLED)\]/.test(m.content)))
  .map(({ role, content }) => ({ role, content }));
async function state() {
  return page.evaluate(() => { const s = window.__panSessionStore.getState(); return {
    id: s.currentSessionId, rows: s.currentMessages, total: s.sessions.find(x => x.id === s.currentSessionId)?.historyTotal,
    start: s.historyLoadEnd, more: s.hasMoreMessages,
  }; });
}
async function select(name) {
  await page.locator('[data-session-card-id]').filter({ hasText: name }).first().click();
  await poll(state, s => s.id === ids[name] && s.rows.length > 0, `select ${name}`);
}
async function send(text) {
  const input = page.locator('[contenteditable="true"]').first();
  await input.fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}
async function canonical(id) {
  let before = 0, rows = [];
  for (;;) {
    const p = await api(`/api/sessions/${id}/history?before=${before}&limit=500`);
    rows = [...p.history, ...rows];
    if (p.start === 0) return rows;
    assert.ok(before === 0 || p.start < before, 'pagination makes progress');
    before = p.start;
  }
}
async function equalCanonical(label) {
  const current = await state();
  const expected = projection(await canonical(current.id));
  const result = await poll(state, s => JSON.stringify(projection(s.rows)) === JSON.stringify(expected.slice(s.start)), label);
  const violations = await page.evaluate(() => window.__transientViolations || []);
  assert.deepEqual(violations, [], 'no transient duplicate reply before convergence');
  // Compare virtual DOM rows by their actual display index, including the
  // collapsed thinking/tool-group summaries. Also check visual geometry:
  // correct store order is insufficient if measured rows overlap on screen.
  await page.waitForTimeout(80);
  const dom = await page.locator('main [data-index]').evaluateAll(rows => rows.map(el => ({
    index: Number(el.dataset.index), text: el.textContent,
    top: el.getBoundingClientRect().top, bottom: el.getBoundingClientRect().bottom,
  })));
  assert.ok(dom.length > 0, 'rendered rows');
  const grouped = [];
  for (const row of result.rows) {
    if (row.role === 'tool' && grouped.at(-1)?.role === 'tool') grouped.at(-1).items.push(row);
    else if (row.role === 'thinking' && grouped.at(-1)?.role === 'thinking') grouped.at(-1).items.push(row);
    else if (row.role === 'tool' || row.role === 'thinking') grouped.push({ role: row.role, items: [row] });
    else grouped.push({ ...row, items: [row] });
  }
  const normalize = x => x.replace(/\s+/g, ' ').trim();
  for (let i = 0; i < dom.length; i++) {
    const actual = dom[i], expectedRow = grouped[actual.index];
    assert.ok(expectedRow, `DOM row ${actual.index} has a store counterpart`);
    if (expectedRow.role === 'thinking') {
      assert.ok(normalize(actual.text).toLowerCase().includes('thinking'),
        `thinking group ${actual.index}: ${actual.text}`);
      if (expectedRow.items.length > 1) {
        assert.ok(normalize(actual.text).includes(`${expectedRow.items.length} thinking blocks`),
          `thinking group count ${actual.index}: ${actual.text}`);
      }
    } else if (expectedRow.role !== 'tool') {
      assert.ok(normalize(actual.text).includes(normalize(expectedRow.content)),
        `DOM/store mismatch at ${actual.index}: ${actual.text.slice(0,100)} expected ${expectedRow.content.slice(0,100)}`);
    } else {
      assert.ok(/tool/i.test(actual.text), `tool group ${actual.index}: ${actual.text}`);
    }
    if (i > 0) {
      assert.ok(actual.index > dom[i-1].index, 'DOM indexes strictly ordered');
      assert.ok(actual.top >= dom[i-1].bottom - 1, 'rendered messages do not overlap');
    }
  }
  evidence.stages.push({ label, id: result.id, start: result.start, count: result.rows.length, canonicalCount: expected.length, dom });
  console.log('PASS', label, result.rows.length, 'canonical', expected.length, 'start', result.start);
  return result;
}

async function assertLiveVisualOrder(label, snapshots) {
  const sample = await page.evaluate((sampleLabel) => {
    const store = window.__panSessionStore.getState();
    const grouped = [];
    for (const message of store.currentMessages) {
      const previous = grouped.at(-1);
      if (message.role === 'tool' && previous?.role === 'tool') {
        previous.items.push(message);
      } else if (message.role === 'thinking' && previous?.role === 'thinking') {
        previous.items.push(message);
      } else if (message.role === 'tool') {
        grouped.push({ role: 'tool', items: [message] });
      } else if (message.role === 'thinking') {
        grouped.push({ role: 'thinking', items: [message] });
      } else {
        grouped.push({ role: message.role, content: message.content, items: [message] });
      }
    }
    const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
    const nodes = [...document.querySelectorAll('main [data-index]')].map((el) => {
      const rect = el.getBoundingClientRect();
      const index = Number(el.dataset.index);
      const expected = grouped[index];
      const actualRole = el.querySelector('.msg.tool') || el.querySelector('.tool-group')
        ? 'tool'
        : el.querySelector('.thinking')
          ? 'thinking'
          : el.querySelector('.msg.user')
            ? 'user'
            : el.querySelector('.msg.assistant')
              ? 'assistant'
              : el.querySelector('.system-message')
                ? 'system'
                : 'unknown';
      return {
        index,
        top: rect.top,
        bottom: rect.bottom,
        text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 180),
        actualRole,
        expectedRole: expected?.role || 'missing',
        expectedContent: expected?.role !== 'thinking' && expected?.content
          ? normalize(expected.content).slice(0, 180).trimEnd() : null,
        expectedToolCount: expected?.role === 'tool' ? expected.items.length : 0,
        expectedThinkingCount: expected?.role === 'thinking' ? expected.items.length : 0,
      };
    });
    const violations = [];
    for (let i = 1; i < nodes.length; i += 1) {
      const previous = nodes[i - 1];
      const current = nodes[i];
      if (current.index <= previous.index) {
        violations.push({ kind: 'index-order', previous, current });
      }
      if (current.top < previous.top - 1) {
        violations.push({ kind: 'geometry-order', previous, current });
      }
      if (current.top < previous.bottom - 1) {
        violations.push({ kind: 'geometry-overlap', previous, current });
      }
    }
    for (const node of nodes) {
      if (node.expectedRole !== node.actualRole) {
        violations.push({ kind: 'role-mismatch', node });
      }
      if (node.expectedRole === 'thinking'
          && !normalize(node.text).toLowerCase().includes('thinking')) {
        violations.push({ kind: 'thinking-summary-mismatch', node });
      }
      if (node.expectedThinkingCount > 1
          && !normalize(node.text).includes(`${node.expectedThinkingCount} thinking blocks`)) {
        violations.push({ kind: 'thinking-count-mismatch', node });
      }
      if (node.expectedRole !== 'tool' && node.expectedRole !== 'thinking' && node.expectedContent
          && !normalize(node.text).includes(node.expectedContent)) {
        violations.push({ kind: 'content-mismatch', node });
      }
    }
    if (sampleLabel === 'switch-delta') {
      const toolIndexes = grouped
        .map((item, index) => item.role === 'tool'
          && item.items.some(message => String(message.content).includes(sampleLabel))
          ? index : -1)
        .filter(index => index >= 0);
      const answerIndex = grouped.findIndex(item => item.role === 'assistant'
        && String(item.content || '').includes(`answer:${sampleLabel}`));
      if (toolIndexes.length > 0 && answerIndex >= 0 && Math.max(...toolIndexes) >= answerIndex) {
        violations.push({
          kind: 'semantic-tool-after-answer',
          toolIndexes,
          answerIndex,
          grouped: grouped.map(item => ({
            role: item.role,
            content: item.content,
            tools: item.items.map(message => message.content),
          })),
        });
      }
    }
    return { nodes, violations };
  }, label);
  snapshots.push({ at: Date.now(), label, ...sample });
  assert.deepEqual(sample.violations, [], `${label}: live DOM order/geometry violation`);
}
const ids = {};
const faults = {
  duplicate: false,
  reorder: false,
  held: null,
  delayedHistory: 0,
  completedDelayedHistory: 0,
  historyDelay: 0,
  delaySnapshot: false,
  // A browser can keep an OPEN WebSocket object after its background page or
  // an intermediary has stopped delivering frames. The E2E marks the current
  // real socket as a one-way black hole while the page is backgrounded; a
  // correct resume path must create a new socket and converge without reload.
  backgroundDeadSocket: null,
};
let socketRoute;
let socketConnectionCount = 0;
try {
  await start();
  const listing = await api('/api/sessions?summary=1');
  for (const s of (listing.sessions || listing)) ids[s.name] = s.id;
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.routeWebSocket('**/ws', ws => {
    socketRoute = ws;
    socketConnectionCount++;
    const upstream = ws.connectToServer();
    upstream.onMessage(message => {
      if (faults.backgroundDeadSocket === ws) return;
      const e = JSON.parse(String(message));
      if (faults.delaySnapshot && e.type === 'resync.snapshot') {
        faults.delaySnapshot = false;
        setTimeout(() => ws.send(message), 450);
        evidence.stages.push({ label: 'authoritative snapshot delayed across live frames' });
        return;
      }
      if (faults.reorder && e.type === 'worker.stream' && e.event?.delta) {
        if (!faults.held) { faults.held = message; return; }
        ws.send(message);
        ws.send(faults.held);
        faults.held = null;
        faults.reorder = false;
        evidence.stages.push({ label: 'two live transport frames reordered' });
        return;
      }
      ws.send(message);
      if (faults.duplicate && e.type === 'worker.stream') ws.send(message);
    });
  });
  await context.route('**/api/sessions/*/history?*', async route => {
    const response = await route.fetch();
    if (faults.historyDelay > 0) {
      faults.delayedHistory++;
      await sleep(faults.historyDelay);
      faults.completedDelayedHistory++;
    }
    await route.fulfill({ response });
  });
  await context.route('**://fonts.googleapis.com/**', r => r.abort());
  await context.route('**://fonts.gstatic.com/**', r => r.abort());
  page = await context.newPage();
  await page.addInitScript(() => {
    window.__perf = { frames: [], longTasks: [] };
    let last = performance.now();
    const tick = now => { window.__perf.frames.push(now - last); last = now; requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
    new PerformanceObserver(list => window.__perf.longTasks.push(...list.getEntries().map(e => e.duration)))
      .observe({ type: 'longtask', buffered: true });
  });
  page.on('pageerror', e => evidence.errors.push(String(e)));
  page.on('request', r => { if (r.url().includes('/api/')) evidence.requests.push({ at: Date.now(), url: r.url(), method: r.method() }); });
  page.on('websocket', ws => ws.on('framereceived', f => {
    try { evidence.frames.push({ at: Date.now(), event: JSON.parse(String(f.payload)) }); }
    catch { /* Ignore transport frames that are not JSON. */ }
  }));
  await page.goto(`${base}/react/?panE2E=1`, { waitUntil: 'domcontentloaded' });
  await select('E2E-A');
  await page.evaluate(() => {
    window.__transcriptTrace = [];
    window.__transientViolations = [];
    window.__panSessionStore.subscribe(s => {
      const seen = new Set();
      for (const m of s.currentMessages) {
        if (m.role !== 'assistant' || !m.content.startsWith('answer:')) continue;
        const label = m.content.split('\n')[0];
        if (seen.has(label) && window.__transientViolations.length < 10) {
          window.__transientViolations.push({ id: s.currentSessionId, label, at: Date.now() });
        }
        seen.add(label);
      }
    });
    window.__stopTranscriptTrace = window.__panSessionStore.subscribe(s => {
      const t = s.sessionTranscripts[s.currentSessionId];
      if (!t) return;
      const trace = { at: Date.now(), id: s.currentSessionId, anchor: t.anchorOffset, total: t.window.total,
        runtime: t.runtime.map(m => [m.role, m.content.slice(0,60)]),
        tail: s.currentMessages.slice(-16).map(m => [m.role,m.content.slice(0,60)]) };
      window.__transcriptTrace.push(trace);
      if (window.__transcriptTrace.length > 250) window.__transcriptTrace.shift();
    });
  });
  await equalCanonical('initial tail');
  // Actual wheel-to-top pagination, then keep this loaded prefix through all turns.
  const scroller = page.locator('main div.overflow-auto').first();
  for (let i = 0; i < 6 && (await state()).start > 0; i++) {
    await scroller.hover();
    await page.mouse.wheel(0, -100000);
    await sleep(350);
  }
  await equalCanonical('wheel pagination');
  const oldest = (await state()).start;
  for (let turn = 1; turn <= 4; turn++) {
    await select('E2E-A');
    await send(`round-${turn}`);
    await poll(state, s => s.rows.some(m => m.content.includes(`answer:round-${turn}`)), 'live answer visible');
    await select('E2E-B');
    await send(`compound-${turn}`);
    if (turn % 2) { await sleep(180); await select('E2E-A'); }
    else { await sleep(2100); await select('E2E-A'); }
    await poll(() => api(`/api/sessions/${ids['E2E-A']}`), s => s.lastResult?.result?.includes(`answer:round-${turn}`), 'worker durable final');
    await equalCanonical(`A multi-turn ${turn}`);
    assert.equal((await state()).start, oldest, 'loaded history prefix survives switching');
    await select('E2E-B');
    await equalCanonical(`B compound ${turn}`);
  }
  await select('E2E-A');
  faults.historyDelay = 700;
  faults.duplicate = true;
  faults.reorder = true;
  faults.delaySnapshot = true;
  await send('faults-round');
  await poll(state, s => s.rows.some(m => m.content.includes('answer:faults-round')), 'fault round live');
  await select('E2E-B');
  await select('E2E-A');
  await select('E2E-B');
  await select('E2E-A');
  await poll(() => api(`/api/sessions/${ids['E2E-A']}`), s => s.lastResult?.result?.includes('answer:faults-round'), 'fault round complete');
  await sleep(900);
  faults.historyDelay = 0;
  faults.duplicate = false;
  await equalCanonical('delayed history + duplicate/reordered frames + rapid A/B/A');
  assert.ok(faults.delayedHistory > 0);
  assert.equal(faults.reorder, false, 'reordering actually executed');
  assert.equal(faults.delaySnapshot, false, 'snapshot delay actually executed');

  // Replay older completed tasks in reverse order through the real broadcaster.
  const oldResults = evidence.frames.filter(x => x.event.type === 'worker.result' && x.event.sessionId === ids['E2E-A']).map(x => x.event);
  for (const original of oldResults.slice(0, 3).reverse()) {
    const event = { ...original };
    for (const key of ['deliveryEpoch', 'deliverySeq', 'eventEpoch', 'eventSeq', 'serverEpoch', 'sourceCursorStart', 'sourceCursorEnd']) delete event[key];
    await api('/__e2e/broadcast', { event: { ...event, replayed: true } });
    await api('/__e2e/broadcast', { event: {
      type: 'worker.stream', sessionId: ids['E2E-A'], workerId: original.workerId,
      generation: original.generation, taskSeq: original.taskSeq,
      event: { type: 'assistant', delta: true, item_id: 'late-old', content: 'MUST-NOT-APPEAR' },
    } });
  }
  await equalCanonical('late duplicate terminal results and obsolete task deltas');

  // Regression: while the tool group is already above an actively growing
  // assistant delta, scroll the user away from the bottom and sample every
  // frame. Final-state equality alone misses transient virtual-row movement.
  await select('E2E-A');
  const liveVisualSnapshots = [];
  evidence.liveVisualSnapshots = liveVisualSnapshots;
  const liveInput = page.locator('[contenteditable="true"]').first();
  await liveInput.fill('visual-order-round');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await poll(state, s => s.rows.some(m => m.role === 'tool' && m.content.includes('visual-order-round')), 'visual-order tool visible');
  await scroller.hover();
  await page.mouse.wheel(0, -500);
  for (let i = 0; i < 55; i += 1) {
    await assertLiveVisualOrder('visual-order-round', liveVisualSnapshots);
    await sleep(25);
  }
  await poll(() => api(`/api/sessions/${ids['E2E-A']}`), s => s.lastResult?.result?.includes('answer:visual-order-round'), 'visual-order final');
  evidence.stages.push({ label: 'live delta visual order while user scrolls', samples: liveVisualSnapshots.length });

  // Regression: switch away from a live turn after its tool blocks exist, let
  // the selected Session continue receiving deltas in the background, then
  // switch back while the history request is deliberately delayed. The final
  // assistant text must remain after its own tool blocks in both the store and
  // the rendered virtual rows.
  await select('E2E-A');
  await page.getByTitle('Scroll to bottom').click().catch(() => {});
  await poll(() => scroller.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight), d => d <= 2, 'switch-delta before stream');
  faults.historyDelay = 650;
  const switchVisualSnapshots = [];
  evidence.switchVisualSnapshots = switchVisualSnapshots;
  await send('switch-delta');
  await poll(state, s => s.id === ids['E2E-A']
    && s.rows.some(m => m.role === 'tool' && m.content.includes('switch-delta'))
    && s.rows.some(m => m.role === 'assistant' && m.content.includes('answer:switch-delta')), 'switch-delta live tool and answer');
  const switchGroup = page.locator('.tool-group-header').last();
  await switchGroup.click();
  assert.ok(
    await page.locator('.tool-group').last().evaluate(el => el.getBoundingClientRect().height > 200),
    'switch-delta tool group should be expanded before switching sessions',
  );
  await scroller.hover();
  await page.mouse.wheel(0, -500);
  await sleep(35);
  await select('E2E-B');
  await select('E2E-A');
  await scroller.hover();
  await page.mouse.wheel(0, -500);
  for (let i = 0; i < 120; i += 1) {
    await assertLiveVisualOrder('switch-delta', switchVisualSnapshots);
    if (i % 6 === 0) await page.mouse.wheel(0, i % 12 === 0 ? 180 : -120);
    await sleep(16);
  }
  await poll(() => api(`/api/sessions/${ids['E2E-A']}`), s => s.lastResult?.result?.includes('answer:switch-delta'), 'switch-delta final');
  faults.historyDelay = 0;
  await equalCanonical('session switch during live delta and scroll');
  evidence.stages.push({ label: 'session switch during live delta and scroll', samples: switchVisualSnapshots.length });

  // Regression: a Codex Session keeps emitting deltas while it is not selected,
  // then the user selects it again before the Worker finishes. This is the
  // background-session projection path: every active store/DOM frame must still
  // contain exactly one assistant row for the turn, and it must converge to the
  // single canonical reply after completion.
  await select('E2E-A');
  await page.getByTitle('Scroll to bottom').click().catch(() => {});
  const backgroundLiveLabel = 'background-live-select';
  const backgroundLiveSamples = [];
  evidence.backgroundLiveSamples = backgroundLiveSamples;
  const delayedHistoryBeforeBackground = faults.delayedHistory;
  const completedDelayedHistoryBeforeBackground = faults.completedDelayedHistory;
  faults.duplicate = true;
  faults.reorder = true;
  await send(backgroundLiveLabel);
  await poll(state, s => s.id === ids['E2E-A'] && s.rows.some(m =>
    m.role === 'assistant' && m.content.includes(`answer:${backgroundLiveLabel}`)), 'background-live first delta');
  await poll(() => api(`/api/sessions/${ids['E2E-A']}`), s => s.workerStatus === 'running', 'background-live worker starts');
  await select('E2E-B');
  await sleep(400);
  assert.equal((await api(`/api/sessions/${ids['E2E-A']}`)).workerStatus, 'running',
    'Codex stream continues while its Session is in the background');
  await equalCanonical('background A stream leaves selected B transcript isolated');
  evidence.stages.push({ label: 'background A stream leaves selected B transcript isolated' });
  // The entry request will carry a history page captured before the current
  // live turn has finished. Let stream deltas race that delayed page merge.
  faults.historyDelay = 2600;
  await select('E2E-A');

  const assertSingleBackgroundReply = async () => {
    const sample = await page.evaluate(({ sessionId, label }) => {
      const store = window.__panSessionStore.getState();
      const rows = store.currentMessages.filter(message => message.role === 'assistant'
        && String(message.content).includes(`answer:${label}`));
      const dom = [...document.querySelectorAll('main [data-index]')]
        .filter(element => element.textContent.includes(`answer:${label}`))
        .map(element => ({ index: Number(element.dataset.index), text: element.textContent }));
      return { sessionId: store.currentSessionId, rows, dom };
    }, { sessionId: ids['E2E-A'], label: backgroundLiveLabel });
    assert.equal(sample.sessionId, ids['E2E-A'], 'the streaming Session is selected');
    assert.equal(sample.rows.length, 1, `store has one ${backgroundLiveLabel} assistant row`);
    assert.equal(sample.dom.length, 1, `DOM has one ${backgroundLiveLabel} assistant row`);
    backgroundLiveSamples.push({ at: Date.now(), contentLength: sample.rows[0].content.length,
      index: sample.dom[0].index });
  };
  for (let i = 0; i < 12; i += 1) {
    await assertSingleBackgroundReply();
    await sleep(16);
  }
  await select('E2E-B');
  await sleep(220);
  assert.equal((await api(`/api/sessions/${ids['E2E-A']}`)).workerStatus, 'running',
    'the stream remains active through a second session switch');
  await select('E2E-A');
  for (let i = 0; i < 18; i += 1) {
    await assertSingleBackgroundReply();
    await sleep(16);
  }
  await poll(() => api(`/api/sessions/${ids['E2E-A']}`), s => s.lastResult?.result?.includes(`answer:${backgroundLiveLabel}`),
    'background-live worker completes');
  // Include the terminal recovery request too, then wait until every delayed
  // selection/recovery response has actually returned after the Worker result.
  await sleep(160);
  const delayedHistoryTarget = faults.delayedHistory;
  assert.ok(delayedHistoryTarget - delayedHistoryBeforeBackground >= 2,
    'background A/B/A issued multiple delayed selection-history requests');
  await poll(() => Promise.resolve(faults.completedDelayedHistory), count => count >= delayedHistoryTarget,
    'post-terminal delayed history responses complete', 10000);
  await sleep(120);
  evidence.backgroundHistoryRace = {
    delayed: delayedHistoryTarget - delayedHistoryBeforeBackground,
    completed: faults.completedDelayedHistory - completedDelayedHistoryBeforeBackground,
    delayMs: 2600,
  };
  faults.duplicate = false;
  faults.historyDelay = 0;
  assert.equal(faults.reorder, false, 'background-live path actually reordered stream frames');
  assert.ok(faults.delayedHistory > delayedHistoryBeforeBackground,
    'background-live path delayed the selected Session history response');
  await equalCanonical('background Codex stream selected while still running');
  assert.ok(backgroundLiveSamples.length >= 24, 'sampled the selected background stream during live deltas');
  evidence.stages.push({ label: 'background Codex stream selected while still running',
    samples: backgroundLiveSamples.length });

  // Claude's real stream-json adapter shape has id-less, incremental deltas,
  // then one completed assistant event containing thinking, tool, and text
  // blocks. Keep this separate from Codex's native item-ID reconciliation.
  await select('E2E-C');
  const backgroundClaudeLabel = 'background-live-claude';
  const backgroundClaudeSamples = [];
  evidence.backgroundClaudeSamples = backgroundClaudeSamples;
  await send(backgroundClaudeLabel);
  await poll(state, s => s.id === ids['E2E-C'] && s.rows.some(m =>
    m.role === 'assistant' && m.content.includes(`answer:${backgroundClaudeLabel}`)), 'Claude background stream first delta');
  await poll(() => api(`/api/sessions/${ids['E2E-C']}`), s => s.workerStatus === 'running', 'Claude background worker starts');
  await select('E2E-B');
  await sleep(350);
  assert.equal((await api(`/api/sessions/${ids['E2E-C']}`)).workerStatus, 'running',
    'Claude stream continues while its Session is in the background');
  await select('E2E-C');
  const assertSingleClaudeReply = async () => {
    const sample = await page.evaluate(label => {
      const store = window.__panSessionStore.getState();
      const rows = store.currentMessages.filter(message => message.role === 'assistant'
        && String(message.content).includes(`answer:${label}`));
      const dom = [...document.querySelectorAll('main [data-index]')]
        .filter(element => element.textContent.includes(`answer:${label}`))
        .map(element => ({ index: Number(element.dataset.index), text: element.textContent }));
      return { rows, dom };
    }, backgroundClaudeLabel);
    assert.equal(sample.rows.length, 1, `store has one ${backgroundClaudeLabel} assistant row`);
    assert.equal(sample.dom.length, 1, `DOM has one ${backgroundClaudeLabel} assistant row`);
    backgroundClaudeSamples.push({ at: Date.now(), contentLength: sample.rows[0].content.length,
      index: sample.dom[0].index });
  };
  for (let i = 0; i < 10; i += 1) {
    await assertSingleClaudeReply();
    await sleep(16);
  }
  await select('E2E-B');
  await sleep(180);
  await select('E2E-C');
  for (let i = 0; i < 12; i += 1) {
    await assertSingleClaudeReply();
    await sleep(16);
  }
  await poll(() => api(`/api/sessions/${ids['E2E-C']}`), s => s.lastResult?.result?.includes(`answer:${backgroundClaudeLabel}`),
    'Claude background worker completes');
  await equalCanonical('background Claude stream selected while still running');
  assert.ok(backgroundClaudeSamples.length >= 22, 'sampled Claude stream while selected during live output');
  evidence.stages.push({ label: 'background Claude stream selected while still running',
    samples: backgroundClaudeSamples.length });

  // Regression: put the real Chromium page in the background while a real
  // Worker continues streaming. Keep the old browser WebSocket OPEN-looking
  // but drop its server-to-browser frames, which models the half-open socket
  // commonly left behind by background throttling/sleep. The page must resume
  // by replacing that transport and reconciling the durable terminal result;
  // no page reload is allowed.
  const backgroundPage = await context.newPage();
  await backgroundPage.goto('about:blank');
  // The preceding provider-specific Claude regression leaves E2E-C selected.
  // This background resume scenario is a Codex test and polls E2E-A below.
  await select('E2E-A');
  await page.bringToFront();
  await poll(() => page.evaluate(() => document.visibilityState), s => s === 'visible', 'page foreground before background stream');
  await page.getByTitle('Scroll to bottom').click().catch(() => {});
  await send('background-resume');
  await poll(state, s => s.rows.some(m => m.role === 'tool' && m.content.includes('background-resume')), 'background stream tool visible');
  assert.ok(socketRoute, 'background test needs the live WebSocket route');
  const socketsBeforeBackground = socketConnectionCount;
  faults.backgroundDeadSocket = socketRoute;
  const pageLifecycle = await context.newCDPSession(page);
  await backgroundPage.bringToFront();
  // Headless Chromium does not update document.visibilityState merely because
  // another headless Page is frontmost. Send the same browser lifecycle signal
  // that the production page receives, then use the real CDP frozen state so
  // timers/React work are actually suspended while the Worker continues.
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
    Object.defineProperty(document, 'hidden', { value: true, configurable: true });
    document.dispatchEvent(new window.Event('visibilitychange'));
  });
  await pageLifecycle.send('Page.setWebLifecycleState', { state: 'frozen' });
  assert.equal(await page.evaluate(() => document.visibilityState), 'hidden', 'page enters browser background');
  await poll(() => api(`/api/sessions/${ids['E2E-A']}`), s => s.lastResult?.result?.includes('answer:background-resume'), 'background worker completes while page hidden');
  await sleep(500);
  await pageLifecycle.send('Page.setWebLifecycleState', { state: 'active' });
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    Object.defineProperty(document, 'hidden', { value: false, configurable: true });
    document.dispatchEvent(new window.Event('visibilitychange'));
  });
  await page.bringToFront();
  const resumedAt = Date.now();
  await poll(() => page.evaluate(() => document.visibilityState), s => s === 'visible', 'page resumes from browser background');
  await poll(() => Promise.resolve(socketConnectionCount), count => count > socketsBeforeBackground, 'background resume opens a new websocket');
  await poll(state, s => s.rows.some(m => m.role === 'assistant' && m.content.includes('answer:background-resume')), 'background resume converges without reload', 12000);
  faults.backgroundDeadSocket = null;
  await backgroundPage.close();
  evidence.stages.push({
    label: 'background tab resume replaces half-open websocket without reload',
    resumedAt,
    socketsBeforeBackground,
    socketsAfterResume: socketConnectionCount,
  });

  // The same invariant while follow-bottom is active. A growing delta must
  // move the viewport, never reorder or repaint the already-rendered tool row.
  await page.getByTitle('Scroll to bottom').click().catch(() => {});
  await poll(() => scroller.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight), d => d <= 2, 'visual-order pinned before stream');
  const pinnedVisualSnapshots = [];
  evidence.pinnedVisualSnapshots = pinnedVisualSnapshots;
  const pinnedInput = page.locator('[contenteditable="true"]').first();
  await pinnedInput.fill('visual-order-pinned');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await poll(state, s => s.rows.some(m => m.role === 'tool' && m.content.includes('visual-order-pinned')), 'pinned visual-order tool visible');
  for (let i = 0; i < 90; i += 1) {
    await assertLiveVisualOrder('visual-order-pinned', pinnedVisualSnapshots);
    await sleep(20);
  }
  await poll(() => api(`/api/sessions/${ids['E2E-A']}`), s => s.lastResult?.result?.includes('answer:visual-order-pinned'), 'pinned visual-order final');
  evidence.stages.push({ label: 'live delta visual order while pinned to bottom', samples: pinnedVisualSnapshots.length });

  // Stress the variable-height case with a real expandable multi-tool group,
  // then keep moving the reader while the answer delta grows underneath it.
  await page.getByTitle('Scroll to bottom').click().catch(() => {});
  await poll(() => scroller.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight), d => d <= 2, 'visual-order stress before stream');
  const stressVisualSnapshots = [];
  evidence.stressVisualSnapshots = stressVisualSnapshots;
  const stressInput = page.locator('[contenteditable="true"]').first();
  await stressInput.fill('visual-order-stress');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await poll(state, s => s.rows.filter(m => m.role === 'tool' && m.content.includes('visual-order-stress')).length >= 8, 'stress tool group visible');
  const stressGroup = page.locator('.tool-group-header').last();
  await stressGroup.click();
  assert.ok(
    await page.locator('.tool-group').last().evaluate(el => el.getBoundingClientRect().height > 200),
    'stress tool group should be expanded to a variable-height block',
  );
  await scroller.hover();
  for (let i = 0; i < 140; i += 1) {
    if (i % 5 === 0) await page.mouse.wheel(0, -120);
    await assertLiveVisualOrder('visual-order-stress', stressVisualSnapshots);
    await sleep(16);
  }
  await poll(() => api(`/api/sessions/${ids['E2E-A']}`), s => s.lastResult?.result?.includes('answer:visual-order-stress'), 'stress visual-order final');
  evidence.stages.push({ label: 'expanded multi-tool group while scrolling through delta', samples: stressVisualSnapshots.length });

  await send('disconnect-round');
  await poll(state, s => s.rows.some(m => m.content.includes('answer:disconnect-round')), 'pre-disconnect live');
  socketRoute.close({ code: 1012, reason: 'E2E transport interruption' });
  await sleep(3500);
  await equalCanonical('disconnect during delta and reconnect after terminal');

  // Hold an actual Worker at the provider boundary so queue mutations cannot
  // race an artificial running status. Edit/delete through visible controls.
  await send('hold-queue');
  await poll(state, s => s.rows.some(m => m.content === 'queue gate waiting'), 'real worker queue gate');
  await send('edit-me');
  await send('delete-me');
  await page.getByTitle(/发送队列/).click();
  let editRow = page.locator('.queue-row-in').filter({ hasText: 'edit-me' });
  await editRow.hover();
  await editRow.getByTitle('编辑', { exact: true }).click();
  await page.locator('.queue-row-in textarea').fill('edited-message');
  await page.getByTitle('保存', { exact: true }).click();
  await page.locator('.queue-row-in textarea').waitFor({ state: 'detached' });
  await page.locator('.queue-row-in').filter({ hasText: 'edited-message' }).waitFor();
  const deleteRow = page.locator('.queue-row-in').filter({ hasText: 'delete-me' });
  await deleteRow.hover();
  await deleteRow.getByTitle('删除', { exact: true }).click();
  await deleteRow.waitFor({ state: 'detached' });
  await select('E2E-B');
  await select('E2E-A');
  assert.ok(!(await state()).rows.some(m => m.content === 'delete-me' || m.content === 'edit-me'));
  await fs.writeFile(path.join(runtime, 'release-queue'), 'release');
  await poll(() => api(`/api/sessions/${ids['E2E-A']}`), s => s.lastResult?.result?.includes('answer:edited-message'), 'edited queued task completes');
  await equalCanonical('real queued edit + delete + A/B/A + sequential delivery');
  assert.ok(!(await canonical(ids['E2E-A'])).some(m => m.content === 'delete-me' || m.content === 'edit-me'));
  await page.getByTitle(/发送队列/).click();

  // Fully load 5000 canonical rows using the same wheel/pagination UI as a user.
  await select('E2E-LONG');
  await page.evaluate(() => window.__stopTranscriptTrace?.());
  for (let i = 0; i < 110 && (await state()).start > 0; i++) {
    const before = (await state()).start;
    await scroller.hover();
    await page.mouse.wheel(0, -1000000);
    await poll(state, s => s.start < before, 'long-history wheel page', 5000);
    if (i % 20 === 19) console.log('long history remaining', (await state()).start);
  }
  assert.equal((await state()).start, 0, 'all 5000 rows loaded');
  await equalCanonical('long history fully paginated');
  await page.getByTitle('Scroll to bottom').click();
  await sleep(500);
  await page.evaluate(() => { window.__perf.frames = []; window.__perf.longTasks = []; });
  const perfStart = Date.now();
  await send('long-stream');
  await poll(state, s => s.rows.some(m => m.content.includes('answer:long-stream')), 'long stream live');
  await scroller.hover();
  await page.mouse.wheel(0, -700);
  await sleep(350);
  const away = await scroller.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight);
  assert.ok(away > 100, 'wheel opts out of follow');
  await sleep(450);
  assert.ok(await scroller.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight) > 100, 'stream does not pull reader down');
  await page.getByTitle('Scroll to bottom').click();
  await poll(() => scroller.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight), d => d <= 2, 'return to bottom');
  await poll(() => api(`/api/sessions/${ids['E2E-LONG']}`), s => s.lastResult?.result?.includes('answer:long-stream'), 'long stream final');
  await equalCanonical('5000 loaded rows + delta + upscroll + return bottom');
  const perf = await page.evaluate(() => window.__perf);
  const sorted = perf.frames.slice().sort((a,b) => a-b);
  evidence.performance = { durationMs: Date.now()-perfStart, frameCount: sorted.length,
    p95FrameMs: sorted[Math.floor(sorted.length*.95)], maxFrameMs: sorted.at(-1), longTasks: perf.longTasks };
  assert.ok(evidence.performance.p95FrameMs < 100, `stream frame p95 ${evidence.performance.p95FrameMs}ms`);
  assert.ok((evidence.performance.maxFrameMs || 0) < 1000, 'no one-second browser stalls');
  await select('E2E-A');
  await select('E2E-LONG');
  assert.equal((await state()).start, 0, '5000-row window survives return');
  await equalCanonical('long history A/B/A after streaming');

  // Reproduce the backend duplicate found in the persisted Session JSONL:
  // write the agent user row and RESERVED receipt, then kill the real Pan
  // process before the CLI hand-off callback. Recovery must reuse that same
  // history receipt while delivering the queued task once to the fake Codex
  // process. This crosses HTTP, Worker, Session JSONL, process restart, WS,
  // Zustand projection, and the real Chromium virtual list.
  const handoffContext = 'handoff-context-before-crash';
  const managerHistoryBeforeRecovery = await canonical(ids['E2E-B']);
  await api('/api/assign', {
    sessionId: ids['E2E-A'],
    text: handoffContext,
    source: 'agent',
    sourceSessionId: ids['E2E-B'],
    taskId: 'e2e-handoff-recovery-context',
  });
  const contextReady = path.join(runtime, 'handoff-context-ready');
  await poll(() => fs.stat(contextReady).then(() => true).catch(() => false),
    Boolean, 'assigned MA task is active before the follow-up arrives');

  const recoveryLabel = 'crash-recovery-history-idempotency';
  await api('/api/send', {
    sessionId: ids['E2E-A'],
    text: `////by agent\n${recoveryLabel}`,
    source: 'agent',
    sourceSessionId: ids['E2E-B'],
  });
  const targetSessionFile = path.join(runtime, 'sessions', `${ids['E2E-A']}.json`);
  const queueSnapshot = JSON.parse(await fs.readFile(targetSessionFile, 'utf8'));
  const recoveryQueueItem = queueSnapshot.queue_pending.find(item =>
    String(item.text).includes(recoveryLabel));
  assert.ok(recoveryQueueItem, 'active task queues the agent follow-up durably');
  assert.equal(recoveryQueueItem.taskId, 'e2e-handoff-recovery-context');
  assert.equal(recoveryQueueItem.taskIdSource, 'active',
    'agent_send records the active MA task id as pairing context');
  assert.equal(recoveryQueueItem.sourceSessionId, ids['E2E-B']);

  await fs.writeFile(path.join(runtime, 'release-handoff-context'), 'release');
  await poll(() => api(`/api/sessions/${ids['E2E-A']}`),
    s => s.lastResult?.result?.includes(`answer:${handoffContext}`),
    'agent-assigned task establishes active task context');

  const reservationMarker = path.join(runtime, 'handoff-reservation-paused-once');
  await poll(() => fs.stat(reservationMarker).then(() => true).catch(() => false),
    Boolean, 'queue history/receipt persisted before the CLI hand-off');
  const historyFile = path.join(runtime, 'sessions', `${ids['E2E-A']}.history.jsonl`);
  const countPersistedAgentRows = async () => {
    const contents = await fs.readFile(historyFile, 'utf8');
    return contents.split(/\r?\n/).filter(line => {
      if (!line) return false;
      const row = JSON.parse(line);
      return row.role === 'user' && String(row.content).includes(recoveryLabel);
    }).length;
  };
  assert.equal(await countPersistedAgentRows(), 1,
    'the reservation boundary appends one durable agent history row');

  const socketsBeforeCrash = socketConnectionCount;
  await stop();
  await start();
  await poll(() => Promise.resolve(socketConnectionCount), count => count > socketsBeforeCrash,
    'the same Chromium page reconnects after Pan process recovery', 30000);
  await select('E2E-B');
  assert.ok(!(await canonical(ids['E2E-B'])).some(row =>
    String(row.content).includes(recoveryLabel)),
  'the message queued for A never enters manager Session B history');
  assert.equal((await state()).rows.some(row => String(row.content).includes(recoveryLabel)), false,
    'switching to B does not project A queue history into the selected Session');
  assert.deepEqual(projection(await canonical(ids['E2E-B'])),
    projection(managerHistoryBeforeRecovery), 'manager Session B history stays unchanged');
  await select('E2E-A');
  await poll(() => api(`/api/sessions/${ids['E2E-A']}`),
    s => s.lastResult?.result?.includes(recoveryLabel),
    'recovered agent message reaches CLI and completes');
  assert.equal(await countPersistedAgentRows(), 1,
    'reserved-state recovery does not append a second JSONL user row');
  const cliReceiptFile = path.join(runtime, 'crash-recovery-cli-inputs.jsonl');
  const cliReceipts = await fs.readFile(cliReceiptFile, 'utf8');
  assert.equal(cliReceipts.trim().split(/\r?\n/).length, 1,
    'the task crosses the provider hand-off exactly once after restart');
  const canonicalRecoveryRows = await canonical(ids['E2E-A']);
  assert.equal(canonicalRecoveryRows.filter(row => row.role === 'user'
    && String(row.content).includes(recoveryLabel)).length, 1,
  'HTTP history contains one copy of the recovered agent message');
  const recoveryState = await state();
  assert.equal(recoveryState.rows.filter(row => row.role === 'user'
    && String(row.content).includes(recoveryLabel)).length, 1,
  'Zustand contains one copy of the recovered agent message');
  const recoveryDomRows = await page.locator('main [data-index]').evaluateAll((nodes, label) => nodes
    .filter(node => node.querySelector('.msg.user')?.textContent?.includes(label)).length,
  recoveryLabel);
  const recoveryDomAssistantRows = await page.locator('main [data-index]').evaluateAll((nodes, label) => nodes
    .filter(node => node.querySelector('.msg.assistant')?.textContent?.includes(label)).length,
  recoveryLabel);
  assert.equal(recoveryDomRows, 1, 'Chromium renders one copy of the recovered agent user message');
  assert.equal(recoveryDomAssistantRows, 1, 'Chromium renders one copy of the recovered assistant reply');
  await equalCanonical('agent hand-off crash recovery and transcript identity');
  evidence.stages.push({
    label: 'agent history receipt survives process crash before CLI hand-off',
    historyRows: await countPersistedAgentRows(),
    cliReceipts: cliReceipts.trim().split(/\r?\n/).length,
    canonicalRows: canonicalRecoveryRows.length,
    domUserRows: recoveryDomRows,
    domAssistantRows: recoveryDomAssistantRows,
  });

  await select('E2E-A');
  await page.reload();
  await select('E2E-A');
  await equalCanonical('browser reload');
  await stop();
  await start();
  await sleep(3000);
  await select('E2E-A');
  await equalCanonical('cold server restart and websocket reconnect');
  await page.screenshot({ path: path.join(runtime, 'final.png') });
  assert.deepEqual(evidence.errors, []);
  evidence.pass = true;
} catch (error) {
  evidence.failure = String(error.stack || error);
  if (page) {
    evidence.failedState = await state().catch(() => null);
    evidence.transcriptTrace = await page.evaluate(() => window.__transcriptTrace).catch(() => null);
    await page.screenshot({ path: path.join(runtime, 'failure.png') }).catch(() => {});
  }
  console.error(error);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  await stop();
  await freePort();
  evidence.cleanup = { portFree: true };
  await fs.writeFile(path.join(runtime, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log('EVIDENCE', runtime);
}
