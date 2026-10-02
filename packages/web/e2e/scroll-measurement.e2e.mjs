/* global window, document, NodeFilter, WheelEvent, requestAnimationFrame, setTimeout, process, fetch, console */
/* Real Chromium geometry and ResizeObserver; disposable HTTP/WS fixture only. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { chromium } from '@playwright/test';

const root = path.resolve(import.meta.dirname, '../../..');
const port = 8797;
const url = `http://127.0.0.1:${port}`;
const output = path.resolve(import.meta.dirname, '../test-results', `scroll-measurement-${Date.now()}`);
const historyCount = Number(process.env.PAN_SCROLL_HISTORY_COUNT || 200);
const viewportWidth = Number(process.env.PAN_SCROLL_VIEWPORT_WIDTH || 1120);
assert.ok(Number.isInteger(historyCount) && historyCount >= 200 && historyCount <= 20000);
assert.ok(Number.isInteger(viewportWidth) && viewportWidth >= 320 && viewportWidth <= 3840);
await fs.mkdir(output, { recursive: true });
await new Promise((resolve, reject) => {
  const probe = net.createServer();
  probe.once('error', reject);
  probe.listen(port, '127.0.0.1', () => probe.close(resolve));
});
const server = spawn(process.env.PAN_TEST_PYTHON || 'D:/project/Pan-main/.venv/Scripts/python.exe',
  [path.join(import.meta.dirname, 'server.py')], {
    cwd: root, windowsHide: true,
    env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: output },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
let logs = '';
server.stdout.on('data', chunk => { logs += chunk; });
server.stderr.on('data', chunk => { logs += chunk; });
const report = { output, port, historyCount, viewportWidth,
  mode: process.env.PAN_SCROLL_RECORD_ONLY === '1' ? 'record' : 'assert', scenarios: [], errors: [] };
let browser;
try {
  const deadline = Date.now() + 30000;
  let ready = false;
  while (Date.now() < deadline) {
    try { ready = (await fetch(`${url}/api/sessions?summary=1`)).ok; } catch { /* starting */ }
    if (ready) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(ready, `isolated server did not start: ${logs.slice(-2000)}`);
  const identity = JSON.parse(await fs.readFile(path.join(output, 'server-identity.json'), 'utf8'));
  assert.equal(identity.port, port);
  assert.equal(path.resolve(identity.checkout), root);
  browser = await chromium.launch({ headless: true });
  report.chromium = browser.version();
  const page = await browser.newPage({ viewport: { width: 1120, height: 900 } });
  page.on('pageerror', error => report.errors.push(error.message));
  await page.goto(`${url}/react/?panE2E=1`);
  await page.locator('[data-session-card-id]').filter({ hasText: 'Chat Stream' }).first().click();
  await page.waitForFunction(() => {
    const state = window.__panSessionStore?.getState();
    return state?.currentSessionId && !state.historyLoading && state.currentMessages.length > 0;
  });
  await page.evaluate(count => {
    const store = window.__panSessionStore;
    const messages = Array.from({ length: count }, (_, i) => ({
      role: 'assistant', messageId: `size-row-${i}`,
      content: `Reading marker ${i}\n\n${'A paragraph that wraps across several lines. '.repeat(i % 5 + 1)}`,
    }));
    store.setState({ currentMessages: messages, hasMoreMessages: false, historyLoading: false });
  }, historyCount);
  await page.setViewportSize({ width: viewportWidth, height: 900 });
  const scroller = page.locator('.chat-view-stage .overflow-auto').first();
  await page.waitForTimeout(600);
  await scroller.hover();
  for (let i = 0; i < 4; i++) { await page.mouse.wheel(0, -240); await page.waitForTimeout(150); }
  await page.waitForTimeout(400);
  // Controlled block growth models a late image/disclosure measurement. It
  // changes geometry without changing the selected text or store identities.
  for (const location of ['above', 'spanning']) {
    let spanningKey;
    if (location === 'spanning') {
      spanningKey = await scroller.evaluate(el => {
        const viewport = el.getBoundingClientRect();
        const paragraph = Array.from(el.querySelectorAll('p')).find(p => {
          const rect = p.getBoundingClientRect();
          return rect.height > 40 && rect.top > viewport.top && rect.bottom < viewport.bottom;
        });
        if (!paragraph) throw new Error('No multiline paragraph for spanning-row case');
        el.dispatchEvent(new WheelEvent('wheel', { deltaY: 1, bubbles: true }));
        el.scrollTop += paragraph.getBoundingClientRect().top - el.getBoundingClientRect().top + 8;
        return paragraph.closest('[data-scroll-anchor-key]').dataset.scrollAnchorKey;
      });
      await page.waitForTimeout(100);
    }
    const scenario = await scroller.evaluate(async (el, { location, spanningKey }) => {
      const viewport = el.getBoundingClientRect();
      const rows = Array.from(el.querySelectorAll('[data-scroll-anchor-key]'));
      const target = location === 'above'
        ? rows.filter(row => row.getBoundingClientRect().bottom <= viewport.top).at(-1)
        : rows.find(row => row.dataset.scrollAnchorKey === spanningKey);
      if (!target) throw new Error(`No ${location} measurement target`);
      if (location === 'spanning') {
        const rect = target.getBoundingClientRect();
        if (!(rect.top < viewport.top && rect.bottom > viewport.top)) throw new Error('Target does not span the viewport edge');
      }
      const walker = document.createTreeWalker(location === 'spanning' ? target : el, NodeFilter.SHOW_TEXT);
      let marker;
      let textNode;
      while (!marker && (textNode = walker.nextNode())) {
        for (let offset = 0; offset < textNode.length; offset += 5) {
          const range = document.createRange();
          range.setStart(textNode, offset);
          range.setEnd(textNode, Math.min(offset + 1, textNode.length));
          const rect = range.getBoundingClientRect();
          if (rect.width > 0 && rect.top >= viewport.top + 1 && rect.bottom < viewport.bottom) {
            marker = range;
            break;
          }
        }
      }
      if (!marker) throw new Error('No visible reading marker');
      const start = marker.getBoundingClientRect().top;
      const frames = [];
      const beforeTop = el.scrollTop;
      target.style.height = `${target.getBoundingClientRect().height + 100}px`;
      for (let i = 0; i < 20; i++) {
        // rAF itself runs before ResizeObserver. Sample in a subsequent task
        // so an intermediate pre-observer layout is not counted as a paint.
        await new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 0)));
        frames.push({ offset: marker.getBoundingClientRect().top - start, scrollTop: el.scrollTop });
      }
      return { location, beforeTop, targetKey: target.dataset.scrollAnchorKey, frames,
        maxDrift: Math.max(...frames.map(frame => Math.abs(frame.offset))),
        finalDrift: frames.at(-1).offset };
    }, { location, spanningKey });
    report.scenarios.push(scenario);
    console.log(JSON.stringify({ location, maxDrift: scenario.maxDrift, finalDrift: scenario.finalDrift }));
    if (process.env.PAN_SCROLL_RECORD_ONLY !== '1') {
      assert.ok(scenario.maxDrift <= 2, `${location} row resize moves reading marker: ${JSON.stringify(scenario)}`);
    }
  }
  const upward = await scroller.evaluate(async el => {
    const frames = [];
    const intervals = [];
    let previousFrame;
    for (let i = 0; i < 120; i++) {
      const viewport = el.getBoundingClientRect();
      const marker = Array.from(el.querySelectorAll('p')).find(p => {
        const rect = p.getBoundingClientRect();
        return rect.top > viewport.top + viewport.height / 2 && rect.bottom < viewport.bottom;
      });
      if (!marker || el.scrollTop < 200) break;
      const start = marker.getBoundingClientRect().top;
      const beforeTop = el.scrollTop;
      el.dispatchEvent(new WheelEvent('wheel', { deltaY: -60, bubbles: true }));
      el.scrollTop -= 60;
      const frameTime = await new Promise(resolve => requestAnimationFrame(time => setTimeout(() => resolve(time), 0)));
      if (previousFrame !== undefined) intervals.push(frameTime - previousFrame);
      previousFrame = frameTime;
      if (marker.isConnected) frames.push({ step: i, drift: marker.getBoundingClientRect().top - start - 60,
        beforeTop, afterTop: el.scrollTop, markerKey: marker.closest('[data-scroll-anchor-key]').dataset.scrollAnchorKey });
    }
    intervals.sort((a, b) => a - b);
    return { location: 'upward-first-measurement', frames,
      frameP95: intervals[Math.floor(intervals.length * 0.95)],
      maxDrift: Math.max(0, ...frames.map(frame => Math.abs(frame.drift))) };
  });
  report.scenarios.push(upward);
  console.log(JSON.stringify({ location: upward.location, maxDrift: upward.maxDrift, samples: upward.frames.length }));
  assert.ok(upward.frames.length > 20, 'enough upward measurement samples');
  if (process.env.PAN_SCROLL_RECORD_ONLY !== '1') assert.ok(upward.maxDrift <= 2, `upward scroll jumps: ${JSON.stringify(upward)}`);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  const metrics = await cdp.send('Performance.getMetrics');
  const counters = await cdp.send('Memory.getDOMCounters');
  report.resources = { ...counters,
    jsHeapUsedBytes: metrics.metrics.find(metric => metric.name === 'JSHeapUsedSize')?.value,
    mountedRows: await scroller.locator('[data-scroll-anchor-key][data-index]').count() };
  assert.ok(report.resources.mountedRows < 80, 'long history retains a bounded rendered window');
  await cdp.detach();
  assert.deepEqual(report.errors, []);
  report.passed = report.scenarios.every(scenario => scenario.maxDrift <= 2);
} catch (error) {
  report.passed = false;
  report.failure = error.stack;
  throw error;
} finally {
  await browser?.close();
  // Shut down only the server launched above, using its disposable identity.
  try {
    const identity = JSON.parse(await fs.readFile(path.join(output, 'server-identity.json'), 'utf8'));
    if (identity.port === port && path.resolve(identity.checkout) === root) process.kill(identity.pid);
  } catch { if (server.exitCode === null) server.kill(); }
  let portFree = false;
  for (let i = 0; i < 30; i++) {
    const listening = await new Promise(resolve => {
      const socket = net.connect({ host: '127.0.0.1', port });
      socket.once('connect', () => { socket.destroy(); resolve(true); });
      socket.once('error', () => { socket.destroy(); resolve(false); });
      socket.setTimeout(300, () => { socket.destroy(); resolve(true); });
    });
    if (!listening) { portFree = true; break; }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  report.cleanup = { portFree };
  await fs.writeFile(path.join(output, 'server.log'), logs);
  await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(report, null, 2));
  console.log(`Evidence: ${output}`);
  assert.ok(portFree, 'isolated fixture listener is released');
}
