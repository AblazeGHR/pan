// Isolated synthetic browser regression. Never connects to a Pan backend.
// PAN_PERF_DIST must point to a build of this checkout (outside practical).
/* global process, fetch, setTimeout, URL, window, document, navigator,
   PerformanceObserver, performance, requestAnimationFrame, console */
import assert from 'node:assert/strict';
import net from 'node:net';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { chromium } from '@playwright/test';

const dist = process.env.PAN_PERF_DIST;
assert.ok(dist, 'Set PAN_PERF_DIST to an isolated build directory');
const probe = net.createServer();
await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
const port = probe.address().port;
await new Promise(resolve => probe.close(resolve));
const root = path.resolve(import.meta.dirname, '..');
const server = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'),
  'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort', '--outDir', dist],
{ cwd: root, windowsHide: true, stdio: 'pipe' });
let browser;
try {
  const origin = `http://127.0.0.1:${port}`;
  let ready = false;
  for (let n = 0; n < 100; n++) {
    try { ready = (await fetch(`${origin}/react/`)).ok; } catch { /* starting */ }
    if (ready) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(ready, 'owned preview starts');
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    return url.origin === origin && !url.pathname.startsWith('/api/')
      ? route.continue() : route.abort();
  });
  await page.goto(`${origin}/react/?mock=1&panE2E=1`);
  await page.locator('[data-session-card-id]').first().click();
  await page.waitForFunction(() => window.__panSessionStore?.getState().currentSessionId);
  const results = [];
  for (const language of ['diff', 'typescript']) {
    const result = await page.evaluate(async language => {
      const store = window.__panSessionStore;
      const tasks = [];
      const frames = [];
      const observer = new PerformanceObserver(list => tasks.push(...list.getEntries().map(e => e.duration)));
      observer.observe({ type: 'longtask', buffered: false });
      let running = true, last = performance.now();
      function frame(now) { frames.push(now - last); last = now; if (running) requestAnimationFrame(frame); }
      requestAnimationFrame(frame);
      const lines = Array.from({ length: 2000 }, (_, i) => language === 'diff'
        ? `+const value${i} = { name: "line ${i}", enabled: true };`
        : `export const value${i} = { name: "line ${i}", enabled: true };`);
      const started = performance.now();
      for (let update = 0; update < 12; update++) {
        const content = '```' + language + '\n' + lines.join('\n') + `\n// delta ${update}\n` + '```';
        store.setState({ currentMessages: [{ role: 'assistant', content, nativeItemId: 'perf-delta', blockId: 'perf-delta' }] });
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        const scroller = document.querySelector('[data-testid="large-code-window"]');
        if (scroller) scroller.scrollTop = update % 2 ? 0 : scroller.scrollHeight;
      }
      await new Promise(resolve => setTimeout(resolve, 100));
      const codeWindow = document.querySelector('[data-testid="large-code-window"]');
      if (codeWindow) {
        codeWindow.scrollTop = codeWindow.scrollHeight;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }
      running = false;
      observer.disconnect();
      const sorted = frames.slice().sort((a, b) => a - b);
      return { language, elapsedMs: performance.now() - started,
        longTasks: tasks, maxFrameMs: Math.max(...frames),
        p95FrameMs: sorted[Math.floor(sorted.length * .95)],
        codeNodes: document.querySelectorAll('pre *').length,
        virtualLines: document.querySelectorAll('[data-code-line]').length,
        fullTextPresent: document.body.textContent.includes('value1999') };
    }, language);
    if (process.env.PAN_PERF_ASSERT === '1') {
      const window = page.getByTestId('large-code-window');
      await window.hover();
      const before = await window.evaluate(el => el.scrollTop);
      await page.mouse.wheel(0, -600);
      await page.waitForFunction(old => document.querySelector('[data-testid="large-code-window"]').scrollTop < old, before);
      await page.mouse.wheel(0, 100_000);
      await page.waitForFunction(() => document.querySelector('[data-code-line="1999"]'));
      await page.evaluate(() => {
        Object.defineProperty(navigator, 'clipboard', { configurable: true,
          value: { writeText: async text => { window.__copiedCode = text; } } });
      });
      await page.getByRole('button', { name: 'Copy code' }).click();
      assert.ok(await page.evaluate(() => window.__copiedCode.includes('value1999') && window.__copiedCode.endsWith('// delta 11')),
        'copy contains all lines and the final delta');
      const preserved = await page.evaluate(async () => {
        const viewport = document.querySelector('[data-testid="large-code-window"]');
        viewport.scrollTop = 1000;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        const before = viewport.scrollTop;
        const store = window.__panSessionStore;
        const message = store.getState().currentMessages[0];
        const content = message.content.replace('// delta 11', '// completed delta');
        store.setState({ currentMessages: [{ ...message, content }] });
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return { before, after: viewport.scrollTop, content: store.getState().currentMessages[0].content };
      });
      assert.equal(preserved.before, preserved.after, 'a delta preserves the internal reading position');
      assert.ok(preserved.content.includes('// completed delta'));
      result.wheelAndCopyPassed = true;
      result.deltaPreservesReadingPosition = true;
    }
    results.push(result);
  }
  assert.deepEqual(errors, []);
  if (process.env.PAN_PERF_SCREENSHOT) await page.screenshot({ path: process.env.PAN_PERF_SCREENSHOT });
  if (process.env.PAN_PERF_ASSERT === '1') {
    for (const result of results) {
      assert.ok(result.virtualLines > 0 && result.virtualLines < 100, JSON.stringify(result));
      assert.ok(result.codeNodes < 200, JSON.stringify(result));
      assert.ok(result.fullTextPresent, 'last code line can be reached by scrolling');
    }
  }
  console.log(JSON.stringify({ synthetic: true, results, errors }, null, 2));
} finally {
  await browser?.close();
  server.kill();
  await new Promise(resolve => server.exitCode !== null ? resolve() : server.once('exit', resolve));
}
