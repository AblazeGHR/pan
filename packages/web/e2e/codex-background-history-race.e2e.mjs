/*
 * Reproduce Codex app-server ordering across a background session switch.
 * The fixture pauses after item/completed has been persisted, lets the browser
 * reload canonical history on A→B→A, then delivers late deltas with a different
 * native item ID for the same turn.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import process from 'node:process';
import { setTimeout } from 'node:timers';
import { chromium } from '@playwright/test';

const root = path.resolve(import.meta.dirname, '../../..');
const port = 8765;
const base = `http://127.0.0.1:${port}`;
const label = 'codex-final-first-background';
const runtime = path.resolve(import.meta.dirname, '../test-results', `codex-background-history-race-${Date.now()}`);
const python = process.env.PAN_E2E_PYTHON || 'D:/project/Pan/.venv/Scripts/python.exe';
const evidence = { root, runtime, port, label, samples: [], errors: [] };
await fs.mkdir(runtime, { recursive: true });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let server;
let browser;
let context;
let page;
let serverLogs = [];

async function freePort() {
  await new Promise((resolve, reject) => {
    const listener = net.createServer();
    listener.once('error', reject);
    listener.listen(port, '127.0.0.1', () => listener.close(resolve));
  });
}

async function api(route) {
  const response = await fetch(base + route);
  assert.equal(response.status, 200, `${route}: HTTP ${response.status}`);
  return response.json();
}

async function poll(read, test, labelText, timeout = 15000) {
  let last;
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    last = await read();
    if (test(last)) return last;
    await sleep(40);
  }
  throw new Error(`${labelText}: ${JSON.stringify(last).slice(0, 2500)}`);
}

async function stop() {
  if (!server || server.exitCode !== null) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true });
  } else {
    server.kill('SIGTERM');
  }
  await poll(() => Promise.resolve(server.exitCode), value => value !== null, 'isolated server exit');
}

try {
  await freePort();
  server = spawn(python, [path.join(import.meta.dirname, 'frontend-full.server.py')], {
    cwd: root,
    windowsHide: true,
    env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  server.stdout.on('data', chunk => serverLogs.push(chunk));
  server.stderr.on('data', chunk => serverLogs.push(chunk));
  await poll(async () => {
    try { return await api('/api/sessions?summary=1'); } catch { return null; }
  }, value => Boolean(value), 'isolated server readiness', 30000);

  const listing = await api('/api/sessions?summary=1');
  const sessions = listing.sessions || listing;
  const ids = Object.fromEntries(sessions.map(session => [session.name, session.id]));
  assert.ok(ids['E2E-A'] && ids['E2E-B'], 'Codex and alternate sessions are seeded');

  browser = await chromium.launch({ headless: true });
  context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  page = await context.newPage();
  page.on('pageerror', error => evidence.errors.push(String(error)));
  await page.goto(`${base}/react/?panE2E=1`, { waitUntil: 'domcontentloaded' });

  const select = async name => {
    await page.locator('[data-session-card-id]').filter({ hasText: name }).first().click();
    await poll(() => page.evaluate(() => window.__panSessionStore.getState().currentSessionId),
      id => id === ids[name], `select ${name}`);
  };
  const send = async text => {
    await page.locator('[contenteditable="true"]').first().fill(text);
    await page.getByRole('button', { name: 'Send', exact: true }).click();
  };
  const history = async () => {
    const response = await api(`/api/sessions/${ids['E2E-A']}/history?limit=500`);
    return response.history || [];
  };
  const visibleSample = async () => page.evaluate(messageLabel => {
    const state = window.__panSessionStore.getState();
    const matching = state.currentMessages.filter(message => message.role === 'assistant'
      && String(message.content).includes(`answer:${messageLabel}`));
    const order = state.currentMessages.filter(message =>
      (message.role === 'thinking' && message.content === `think:${messageLabel}`)
      || (message.role === 'tool' && String(message.content).includes(messageLabel))
      || (message.role === 'assistant' && String(message.content).includes(`answer:${messageLabel}`)))
      .map(message => message.role);
    const dom = [...document.querySelectorAll('main [data-index]')]
      .filter(element => element.textContent.includes(`answer:${messageLabel}`))
      .map(element => ({ index: Number(element.dataset.index), text: element.textContent }));
    return {
      sessionId: state.currentSessionId,
      order,
      live: (() => {
        const buffer = state.liveStreamBuffers[messageLabel ? state.currentSessionId : ''];
        const transcript = state.sessionTranscripts[state.currentSessionId];
        return buffer ? {
          taskKey: buffer.taskKey,
          taskSeq: buffer.taskSeq ?? null,
          anchorOffset: transcript?.anchorOffset ?? null,
          windowTotal: transcript?.window.total ?? null,
          messages: buffer.messages.map(message => ({ role: message.role,
            content: String(message.content).slice(0, 80),
            contentLength: String(message.content).length,
            nativeItemId: message.nativeItemId ?? null })),
          canonicalCandidates: [...(transcript?.window.rows.entries() ?? [])]
            .filter(([, message]) => message.role === 'assistant' && String(message.content).includes(`answer:${messageLabel}`))
            .map(([offset, message]) => ({ offset, contentLength: String(message.content).length,
              contentEqualsLive: message.content === buffer.messages.find(row => row.role === 'assistant')?.content,
              sameLivePrefix: message.content.startsWith(buffer.messages.find(row => row.role === 'assistant')?.content ?? '') })),
          projectionIndexes: buffer.projectionIndexes ?? {},
          projectionRefIndexes: Object.fromEntries(Object.entries(buffer.projectionRefs ?? {})
            .map(([key, ref]) => [key, state.currentMessages.indexOf(ref)])),
        } : null;
      })(),
      rows: matching.map(message => ({ content: message.content, messageId: message.messageId ?? null,
        nativeItemId: message.nativeItemId ?? null })),
      dom,
    };
  }, label);

  await select('E2E-A');
  await send(label);
  await poll(async () => {
    try { await fs.access(path.join(runtime, 'codex-final-first-ready')); return true; }
    catch { return false; }
  }, Boolean, 'Codex completed item pauses before late deltas');
  const fullText = `answer:${label}\n${Array.from({ length: 160 }, (_, index) =>
    `line ${String(index).padStart(3, '0')} streaming text\n`).join('')}`;
  await poll(history, rows => rows.some(row => row.role === 'assistant' && row.content === fullText),
    'completed Codex item persisted before switch');
  await poll(() => api(`/api/sessions/${ids['E2E-A']}`), session => session.workerStatus === 'running',
    'Codex Worker remains active while paused');

  await select('E2E-B');
  const historyResponse = page.waitForResponse(response =>
    response.url().includes(`/api/sessions/${ids['E2E-A']}/history`)
      && response.request().method() === 'GET');
  await select('E2E-A');
  await historyResponse;
  await poll(visibleSample, sample => sample.rows.length === 1
      && Boolean(sample.rows[0].messageId) && !sample.rows[0].nativeItemId,
    'A reloads canonical assistant row during the still-running turn');
  assert.equal((await api(`/api/sessions/${ids['E2E-A']}`)).workerStatus, 'running');

  evidence.beforeLateDelta = await visibleSample();
  assert.deepEqual(evidence.beforeLateDelta.order, ['thinking', 'tool', 'assistant'],
    'Codex reasoning, command, and completed answer keep their event order after history refresh');
  await fs.writeFile(path.join(runtime, 'codex-final-first-release'), 'release');
  const deadline = Date.now() + 10000;
  let completed = false;
  while (Date.now() < deadline) {
    const sample = await visibleSample();
    evidence.samples.push({ at: Date.now(), storeRows: sample.rows.length, domRows: sample.dom.length,
      contentLengths: sample.rows.map(row => row.content.length) });
    assert.equal(sample.sessionId, ids['E2E-A'], 'A stays selected through late deltas');
    assert.equal(sample.rows.length, 1,
      `one assistant row after canonical refresh and late Codex delta; got ${JSON.stringify(sample.rows)}`);
    assert.equal(sample.dom.length, 1,
      `one visible assistant row after canonical refresh and late Codex delta`);
    assert.deepEqual(sample.order, ['thinking', 'tool', 'assistant'],
      'Codex reasoning, command, and answer order stays stable as late deltas arrive');
    const session = await api(`/api/sessions/${ids['E2E-A']}`);
    if (session.lastResult?.result === fullText) { completed = true; break; }
    await sleep(12);
  }
  assert.ok(completed, 'late Codex delta sequence reached worker.result');
  const canonical = await history();
  assert.equal(canonical.filter(row => row.role === 'assistant' && row.content === fullText).length, 1,
    'server has one canonical Codex assistant');
  evidence.afterResult = await visibleSample();
  assert.equal(evidence.afterResult.rows.length, 1);
  assert.equal(evidence.afterResult.dom.length, 1);
  assert.deepEqual(evidence.errors, []);
  await page.screenshot({ path: path.join(runtime, 'final.png') });
  evidence.pass = true;
} catch (error) {
  evidence.failure = String(error.stack || error);
  if (page) {
    evidence.failedState = await page.evaluate(() => {
      const state = window.__panSessionStore.getState();
      return { sessionId: state.currentSessionId, rows: state.currentMessages.map(message => ({
        role: message.role, content: String(message.content).slice(0, 500),
        messageId: message.messageId ?? null, nativeItemId: message.nativeItemId ?? null,
      })), live: state.liveStreamBuffers[state.currentSessionId] && {
        taskKey: state.liveStreamBuffers[state.currentSessionId].taskKey,
        taskSeq: state.liveStreamBuffers[state.currentSessionId].taskSeq ?? null,
        messages: state.liveStreamBuffers[state.currentSessionId].messages.map(message => ({
          role: message.role, content: String(message.content).slice(0, 120), nativeItemId: message.nativeItemId ?? null,
        })),
        projectionIndexes: state.liveStreamBuffers[state.currentSessionId].projectionIndexes ?? {},
        projectionRefIndexes: Object.fromEntries(Object.entries(
          state.liveStreamBuffers[state.currentSessionId].projectionRefs ?? {},
        ).map(([key, ref]) => [key, state.currentMessages.indexOf(ref)])),
      } };
    }).catch(() => null);
    await page.screenshot({ path: path.join(runtime, 'failure.png') }).catch(() => {});
  }
  console.error(error);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  await stop();
  await fs.writeFile(path.join(runtime, 'server.log'), Buffer.concat(serverLogs));
  let portFreeAfterCleanup = false;
  try {
    await freePort();
    portFreeAfterCleanup = true;
  } catch (error) {
    evidence.errors.push(`port ${port} remained occupied after cleanup: ${String(error)}`);
  }
  evidence.cleanup = { portFree: portFreeAfterCleanup };
  await fs.writeFile(path.join(runtime, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log('EVIDENCE', runtime);
}
