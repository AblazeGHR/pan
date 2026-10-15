/* global fetch, process, console */
// Real-chain browser verification for the Jobs badge broadcast-retry path.
//
// Boots the REAL Pan server from jobs-badge-retry.server.py on a free
// OS-assigned port (never 8767/8768) with ONE injected fault: the first
// terminal job.updated raised by the recovery loop fails once, exactly like
// a broken WS channel. The user stays on the Chat route for the WHOLE run.
//
// The acceptance proves the publish-then-mark contract end to end:
//   1. the failed attempt is recorded (GET /__e2e/broadcast-fault) and the
//      badge is STILL stale at that moment (nothing was enqueued, and the
//      marker was not persisted);
//   2. the next recovery tick retries, the badge self-decrements with zero
//      navigation and zero frontend polling;
//   3. exactly one failure and exactly one retry happened (no per-tick
//      replay after success), and a fresh page load re-hydrates the count.
import assert from 'node:assert/strict';
import { accessSync } from 'node:fs';
import { createWriteStream } from 'node:fs';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { spawn } from 'node:child_process';
import process from 'node:process';
import { setTimeout as sleep } from 'node:timers/promises';
import { chromium } from '@playwright/test';

const root = path.resolve(import.meta.dirname, '../../..');
const runtime = path.resolve(import.meta.dirname, '../test-results', `jobs-badge-retry-${Date.now()}`);
const python = process.env.PAN_E2E_PYTHON
  ?? ['D:/project/Pan-main/.venv/Scripts/python.exe', 'D:/project/Pan/.venv/Scripts/python.exe']
    .find((candidate) => { try { accessSync(candidate); return true; } catch { return false; } });
assert.ok(python, 'no Pan python interpreter found (set PAN_E2E_PYTHON)');

await fs.mkdir(runtime, { recursive: true });
const jobsCwd = path.join(runtime, 'jobs-cwd');
await fs.mkdir(jobsCwd, { recursive: true });
// Deterministic shell payload: run for N seconds, then exit 0.
const sleeper = path.join(jobsCwd, 'sleeper.py');
await fs.writeFile(sleeper, 'import sys, time\n\ntime.sleep(float(sys.argv[1]))\n', 'utf8');

async function freePort() {
  for (;;) {
    const port = await new Promise((resolve, reject) => {
      const srv = net.createServer();
      srv.on('error', reject);
      srv.listen(0, '127.0.0.1', () => {
        const { port } = srv.address();
        srv.close(() => resolve(port));
      });
    });
    // The fixture must never land on the protected Pan service ports.
    if (port !== 8767 && port !== 8768 && port !== 8765) return port;
  }
}

const port = await freePort();
const base = `http://127.0.0.1:${port}`;
const chatUrl = `${base}/react/`;

async function api(route, init) {
  const res = await fetch(base + route, init);
  assert.equal(res.status, 200, `${route} -> ${res.status}`);
  const data = await res.json();
  if (data && typeof data === 'object' && 'error' in data && data.error) {
    throw new Error(`${route}: ${JSON.stringify(data.error)}`);
  }
  return data;
}

async function apiPost(route, body) {
  return api(route, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
}

async function poll(read, test, label, timeout = 30000) {
  let last;
  for (const end = Date.now() + timeout; Date.now() < end; await sleep(150)) {
    last = await read();
    if (test(last)) return last;
  }
  throw new Error(`${label}: last=${JSON.stringify(last)?.slice(0, 600)}`);
}

const faultCounters = async () => api('/__e2e/broadcast-fault');

/** Create a REAL background-process job through the API (argv sleeper), then
 * wait until the real registry reports it running. */
async function startRunningJob(name, seconds) {
  const sessions = await api('/api/sessions?summary=1');
  const sessionId = sessions.sessions[0].id;
  const created = await apiPost('/api/jobs', {
    kind: 'background-process',
    name,
    target: { sessionId },
    argv: [python, sleeper, String(seconds)],
    cwd: jobsCwd,
  });
  const jobId = created.job.jobId;
  await poll(
    async () => (await api('/api/jobs')).jobs.find((j) => j.jobId === jobId)?.status,
    (status) => status === 'running',
    `${name} should reach running`,
  );
  return jobId;
}

let server, browser;
try {
  server = spawn(python, [path.join(import.meta.dirname, 'jobs-badge-retry.server.py')], {
    cwd: root, windowsHide: true,
    env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const serverLog = createWriteStream(path.join(runtime, `server-${server.pid}.log`));
  server.stdout.pipe(serverLog);
  server.stderr.pipe(serverLog);
  await poll(
    async () => {
      try { return (await fetch(`${base}/api/health`)).ok; } catch { return false; }
    },
    (ok) => ok,
    'fixture server readiness',
    60000,
  );

  // Two real running jobs exist before the app boots: the global WS-open
  // hydration must surface them on the Chat route without visiting /jobs.
  const jobA = await startRunningJob('Retry Job A', 60);
  const jobB = await startRunningJob('Retry Job B', 60);

  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.goto(`${chatUrl}?panE2E=1`, { waitUntil: 'domcontentloaded' });

  const badge = page.locator('[data-testid="jobs-running-badge"]').first();
  const badgeText = async () => {
    if ((await badge.count()) === 0) return null;
    try {
      return ((await badge.textContent({ timeout: 2000 })) ?? '').trim();
    } catch {
      // Detached between count and read: the badge just disappeared.
      return null;
    }
  };

  // 1. Global hydration: the badge shows 2 on the Chat route — /jobs was
  // never visited, so only the real WS-open -> GET /api/jobs chain can know.
  await page.locator('div[aria-label^="界面入口，第"]').waitFor({ state: 'visible', timeout: 20000 });
  await poll(badgeText, (text) => text === '2', 'badge shows 2 after real socket-open hydration');

  // 2. The user NEVER leaves the Chat route for the rest of the run: every
  // count change below arrives through the real WebSocket broadcasts alone.
  // 2a. Real job.created broadcast bumps 2 -> 3 with no navigation.
  const jobC = await startRunningJob('Retry Job C', 6);
  await poll(badgeText, (text) => text === '3', 'badge shows 3 after the real job.created broadcast');

  // 2b. THE FIX UNDER TEST: C's real process finishes (~6s). The detached
  // runner only persists the terminal state; the recovery loop's FIRST
  // terminal broadcast is injected to fail once. The causal chain:
  //   failure recorded AND badge still 3 (nothing enqueued, marker not
  //   persisted) -> next tick retries -> badge self-decrements to 2.
  let sawStaleAfterFailure = false;
  const deadline = Date.now() + 30000;
  for (; Date.now() < deadline; await sleep(150)) {
    const fault = await faultCounters();
    const text = await badgeText();
    if (fault.failed_terminal_events >= 1 && text === '3') sawStaleAfterFailure = true;
    if (fault.passed_terminal_events >= 1 && text === '2') break;
  }
  assert.ok(sawStaleAfterFailure,
    'badge must stay stale while the broadcast channel is down (no enqueue, no mark)');
  assert.equal(await badgeText(), '2',
    'badge self-converges on the next recovery tick after the channel recovers');

  // 3. Exactly one failure and exactly one retry: no per-tick replay after
  // the successful publish, and the marker now suppresses re-broadcasts.
  await sleep(2500); // cover >2 recovery ticks
  const finalFault = await faultCounters();
  assert.equal(finalFault.failed_terminal_events, 1, 'exactly one injected failure');
  assert.equal(finalFault.passed_terminal_events, 1, 'exactly one successful retry');
  assert.equal(await badgeText(), '2', 'badge stays converged with no replay');

  // 4. A fresh page load re-hydrates through the real open handler: the
  // persisted state (marker included) survives the reload.
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.locator('div[aria-label^="界面入口，第"]').waitFor({ state: 'visible', timeout: 20000 });
  await poll(badgeText, (text) => text === '2', 'badge re-hydrates after a fresh page load');

  assert.equal(new URL(page.url()).pathname.includes('/jobs'), false,
    'the whole acceptance must stay off the Jobs route');
  assert.deepEqual(pageErrors, [], 'page errors');
  await context.close();
  console.log('ALL PASS', JSON.stringify({ port, jobsCwd }));
} finally {
  await browser?.close();
  server?.kill();
}

// Real fixture teardown can leave the process exiting non-zero; that is fine —
// the process is a disposable fixture whose evidence lives in the log file.
process.exitCode = 0;
