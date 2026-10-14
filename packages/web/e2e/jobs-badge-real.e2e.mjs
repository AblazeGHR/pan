/* global fetch, process, console */
// Real-chain browser verification for the Jobs nav running-count badge.
//
// Boots the REAL Pan server (real FastAPI HTTP + real WebSocket broadcast, the
// real scheduler/job registry) from the established browser fixture
// (e2e/server.py) on a free OS-assigned port — never 8767/8768 — and loads the
// production bundle at /react/ with NO mock mode, so the app's real global WS
// open hydration and real job.* broadcasts drive the badge. Shell-action
// scheduled tasks run the deterministic sleeper below, giving a controlled
// real running window: the badge must follow created/updated/deleted events
// while the user stays on the Chat route.
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
  const jobsLink = page.getByRole('link', { name: 'Jobs' });
  const chatLink = page.getByRole('link', { name: 'Chat' });

  // 1. Global hydration: the badge shows 2 on the Chat route — /jobs was
  // never visited, so only the real WS-open -> GET /api/jobs chain can know.
  await page.locator('div[aria-label^="界面入口，第"]').waitFor({ state: 'visible', timeout: 20000 });
  await poll(badgeText, (text) => text === '2', 'badge shows 2 after real socket-open hydration');
  const navBoxRunning = await groupBox();
  const linkBoxRunning = await page.getByRole('link', { name: 'Jobs' }).boundingBox();
  const labelBoxRunning = await jobsLink.locator('span').first().boundingBox();
  const badgeBoxRunning = await badge.boundingBox();
  // Below the label, inside the entry box (reserved slot, nothing clipped).
  assert.ok(badgeBoxRunning.y >= labelBoxRunning.y + labelBoxRunning.height - 1,
    'badge must sit below the entry label');
  assert.ok(badgeBoxRunning.y + badgeBoxRunning.height <= linkBoxRunning.y + linkBoxRunning.height + 1,
    'badge must stay inside the entry box (reserved slot, not clipped)');

  // 2. One list, one source: the Jobs page shows the same real jobs.
  await jobsLink.click();
  try {
    await page.waitForURL('**/jobs', { timeout: 10000 });
  } catch (e) {
    const state = await page.evaluate(() => ({
      url: location.href,
      body: document.body.innerText.slice(0, 400),
      dialogs: document.querySelectorAll('[role="dialog"]').length,
    })).catch((err) => `evaluate failed: ${err.message}`);
    console.log('NAV-DEBUG', JSON.stringify(state));
    throw e;
  }
  await page.getByText('Badge Job A').waitFor({ state: 'visible', timeout: 10000 });
  assert.ok(await page.getByText('Badge Job B').isVisible(), 'Badge Job B listed');
  await chatLink.click();
  try {
    await page.waitForURL((url) => ['/', '/react', '/react/'].includes(url.pathname), { timeout: 10000 });
  } catch (e) {
    const state = await page.evaluate(() => ({
      url: location.href,
      body: document.body.innerText.slice(0, 300),
      dialogs: document.querySelectorAll('[role="dialog"]').length,
    })).catch((err) => `evaluate failed: ${err.message}`);
    console.log('BACK-DEBUG', JSON.stringify(state), 'pageErrors=', JSON.stringify(pageErrors));
    throw e;
  }
  await poll(badgeText, (text) => text === '2', 'badge persists back on the Chat route');

  // 3. Off-page real WS update: a real job.created broadcast bumps 2 -> 3
  // with no navigation of any kind.
  const jobC = await startRunningJob('Badge Job C', 6);
  await poll(badgeText, (text) => text === '3', 'badge shows 3 after the real job.created broadcast');

  // 4. Real deletion broadcast: job.deleted must decrement off-page.
  assert.equal((await fetch(`${base}/api/jobs/${jobA}`, { method: 'DELETE' })).status, 200, 'delete A');
  await poll(badgeText, (text) => text === '2', 'badge decrements after a real job.deleted broadcast');

  // 5. C's real process finishes (~6s). A detached background runner records
  // completion in the registry WITHOUT a WS emit (backend semantics), so the
  // badge keeps its last WS-known value until the next authoritative read.
  // Re-entering the Jobs page (real REST refresh) must converge it to only
  // B running — the completed C is neither resurrected nor double-counted.
  await sleep(7000);
  await jobsLink.click();
  try {
    await page.waitForURL('**/jobs', { timeout: 10000 });
  } catch (e) {
    throw e;
  }
  await page.getByText('Badge Job C').waitFor({ state: 'visible', timeout: 10000 });
  await poll(badgeText, (text) => text === '1', 'REST refresh converges the badge to the completed state');
  await chatLink.click();
  try {
    await page.waitForURL((url) => ['/', '/react', '/react/'].includes(url.pathname), { timeout: 10000 });
  } catch (e) {
    throw e;
  }
  await poll(badgeText, (text) => text === '1', 'badge stays 1 back on the Chat route');

  // 6. Real deletion down to zero: the badge must hide, with zero layout shift.
  assert.equal((await fetch(`${base}/api/jobs/${jobB}`, { method: 'DELETE' })).status, 200, 'delete B');
  await poll(badgeText, (text) => text === null, 'badge hides at zero running jobs');
  const navBoxZero = await groupBox();
  assert.equal(navBoxZero.height, navBoxRunning.height,
    'nav height must not shift between badge hidden and shown (reserved slot)');
  const linkBoxZero = await page.getByRole('link', { name: 'Jobs' }).boundingBox();
  assert.equal(linkBoxZero.height, linkBoxRunning.height, 'entry height is stable at 0');

  // 7. A fresh page load re-hydrates through the real open handler: seed one
  // running job again, reload, and the badge must come back on the Chat route.
  await startRunningJob('Badge Job D', 30);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.locator('div[aria-label^="界面入口，第"]').waitFor({ state: 'visible', timeout: 20000 });
  await poll(badgeText, (text) => text === '1', 'badge re-hydrates after a fresh page load');

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
