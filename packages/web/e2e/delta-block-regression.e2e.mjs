/* global window, document, navigator, PerformanceObserver, fetch, URL, console */
// Production UI -> real HTTP/queue/Worker -> deterministic Codex CLI -> WS -> UI.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import process from 'node:process';
import { spawn, spawnSync } from 'node:child_process';
import { Buffer } from 'node:buffer';
import { setTimeout } from 'node:timers/promises';
import { chromium } from '@playwright/test';

const root = path.resolve(import.meta.dirname, '../../..');
const runtime = path.resolve(import.meta.dirname, '../test-results', `delta-regression-${Date.now()}`);
const port = 8765;
const base = `http://127.0.0.1:${port}`;
const python = process.env.PAN_E2E_PYTHON || 'D:/project/Pan/.venv/Scripts/python.exe';
const evidence = { root, runtime, port, stages: [], errors: [] };
await fs.mkdir(runtime, { recursive: true });
let server, browser, page;
async function poll(read, check, label, timeout = 20000) {
  let last;
  for (const end = Date.now() + timeout; Date.now() < end; await setTimeout(50)) {
    last = await read();
    if (check(last)) return last;
  }
  throw new Error(`${label}: ${JSON.stringify(last).slice(0, 1000)}`);
}
async function freePort() {
  await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', reject);
    probe.listen(port, '127.0.0.1', () => probe.close(resolve));
  });
}
async function api(route) {
  const response = await fetch(base + route);
  assert.equal(response.status, 200);
  return response.json();
}
async function gate(name) {
  await poll(() => fs.stat(path.join(runtime, name)).then(() => true).catch(() => false), Boolean, name);
}
async function release(name) { await fs.writeFile(path.join(runtime, name), 'release'); }
async function select(name) {
  const card = page.locator('[data-session-card-id]').filter({ hasText: name }).first();
  const id = await card.getAttribute('data-session-card-id');
  await card.click();
  await page.waitForFunction(id => window.__panSessionStore.getState().currentSessionId === id, id);
  return id;
}
async function send(text) {
  await page.locator('[contenteditable="true"]').first().fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}
async function stop() {
  if (!server || server.exitCode !== null) return;
  if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true });
  else server.kill('SIGTERM');
  await poll(() => Promise.resolve(server.exitCode), code => code !== null, 'owned fixture stops');
}
try {
  await freePort();
  server = spawn(python, [path.join(import.meta.dirname, 'frontend-full.server.py')], {
    cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime },
  });
  const logs = [];
  server.stdout.on('data', chunk => logs.push(chunk));
  server.stderr.on('data', chunk => logs.push(chunk));
  server.on('exit', () => fs.writeFile(path.join(runtime, 'server.log'), Buffer.concat(logs)));
  await poll(async () => { try { return await api('/api/sessions?summary=1'); } catch { return null; } }, Boolean, 'fixture ready');
  evidence.identity = JSON.parse(await fs.readFile(path.join(runtime, 'server-identity.json'), 'utf8'));
  assert.equal(path.resolve(evidence.identity.checkout), root);
  assert.equal(path.resolve(evidence.identity.sessionDir), path.join(runtime, 'sessions'));
  assert.equal(evidence.identity.port, port);
  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('pageerror', error => evidence.errors.push(String(error)));
  await page.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
  await page.goto(`${base}/react/?panE2E=1`);
  const id = await select('E2E-A');
  for (const language of ['typescript', 'diff']) {
    const label = `large-code-regression-${language}`;
    await send(label);
    await gate(`${label}-small-ready`);
    const reply = page.locator('.message-row-assistant').filter({ has: page.getByRole('heading', { name: label, exact: true }) });
    await reply.waitFor();
    assert.equal(await reply.getByTestId('large-code-window').count(), 0, 'small code retains normal layout');
    if (language === 'typescript') assert.ok(await reply.locator('.hljs-keyword').count() > 0, 'small code retains highlighting');
    await release(`${label}-small-release`);
    await gate(`${label}-large-ready`);
    const code = reply.getByTestId('large-code-window');
    await code.waitFor();
    assert.ok(await code.locator('[data-code-line]').count() < 100);
    await select('E2E-B');
    await select('E2E-A');
    await code.waitFor();
    await code.evaluate(el => { el.scrollTop = 1000; });
    await page.waitForTimeout(100);
    const oldTop = await code.evaluate(el => el.scrollTop);
    await page.evaluate(() => {
      window.__deltaLongTasks = [];
      window.__deltaObserver = new PerformanceObserver(list => window.__deltaLongTasks.push(...list.getEntries().map(e => e.duration)));
      window.__deltaObserver.observe({ type: 'longtask' });
    });
    await release(`${label}-large-release`);
    await gate(`${label}-finish-ready`);
    await poll(() => page.evaluate(label => window.__panSessionStore.getState().currentMessages.find(m => m.content.startsWith(`# ${label}`))?.content.includes('value1999'), label), Boolean, 'all deltas arrive');
    assert.equal(await code.evaluate(el => el.scrollTop), oldTop, 'live updates preserve internal reading position');
    const tasks = await page.evaluate(() => { window.__deltaObserver.disconnect(); return window.__deltaLongTasks; });
    await release(`${label}-finish-release`);
    const final = await poll(() => api(`/api/sessions/${id}`), s => s.lastResult?.result?.includes(label), 'durable final');
    const text = final.lastResult.result;
    await poll(() => page.evaluate(text => window.__panSessionStore.getState().currentMessages.filter(m => m.role === 'assistant' && m.content === text).length, text), n => n === 1, 'one canonical completed reply');
    const history = await api(`/api/sessions/${id}/history?limit=50`);
    assert.equal(history.history.filter(m => m.role === 'assistant' && m.content === text).length, 1);
    await page.reload();
    await select('E2E-A');
    await poll(() => page.evaluate(text => window.__panSessionStore.getState().currentMessages.some(m => m.content === text), text), Boolean, 'reload matches durable final');
    await code.waitFor();
    await code.hover();
    await page.mouse.wheel(0, 100_000);
    await code.locator('[data-code-line="1999"]').waitFor();
    const bounded = await code.locator('[data-code-line]').count();
    assert.ok(bounded > 0 && bounded < 100);
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', { configurable: true,
        value: { writeText: async text => { window.__copiedCode = text; } } });
    });
    await reply.getByRole('button', { name: 'Copy code', exact: true }).click();
    const copied = await page.evaluate(() => window.__copiedCode);
    assert.equal(copied, text.split(`\`\`\`${language}\n`)[1].split('\n```')[0]);
    const stage = { label, historyCopies: 1, mountedCodeLines: bounded, clipboardMatchesCanonical: true,
      liveReadingPositionPreserved: true, longTasks: tasks };
    evidence.stages.push(stage);
    console.log('PASS', stage);
  }
  await send('thinking-scroll-regression');
  await gate('thinking-ready');
  await page.getByRole('button', { name: 'thinking', exact: true }).last().click();
  const thinking = page.getByTestId('thinking-content-window').last().locator('div').first();
  await thinking.evaluate(el => { el.scrollTop = 500; });
  await thinking.hover();
  await page.mouse.wheel(0, -200);
  await page.waitForTimeout(100);
  const before = await thinking.evaluate(el => el.scrollTop);
  await release('thinking-release');
  await gate('thinking-appended');
  await poll(() => page.evaluate(() => window.__panSessionStore.getState().currentMessages.some(m => m.role === 'thinking' && m.content.includes('Thought 80:'))), Boolean, 'thinking delta visible');
  await page.waitForTimeout(100);
  assert.equal(await thinking.evaluate(el => el.scrollTop), before);
  await release('thinking-finish');
  await poll(() => api(`/api/sessions/${id}`), s => s.lastResult?.result === 'Thinking scroll fixture completed.', 'thinking task completes');
  evidence.stages.push({ label: 'thinking wheel position survives real delta', before });
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileCode = page.getByTestId('large-code-window').last();
  await mobileCode.scrollIntoViewIfNeeded();
  await mobileCode.focus();
  await page.keyboard.press('Home');
  await poll(() => mobileCode.evaluate(el => el.scrollTop), top => top === 0, 'keyboard Home reaches first code line');
  await page.keyboard.press('End');
  await mobileCode.locator('[data-code-line="1999"]').waitFor();
  const mobile = await mobileCode.evaluate(el => {
    const rect = el.getBoundingClientRect();
    return { viewport: window.innerWidth, left: rect.left, right: rect.right,
      bodyWidth: document.body.scrollWidth, mountedLines: el.querySelectorAll('[data-code-line]').length };
  });
  assert.ok(mobile.left >= 0 && mobile.right <= mobile.viewport + 1, 'code stays inside narrow viewport');
  assert.ok(mobile.bodyWidth <= mobile.viewport + 1, 'wide code does not stretch the page');
  assert.ok(mobile.mountedLines < 100);
  evidence.stages.push({ label: '390px code window and keyboard scrolling', ...mobile });
  await page.screenshot({ path: path.join(runtime, 'final.png') });
  assert.deepEqual(evidence.errors, []);
  evidence.pass = true;
} catch (error) {
  evidence.failure = String(error.stack || error);
  await page?.screenshot({ path: path.join(runtime, 'failure.png') }).catch(() => {});
  console.error(error);
  process.exitCode = 1;
} finally {
  await browser?.close();
  await stop();
  await freePort();
  evidence.cleanup = { portFree: true };
  await fs.writeFile(path.join(runtime, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log('EVIDENCE', runtime);
}
