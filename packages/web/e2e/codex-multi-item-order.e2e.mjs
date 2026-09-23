/*
 * Real FastAPI/Worker/JSONL/WS/Chromium chain for the 2026-09-23 trace:
 * five different Codex assistant items in one turn, with tools between them.
 * The provider boundary is a deterministic local CLI, not a Codex account.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import process from 'node:process';
import { spawn, spawnSync } from 'node:child_process';
import { chromium } from '@playwright/test';

const root = path.resolve(import.meta.dirname, '../../..');
const runtime = path.resolve(import.meta.dirname, '../test-results', `codex-multi-item-${Date.now()}`);
const python = process.env.PAN_E2E_PYTHON || 'D:/project/Pan/.venv/Scripts/python.exe';
const port = 8765;
const base = `http://127.0.0.1:${port}`;
const label = 'codex-five-story-items';
const evidence = { root, runtime, port, label, stages: [], errors: [] };
await fs.mkdir(runtime, { recursive: true });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let server, browser, page;

async function freePort() {
  await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', reject);
    probe.listen(port, '127.0.0.1', () => probe.close(resolve));
  });
}
async function poll(read, check, message, timeout = 20000) {
  let last;
  for (const deadline = Date.now() + timeout; Date.now() < deadline; await sleep(40)) {
    last = await read();
    if (check(last)) return last;
  }
  throw new Error(`${message}: ${JSON.stringify(last).slice(0, 1600)}`);
}
async function api(route, body) {
  const response = await fetch(base + route, body ? {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  } : {});
  assert.equal(response.status, 200, route);
  const data = await response.json();
  assert.ok(!data.error && data.status !== 'error', `${route}: ${JSON.stringify(data)}`);
  return data;
}
async function start() {
  await freePort();
  server = spawn(python, [path.join(import.meta.dirname, 'frontend-full.server.py')], {
    cwd: root, windowsHide: true,
    env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const logs = [];
  server.stdout.on('data', chunk => logs.push(chunk));
  server.stderr.on('data', chunk => logs.push(chunk));
  const pid = server.pid;
  server.on('exit', () => fs.writeFile(path.join(runtime, `server-${pid}.log`), Buffer.concat(logs)));
  await poll(async () => { try { return await api('/api/sessions?summary=1'); } catch { return null; } },
    Boolean, 'isolated server ready', 30000);
  const identity = JSON.parse(await fs.readFile(path.join(runtime, 'server-identity.json'), 'utf8'));
  assert.equal(path.resolve(identity.checkout), root);
  assert.equal(identity.port, port);
  evidence.processes = [identity];
}
async function stop() {
  if (!server || server.exitCode !== null) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true });
  } else server.kill('SIGTERM');
  await poll(() => Promise.resolve(server.exitCode), code => code !== null, 'owned server stopped');
}
const state = () => page.evaluate(() => {
  const s = window.__panSessionStore.getState();
  return { sessionId: s.currentSessionId, rows: s.currentMessages.map(m => ({
    role: m.role, content: m.content, nativeItemId: m.nativeItemId,
  })) };
});
async function history(id) {
  const result = [];
  let before = 0;
  for (;;) {
    const page = await api(`/api/sessions/${id}/history?before=${before}&limit=500`);
    result.unshift(...page.history);
    if (page.start === 0) return result;
    assert.ok(before === 0 || page.start < before);
    before = page.start;
  }
}
function itemRows(rows) {
  return rows.filter(row => row.nativeItemId?.startsWith(`story:${label}:`)
    || row.nativeItemId?.startsWith(`tool:${label}:`));
}

try {
  await start();
  const listing = await api('/api/sessions?summary=1');
  const sessions = listing.sessions || listing;
  const id = sessions.find(session => session.name === 'E2E-A')?.id;
  assert.ok(id);
  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('pageerror', error => evidence.errors.push(String(error)));
  await page.goto(`${base}/react/?panE2E=1`, { waitUntil: 'domcontentloaded' });
  await page.locator('[data-session-card-id]').filter({ hasText: 'E2E-A' }).first().click();
  await poll(state, s => s.sessionId === id, 'selected Codex Session');
  await api('/api/assign', { sessionId: id, text: label });

  for (let story = 2; story <= 5; story += 1) {
    await poll(() => fs.stat(path.join(runtime, `five-story-ready-${story}`))
      .then(() => true).catch(() => false), Boolean, `provider paused at story ${story}`);
    const current = await poll(state, s => itemRows(s.rows).some(row =>
      row.nativeItemId === `story:${label}:${story}` && row.content === `story-${story}-first`),
    `new native item ${story} visible during delta`);
    const items = itemRows(current.rows);
    assert.equal(items.length, story * 2 - 1, `story ${story}: one row per native item`);
    assert.deepEqual(items.map(row => row.nativeItemId), Array.from({ length: story }, (_, index) => {
      const number = index + 1;
      return number === 1
        ? [`story:${label}:1`]
        : [`tool:${label}:${number}`, `story:${label}:${number}`];
    }).flat());
    for (let previous = 1; previous < story; previous += 1) {
      assert.equal(items.find(row => row.nativeItemId === `story:${label}:${previous}`)?.content,
        `story-${previous}-first\nstory-${previous}-final`,
      `story ${story} delta does not overwrite completed item ${previous}`);
    }
    const domCopies = await page.locator('main [data-index]').evaluateAll((nodes, prefix) =>
      nodes.filter(node => node.querySelector('.msg.assistant')?.textContent?.includes(prefix)).length,
    `story-${story}-first`);
    assert.equal(domCopies, 1, `Chromium displays new story ${story} once`);
    evidence.stages.push({ story, visibleItems: items.length, domCopies });
    await fs.writeFile(path.join(runtime, `five-story-release-${story}`), 'release');
  }

  await poll(() => api(`/api/sessions/${id}`), s =>
    s.lastResult?.result?.includes('story-5-final'), 'five-item turn completed');
  const canonical = await history(id);
  const durableStories = canonical.filter(row => row.role === 'assistant'
    && /^story-[1-5]-first\nstory-[1-5]-final$/.test(row.content));
  assert.equal(durableStories.length, 5, 'JSONL/HTTP history has five assistant items');
  const finalState = await poll(state, s =>
    s.rows.filter(row => row.role === 'assistant'
      && /^story-[1-5]-first\nstory-[1-5]-final$/.test(row.content)).length === 5,
  'Zustand converged to five assistant items');
  const finalDom = await page.locator('main [data-index]').evaluateAll(nodes => nodes
    .filter(node => /^story-[1-5]-first\s+story-[1-5]-final$/.test(
      node.querySelector('.msg.assistant')?.textContent ?? '')).length);
  assert.equal(finalDom, 5, 'Chromium shows all five items once after completion');
  await page.reload();
  await page.locator('[data-session-card-id]').filter({ hasText: 'E2E-A' }).first().click();
  const reloaded = await poll(state, s => s.sessionId === id && s.rows.some(row =>
    row.content.includes('story-5-final')), 'reloaded history');
  assert.equal(reloaded.rows.filter(row => row.role === 'assistant'
    && /^story-[1-5]-first\nstory-[1-5]-final$/.test(row.content)).length, 5);
  assert.deepEqual(evidence.errors, []);
  evidence.pass = true;
  evidence.assertions = { durableStories: durableStories.length, finalDom,
    reloadedStories: 5, stages: evidence.stages.length };
} catch (error) {
  evidence.failure = String(error.stack || error);
  if (page) await page.screenshot({ path: path.join(runtime, 'failure.png') }).catch(() => {});
  console.error(error);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  await stop();
  let portFree = false;
  try { await freePort(); portFree = true; } catch { /* preserve cleanup evidence */ }
  evidence.cleanup = { portFree };
  await fs.writeFile(path.join(runtime, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log('EVIDENCE', runtime);
}
