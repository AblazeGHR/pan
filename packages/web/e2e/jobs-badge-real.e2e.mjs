/* global fetch, process, console */
// Real-chain browser verification for the Jobs nav running-count badge.
//
// Boots the REAL Pan server (real FastAPI HTTP + real WebSocket broadcast, the
// real scheduler/job registry) from the established browser fixture
// (e2e/server.py) on a free OS-assigned port — never 8767/8768 — and loads the
// production bundle at /react/ with NO mock mode. The user stays on the Chat
// route for the WHOLE run and never opens the Jobs page: socket-open REST
// hydration, real job.created / job.deleted broadcasts, and the recovery
// loop's terminal job.updated broadcast (a detached background runner can
// only persist the terminal state) must all update the badge off-page, with
// zero navigation and zero frontend polling.
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
const runtime = path.resolve(import.meta.dirname, '../test-results', `jobs-badge-real-${Date.now()}`);
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
  server = spawn(python, [path.join(import.meta.dirname, 'jobs-badge.server.py')], {
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
  const jobA = await startRunningJob('Badge Job A', 45);
  const jobB = await startRunningJob('Badge Job B', 45);

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
  const groupBox = () => page.locator('div[aria-label^="界面入口，第"]').boundingBox();

  // 1. Global hydration: the badge shows 2 on the Chat route — /jobs was
  // never visited, so only the real WS-open -> GET /api/jobs chain can know.
  await page.locator('div[aria-label^="界面入口，第"]').waitFor({ state: 'visible', timeout: 20000 });
  await poll(badgeText, (text) => text === '2', 'badge shows 2 after real socket-open hydration');
  const navBoxRunning = await groupBox();
  const linkBoxRunning = await page.getByRole('link', { name: 'Jobs' }).boundingBox();
  const badgeBoxRunning = await badge.boundingBox();
  // Floating overlay pinned to the entry's bottom-right corner: inside the
  // entry box, never clipped, flush with its right and bottom edges.
  assert.ok(badgeBoxRunning.x >= linkBoxRunning.x - 1,
    'badge must stay inside the entry box (not clipped on the left)');
  assert.ok(badgeBoxRunning.y >= linkBoxRunning.y - 1,
    'badge must stay inside the entry box (not clipped on the top)');
  assert.ok(badgeBoxRunning.x + badgeBoxRunning.width <= linkBoxRunning.x + linkBoxRunning.width + 1,
    'badge must stay inside the entry box (not clipped on the right)');
  assert.ok(badgeBoxRunning.y + badgeBoxRunning.height <= linkBoxRunning.y + linkBoxRunning.height + 1,
    'badge must stay inside the entry box (not clipped on the bottom)');
  assert.ok(Math.abs(badgeBoxRunning.x + badgeBoxRunning.width - (linkBoxRunning.x + linkBoxRunning.width)) <= 1,
    'badge right edge must be flush with the entry right edge (bottom-right corner)');
  assert.ok(Math.abs(badgeBoxRunning.y + badgeBoxRunning.height - (linkBoxRunning.y + linkBoxRunning.height)) <= 1,
    'badge bottom edge must be flush with the entry bottom edge (bottom-right corner)');
  // The overlay occupies no layout space: the Jobs entry keeps the sibling
  // entry's exact box (no stretch, no extra height).
  const chatBoxRunning = await page.getByRole('link', { name: 'Chat' }).boundingBox();
  assert.equal(linkBoxRunning.height, chatBoxRunning.height,
    'Jobs entry must keep the sibling entry height (overlay takes no space)');
  assert.equal(linkBoxRunning.width, chatBoxRunning.width,
    'Jobs entry must keep the sibling entry width (overlay takes no space)');

  // 2. The user NEVER leaves the Chat route for the rest of the run: every
  // count change below arrives through the real WebSocket broadcasts alone.
  // 2a. Real job.created broadcast bumps 2 -> 3 with no navigation.
  const jobC = await startRunningJob('Badge Job C', 6);
  await poll(badgeText, (text) => text === '3', 'badge shows 3 after the real job.created broadcast');

  // 2b. THE FIX UNDER TEST: C's real process finishes (~6s). The detached
  // runner only persists the terminal state; the server's existing recovery
  // loop observes it and broadcasts job.updated, so the badge must decrement
  // BY ITSELF while the user stays on Chat — no Jobs visit, no polling.
  await poll(badgeText, (text) => text === '2',
    'badge self-decrements when the real job completes (recovery-loop broadcast)', 20000);

  // 3. Real deletion broadcast: job.deleted must decrement off-page.
  assert.equal((await fetch(`${base}/api/jobs/${jobA}`, { method: 'DELETE' })).status, 200, 'delete A');
  await poll(badgeText, (text) => text === '1', 'badge decrements after a real job.deleted broadcast');

  // 4. Real deletion down to zero: the badge must hide, with zero layout shift.
  assert.equal((await fetch(`${base}/api/jobs/${jobB}`, { method: 'DELETE' })).status, 200, 'delete B');
  await poll(badgeText, (text) => text === null, 'badge hides at zero running jobs');
  const navBoxZero = await groupBox();
  assert.equal(navBoxZero.height, navBoxRunning.height,
    'nav height must not shift between badge hidden and shown (floating overlay)');
  const linkBoxZero = await page.getByRole('link', { name: 'Jobs' }).boundingBox();
  assert.equal(linkBoxZero.height, linkBoxRunning.height, 'entry height is stable at 0');
  assert.equal(linkBoxZero.width, linkBoxRunning.width, 'entry width is stable at 0');

  // 5. A fresh page load re-hydrates through the real open handler: seed one
  // running job again, reload, and the badge must come back on the Chat route.
  await startRunningJob('Badge Job D', 30);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.locator('div[aria-label^="界面入口，第"]').waitFor({ state: 'visible', timeout: 20000 });
  await poll(badgeText, (text) => text === '1', 'badge re-hydrates after a fresh page load');

  assert.equal(new URL(page.url()).pathname.includes('/jobs'), false,
    'the whole acceptance must stay off the Jobs route');
  assert.deepEqual(pageErrors, [], 'page errors');
  await context.close();
  console.log('ALL PASS', JSON.stringify({ port, jobsCwd }));
} finally {
  await browser?.close();
  server?.kill();
}

// Real DELETE can leave the fixture process exiting non-zero; that is fine —
// the process is a disposable fixture whose evidence lives in the log file.
process.exitCode = 0;
