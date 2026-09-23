/*
 * Focused real-browser regression for a persisted MA message duplicated when
 * Pan restarts after writing history/RESERVED but before the CLI hand-off.
 * It uses the real FastAPI, Session JSONL, Worker, WebSocket and Chromium app;
 * only the provider process is deterministic and local.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import process from 'node:process';
import { spawn, spawnSync } from 'node:child_process';
import { chromium } from '@playwright/test';

const root = path.resolve(import.meta.dirname, '../../..');
const runtime = path.resolve(import.meta.dirname, '../test-results', `message-recovery-${Date.now()}`);
const python = process.env.PAN_E2E_PYTHON || 'D:/project/Pan/.venv/Scripts/python.exe';
const port = 8765;
const base = `http://127.0.0.1:${port}`;
const label = 'crash-recovery-history-idempotency';
const taskId = 'e2e-handoff-recovery-context';
const evidence = { root, runtime, port, label, stages: [], errors: [] };
await fs.mkdir(runtime, { recursive: true });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let server;
let browser;
let context;
let page;
let socketCount = 0;

async function freePort() {
  await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', reject);
    probe.listen(port, '127.0.0.1', () => probe.close(resolve));
  });
}

async function poll(read, check, message, timeout = 15000) {
  let last;
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    last = await read();
    if (check(last)) return last;
    await sleep(40);
  }
  throw new Error(`${message}: ${JSON.stringify(last).slice(0, 1600)}`);
}

async function api(route, body) {
  const response = await fetch(base + route, body ? {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  } : {});
  assert.equal(response.status, 200, `${route}: HTTP ${response.status}`);
  const data = await response.json();
  assert.ok(!data.error && data.status !== 'error', `${route}: ${JSON.stringify(data)}`);
  return data;
}

async function start() {
  await freePort();
  server = spawn(python, [path.join(import.meta.dirname, 'frontend-full.server.py')], {
    cwd: root,
    windowsHide: true,
    env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const logs = [];
  server.stdout.on('data', chunk => logs.push(chunk));
  server.stderr.on('data', chunk => logs.push(chunk));
  const pid = server.pid;
  server.on('exit', () => fs.writeFile(
    path.join(runtime, `server-${pid}.log`), Buffer.concat(logs),
  ));
  await poll(async () => {
    try { return await api('/api/sessions?summary=1'); } catch { return null; }
  }, Boolean, 'isolated Pan server readiness', 30000);
  const identity = JSON.parse(await fs.readFile(path.join(runtime, 'server-identity.json'), 'utf8'));
  assert.equal(path.resolve(identity.checkout), root);
  assert.equal(identity.port, port);
  evidence.processes ??= [];
  evidence.processes.push(identity);
}

async function stop() {
  if (!server || server.exitCode !== null) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true });
  } else {
    server.kill('SIGTERM');
  }
  await poll(() => Promise.resolve(server.exitCode), code => code !== null, 'owned server exit');
}

async function history(sessionId) {
  const result = [];
  let before = 0;
  for (;;) {
    const page = await api(`/api/sessions/${sessionId}/history?before=${before}&limit=500`);
    result.unshift(...page.history);
    if (page.start === 0) return result;
    assert.ok(before === 0 || page.start < before, 'history pagination makes progress');
    before = page.start;
  }
}

async function readState() {
  return page.evaluate(() => {
    const state = window.__panSessionStore.getState();
    return { id: state.currentSessionId, rows: state.currentMessages,
      start: state.historyLoadEnd };
  });
}

async function select(name, id) {
  const response = page.waitForResponse(candidate =>
    candidate.url().includes(`/api/sessions/${id}/history`)
      && candidate.request().method() === 'GET', { timeout: 15000 });
  await page.locator('[data-session-card-id]').filter({ hasText: name }).first().click();
  await response;
  await poll(readState, state => state.id === id, `selected ${name}`);
}

async function countHistoryJsonl(sessionId) {
  const file = path.join(runtime, 'sessions', `${sessionId}.history.jsonl`);
  const contents = await fs.readFile(file, 'utf8');
  return contents.split(/\r?\n/).filter(line => {
    if (!line) return false;
    const row = JSON.parse(line);
    return row.role === 'user' && String(row.content).includes(label);
  }).length;
}

try {
  await start();
  const listing = await api('/api/sessions?summary=1');
  const sessions = listing.sessions || listing;
  const ids = Object.fromEntries(sessions.map(session => [session.name, session.id]));
  assert.ok(ids['E2E-A'] && ids['E2E-B']);

  browser = await chromium.launch({ headless: true });
  context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  page = await context.newPage();
  page.on('pageerror', error => evidence.errors.push(String(error)));
  page.on('websocket', socket => {
    try {
      if (new URL(socket.url()).pathname === '/ws') socketCount += 1;
    } catch { /* Ignore non-WS URLs. */ }
  });
  await page.goto(`${base}/react/?panE2E=1`, { waitUntil: 'domcontentloaded' });
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  const managerBefore = await history(ids['E2E-B']);

  // Hold one real Codex task open so agent_send captures its active taskId.
  await api('/api/assign', {
    sessionId: ids['E2E-A'],
    text: 'handoff-context-before-crash',
    source: 'agent',
    sourceSessionId: ids['E2E-B'],
    taskId,
  });
  await poll(() => fs.stat(path.join(runtime, 'handoff-context-ready'))
    .then(() => true).catch(() => false), Boolean, 'context task reached the real CLI');

  await api('/api/send', {
    sessionId: ids['E2E-A'],
    text: `////by agent\n${label}`,
    source: 'agent',
    sourceSessionId: ids['E2E-B'],
  });
  const sessionFile = path.join(runtime, 'sessions', `${ids['E2E-A']}.json`);
  const persisted = JSON.parse(await fs.readFile(sessionFile, 'utf8'));
  const queued = persisted.queue_pending.find(item => String(item.text).includes(label));
  assert.ok(queued, 'agent follow-up persists while the active task is running');
  assert.equal(queued.taskId, taskId);
  assert.equal(queued.taskIdSource, 'active');
  assert.equal(queued.sourceSessionId, ids['E2E-B']);

  await fs.writeFile(path.join(runtime, 'release-handoff-context'), 'release');
  await poll(() => api(`/api/sessions/${ids['E2E-A']}`), session =>
    session.lastResult?.result?.includes('handoff-context-before-crash'),
  'active task completes before queued agent follow-up is reserved');
  await poll(() => fs.stat(path.join(runtime, 'handoff-reservation-paused-once'))
    .then(() => true).catch(() => false), Boolean,
  'follow-up history and RESERVED receipt persist before CLI hand-off');
  assert.equal(await countHistoryJsonl(ids['E2E-A']), 1,
    'one agent history row exists at the crash boundary');

  const socketsBeforeCrash = socketCount;
  await stop();
  await start();
  await poll(() => Promise.resolve(socketCount), count => count > socketsBeforeCrash,
    'the same browser WebSocket reconnects after process restart', 30000);

  await select('E2E-B', ids['E2E-B']);
  assert.ok(!(await history(ids['E2E-B'])).some(row => String(row.content).includes(label)),
    'the manager Session history is not contaminated by its target');
  assert.equal((await readState()).rows.some(row => String(row.content).includes(label)), false,
    'the B transcript does not project A rows');
  assert.deepEqual(
    (await history(ids['E2E-B'])).map(row => [row.role, row.content]),
    managerBefore.map(row => [row.role, row.content]),
  );

  await select('E2E-A', ids['E2E-A']);
  await poll(() => api(`/api/sessions/${ids['E2E-A']}`), session =>
    session.lastResult?.result?.includes(label),
  'recovered agent task reaches the CLI and completes', 30000);
  assert.equal(await countHistoryJsonl(ids['E2E-A']), 1,
    'restart recovery keeps exactly one JSONL agent row');
  const cliReceipt = await fs.readFile(path.join(runtime, 'crash-recovery-cli-inputs.jsonl'), 'utf8');
  assert.equal(cliReceipt.trim().split(/\r?\n/).length, 1,
    'the deterministic provider receives the task exactly once');

  const canonical = await history(ids['E2E-A']);
  assert.equal(canonical.filter(row => row.role === 'user' && String(row.content).includes(label)).length, 1,
    'HTTP history contains exactly one copy');
  const rendered = await readState();
  assert.equal(rendered.rows.filter(row => row.role === 'user' && String(row.content).includes(label)).length, 1,
    'Zustand contains exactly one copy');
  const dom = await page.locator('main [data-index]').evaluateAll(nodes => ({
    users: nodes.filter(node => node.querySelector('.msg.user')?.textContent?.includes('crash-recovery-history-idempotency')).length,
    assistants: nodes.filter(node => node.querySelector('.msg.assistant')?.textContent?.includes('crash-recovery-history-idempotency')).length,
  }));
  assert.deepEqual(dom, { users: 1, assistants: 1 }, 'Chromium renders one user and one assistant row');
  assert.deepEqual(evidence.errors, []);
  evidence.pass = true;
  evidence.assertions = {
    queueItemId: queued.id,
    sourceSessionId: queued.sourceSessionId,
    taskIdSource: queued.taskIdSource,
    jsonlCopies: await countHistoryJsonl(ids['E2E-A']),
    cliInputs: cliReceipt.trim().split(/\r?\n/).length,
    storeUserRows: 1,
    dom,
    wsReconnects: socketCount - socketsBeforeCrash,
  };
} catch (error) {
  evidence.failure = String(error.stack || error);
  if (page) {
    evidence.failedStore = await readState().catch(() => null);
    await page.screenshot({ path: path.join(runtime, 'failure.png') }).catch(() => {});
  }
  console.error(error);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  await stop();
  let portFree = false;
  try { await freePort(); portFree = true; } catch { /* Preserve cleanup evidence. */ }
  evidence.cleanup = { portFree };
  await fs.writeFile(path.join(runtime, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log('EVIDENCE', runtime);
}
