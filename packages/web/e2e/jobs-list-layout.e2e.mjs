/* global URL, process, fetch, document, window, innerWidth, innerHeight, console, structuredClone, requestAnimationFrame */
import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';

const port = 5190;
const baseURL = `http://127.0.0.1:${port}/jobs-list-layout.html`;
const cwd = fileURLToPath(new URL('..', import.meta.url));
const layoutJobs = Array.from({ length: 48 }, (_, index) => {
  const number = String(index + 1).padStart(2, '0');
  const timedOut = index % 2 === 0;
  return {
    jobId: `job-layout-${number}`,
    kind: timedOut ? 'main-lifecycle' : 'session-message',
    status: timedOut ? 'timed_out' : 'completed',
    name: `Job row ${number}`,
    description: `Responsive layout fixture for row ${number}`,
    source: { type: 'system' },
    target: { sessionId: `session-${number}` },
    paused: false,
    schedule: { legacy: 'scalar-safe' },
    runCount: index,
    createdAt: 1,
    updatedAt: 48 - index,
  };
});
const actionJobs = [
  {
    jobId: 'completed-fixture',
    kind: 'main-lifecycle',
    status: 'completed',
    name: 'Completed fixture',
    description: 'Completed status fixture',
    paused: false,
  },
  {
    jobId: 'timeout-fixture',
    kind: 'main-lifecycle',
    status: 'timed_out',
    name: 'Timed out fixture',
    description: 'Timeout status fixture',
    paused: false,
  },
  {
    jobId: 'failed-fixture',
    kind: 'main-lifecycle',
    status: 'failed',
    name: 'Ordinary failed fixture',
    description: 'Failed status fixture',
    paused: false,
  },
  {
    jobId: 'description-fixture',
    kind: 'main-lifecycle',
    status: 'pending',
    name: 'Description target',
    description: 'Needle description browser fixture',
    paused: false,
  },
  {
    jobId: 'batch-pause-one',
    kind: 'session-message',
    status: 'pending',
    name: 'Batch pause one',
    description: 'Pause success fixture',
    paused: false,
  },
  {
    jobId: 'batch-pause-retry',
    kind: 'session-message',
    status: 'pending',
    name: 'Batch pause retry',
    description: 'Pause retry fixture',
    paused: false,
  },
  {
    jobId: 'batch-pause-already',
    kind: 'session-message',
    status: 'pending',
    name: 'Batch pause already',
    description: 'Already paused fixture',
    paused: true,
  },
  {
    jobId: 'batch-delete-external',
    kind: 'session-message',
    status: 'pending',
    name: 'Batch delete external',
    description: 'WS delete fixture',
    paused: false,
  },
  {
    jobId: 'batch-delete-one',
    kind: 'session-message',
    status: 'pending',
    name: 'Batch delete one',
    description: 'Delete success fixture',
    paused: false,
  },
  {
    jobId: 'batch-delete-retry',
    kind: 'session-message',
    status: 'pending',
    name: 'Batch delete retry',
    description: 'Delete retry fixture',
    paused: false,
  },
].map((job) => ({
  source: { type: 'system' },
  target: { sessionId: 'session-fixture' },
  schedule: { legacy: 'object-safe' },
  runCount: 0,
  createdAt: 1,
  updatedAt: 100,
  ...job,
}));
const initialJobs = [...layoutJobs, ...actionJobs];
let jobsState = structuredClone(initialJobs);
let apiCalls = [];
let pauseFailuresLeft = 1;
let deleteFailuresLeft = 1;

function resetMockApi() {
  jobsState = structuredClone(initialJobs);
  apiCalls = [];
  pauseFailuresLeft = 1;
  deleteFailuresLeft = 1;
}

async function assertPortAvailable() {
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  });
}

await assertPortAvailable();
const server = spawn(
  'pnpm',
  ['exec', 'vite', '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
  { cwd, shell: process.platform === 'win32', stdio: 'ignore' },
);
let browser;

try {
  let ready = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (server.exitCode !== null) throw new Error(`Vite exited with code ${server.exitCode}`);
    try {
      const response = await fetch(baseURL);
      if (response.ok) {
        ready = true;
        break;
      }
    } catch {
      // Vite is still starting.
    }
    await delay(250);
  }
  assert.ok(ready, `Vite did not become ready at ${baseURL}`);

  browser = await chromium.launch({ headless: true });
  console.log(`Chromium ${browser.version()}`);
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  page.setDefaultTimeout(10000);
  const pageErrors = [];
  const protectedRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => {
    if (/:(8767|8768)(\/|$)/.test(request.url())) protectedRequests.push(request.url());
  });
  page.on('websocket', (socket) => {
    if (/:(8767|8768)(\/|$)/.test(socket.url())) protectedRequests.push(socket.url());
  });
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(route.request().url());
    const pathname = decodeURIComponent(url.pathname);
    let requestPayload;
    try {
      requestPayload = request.postDataJSON();
    } catch {
      requestPayload = undefined;
    }
    apiCalls.push({ method: request.method(), pathname, payload: requestPayload });
    let payload = { ok: true };
    if (request.method() === 'GET' && pathname === '/api/jobs') {
      payload = { ok: true, jobs: jobsState };
    } else if (request.method() === 'GET' && pathname === '/api/jobs/kinds') {
      payload = {
        ok: true,
        kinds: [
          {
            kind: 'main-lifecycle',
            label: 'Service lifecycle',
            hasSchedule: false,
            hasProcess: false,
          },
          {
            kind: 'session-message',
            label: 'Session message',
            hasSchedule: true,
            hasProcess: false,
          },
          {
            kind: 'scheduled-task',
            label: 'Scheduled task',
            hasSchedule: true,
            hasProcess: false,
          },
        ],
      };
    } else if (request.method() === 'GET' && pathname === '/api/sessions') {
      payload = { ok: true, sessions: [] };
    } else if (request.method() === 'PATCH' && pathname.startsWith('/api/jobs/')) {
      const jobId = pathname.slice('/api/jobs/'.length);
      if (jobId === 'batch-pause-retry' && pauseFailuresLeft > 0) {
        pauseFailuresLeft -= 1;
        payload = { ok: false, error: { code: 'temporary', message: 'temporary pause failure' } };
      } else {
        jobsState = jobsState.map((job) =>
          job.jobId === jobId ? { ...job, ...requestPayload } : job,
        );
        payload = { ok: true, job: jobsState.find((job) => job.jobId === jobId) };
      }
    } else if (request.method() === 'DELETE' && pathname.startsWith('/api/jobs/')) {
      const jobId = pathname.slice('/api/jobs/'.length);
      if (jobId === 'batch-delete-retry' && deleteFailuresLeft > 0) {
        deleteFailuresLeft -= 1;
        payload = { ok: false, error: { code: 'temporary', message: 'temporary delete failure' } };
      } else {
        jobsState = jobsState.filter((job) => job.jobId !== jobId);
        payload = { ok: true, deleted: true, jobId };
      }
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
  });
  await page.goto(baseURL, { waitUntil: 'networkidle' });

  resetMockApi();
  await page.goto(baseURL, { waitUntil: 'networkidle' });
  const search = page.getByRole('searchbox', { name: 'Search jobs' });
  await page.getByRole('button', { name: 'Completed', exact: true }).click();
  await search.fill('completed fixture');
  assert.equal(await page.getByText('Completed fixture').count(), 1);
  assert.equal(await page.getByText('Ordinary failed fixture').count(), 0);

  await page.getByRole('button', { name: 'Timeout', exact: true }).click();
  await search.fill('timeout-fixture');
  assert.equal(await page.getByText('Timed out fixture').count(), 1);
  await page.getByRole('combobox', { name: 'Kind' }).selectOption('main-lifecycle');
  await search.fill('needle description');
  assert.equal(await page.getByText('Description target').count(), 0);
  await page.getByRole('button', { name: 'All', exact: true }).click();
  await search.fill('');
  await page.getByRole('combobox', { name: 'Kind' }).selectOption('main-lifecycle');
  await search.fill('NEEDLE DESCRIPTION');
  assert.equal(await page.getByText('Description target').count(), 1);
  await page.getByRole('combobox', { name: 'Kind' }).selectOption('all');
  await search.fill('Batch pause');

  await page.getByRole('button', { name: 'Select jobs' }).click();
  await page.getByRole('checkbox', { name: 'Select job Batch pause one' }).check();
  assert.equal(await page.getByText('1 selected').count(), 1);
  assert.equal(await page.getByRole('dialog', { name: 'Job detail' }).count(), 0);
  const pauseRow = page
    .locator('[data-testid="jobs-list-scroll"] .cursor-pointer')
    .filter({ hasText: 'Batch pause one' });
  await pauseRow.getByRole('button', { name: 'Job actions' }).click();
  await page.getByRole('menu').waitFor({ state: 'visible' });
  assert.equal(await page.getByRole('dialog', { name: 'Job detail' }).count(), 0);
  await page.locator('.fixed.inset-0.z-20').click({ position: { x: 5, y: 5 } });
  await page.getByRole('checkbox', { name: 'Select all visible jobs' }).click();
  assert.equal(await page.getByText('3 selected').count(), 1);
  await page.getByRole('button', { name: 'Pause selected' }).click();
  await page.getByText('Pause: 2 succeeded, 1 failed').waitFor({ state: 'visible' });
  const pauseRequests = apiCalls.filter((call) => call.method === 'PATCH');
  assert.equal(pauseRequests.length, 2);
  assert.ok(pauseRequests.every((call) => call.payload.paused === true));
  assert.ok(!pauseRequests.some((call) => call.pathname.endsWith('batch-pause-already')));
  await page.getByRole('button', { name: 'Retry failed (1)' }).click();
  await page.getByText('Pause: 1 succeeded, 0 failed').waitFor({ state: 'visible' });
  assert.equal(apiCalls.filter((call) => call.method === 'PATCH').length, 3);

  await search.fill('Batch delete');
  await page.getByRole('checkbox', { name: 'Select all visible jobs' }).click();
  await page.getByRole('button', { name: 'Delete selected (3)' }).click();
  await page.getByRole('dialog', { name: 'Delete selected jobs' }).waitFor({ state: 'visible' });
  assert.match(
    await page.getByRole('dialog', { name: 'Delete selected jobs' }).textContent(),
    /Delete\s*3\s*selected jobs\?/,
  );
  await page.getByRole('button', { name: 'Cancel' }).click();
  assert.equal(apiCalls.filter((call) => call.method === 'DELETE').length, 0);

  await page.getByRole('button', { name: 'Delete selected (3)' }).click();
  await page.evaluate(() => {
    window.__dispatchJobsEvent({ type: 'job.deleted', jobId: 'batch-delete-external' });
  });
  const deleteDialog = page.getByRole('dialog', { name: 'Delete selected jobs' });
  await deleteDialog.waitFor({ state: 'visible' });
  await page.getByText('Batch delete external').waitFor({ state: 'detached' });
  await page.waitForFunction(() =>
    document.querySelector('[role="dialog"]')?.textContent?.includes('Delete 2 selected jobs?'),
  );
  assert.match(await deleteDialog.textContent(), /Delete\s*2\s*selected jobs\?/);
  await page.getByRole('button', { name: 'Delete 2 jobs' }).click();
  await page.getByText('Delete: 1 succeeded, 1 failed').waitFor({ state: 'visible' });
  const deleteRequests = apiCalls.filter((call) => call.method === 'DELETE');
  assert.deepEqual(deleteRequests.map((call) => call.pathname.split('/').at(-1)).sort(), [
    'batch-delete-one',
    'batch-delete-retry',
  ]);
  await page.getByRole('button', { name: 'Retry failed (1)' }).click();
  await page.getByText('Delete: 1 succeeded, 0 failed').waitFor({ state: 'visible' });
  assert.equal(apiCalls.filter((call) => call.method === 'DELETE').length, 3);
  console.log(
    'Browser interactions: Completed/Timeout, search+kind, checkbox/menu isolation, pause retry, delete cancel/WS prune/retry passed',
  );

  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
    resetMockApi();
    await page.setViewportSize(viewport);
    await page.goto(baseURL, { waitUntil: 'networkidle' });
    await page.evaluate(() => {
      const scroll = document.querySelector('[data-testid="jobs-list-scroll"]');
      if (scroll) scroll.scrollTop = 0;
    });
    await page.getByText('Job row 01').waitFor({ state: 'visible' });
    await page.getByRole('button', { name: 'Timeout' }).click();
    await page.getByRole('button', { name: 'Select jobs' }).click();
    await page.getByRole('checkbox', { name: 'Select all visible jobs' }).check();

    const before = await page.evaluate(() => {
      const getRect = (element) => {
        if (!element) throw new Error('Expected fixed Jobs control is missing');
        const { x, y, width, height, right, bottom } = element.getBoundingClientRect();
        return { x, y, width, height, right, bottom };
      };
      const scroll = document.querySelector('[data-testid="jobs-list-scroll"]');
      const firstRow = document.querySelector('[data-testid="jobs-list-scroll"] .cursor-pointer');
      if (!scroll || !firstRow) throw new Error('Jobs scroll container or row is missing');
      return {
        viewport: { width: innerWidth, height: innerHeight },
        title: getRect(document.querySelector('h1')),
        tab: getRect(
          [...document.querySelectorAll('button')].find(
            (button) => button.textContent?.trim() === 'Jobs',
          ),
        ),
        filter: getRect(
          [...document.querySelectorAll('button')].find(
            (button) => button.textContent?.trim() === 'Timeout',
          ),
        ),
        search: getRect(document.querySelector('input[aria-label="Search jobs"]')),
        selectAll: getRect(document.querySelector('input[aria-label="Select all visible jobs"]')),
        count: getRect(document.querySelector('[aria-live="polite"]')),
        scroll: getRect(scroll),
        firstRow: getRect(firstRow),
        scrollMetrics: { clientHeight: scroll.clientHeight, scrollHeight: scroll.scrollHeight },
        documentWidth: document.documentElement.scrollWidth,
      };
    });
    assert.ok(
      before.scrollMetrics.scrollHeight > before.scrollMetrics.clientHeight,
      'Rows should overflow the list viewport',
    );
    assert.ok(
      before.documentWidth <= viewport.width,
      `Horizontal overflow at ${viewport.width}px: ${before.documentWidth}px`,
    );

    await page.evaluate(() => {
      const scroll = document.querySelector('[data-testid="jobs-list-scroll"]');
      if (!scroll) throw new Error('Jobs scroll container is missing');
      scroll.scrollTop = Math.min(480, scroll.scrollHeight);
    });
    await page.evaluate(
      () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
    );
    const after = await page.evaluate(() => {
      const getRect = (element) => {
        if (!element) throw new Error('Expected fixed Jobs control is missing');
        const { x, y, width, height, right, bottom } = element.getBoundingClientRect();
        return { x, y, width, height, right, bottom };
      };
      const scroll = document.querySelector('[data-testid="jobs-list-scroll"]');
      const firstRow = document.querySelector('[data-testid="jobs-list-scroll"] .cursor-pointer');
      if (!scroll || !firstRow) throw new Error('Jobs scroll container or row is missing');
      return {
        title: getRect(document.querySelector('h1')),
        tab: getRect(
          [...document.querySelectorAll('button')].find(
            (button) => button.textContent?.trim() === 'Jobs',
          ),
        ),
        filter: getRect(
          [...document.querySelectorAll('button')].find(
            (button) => button.textContent?.trim() === 'Timeout',
          ),
        ),
        search: getRect(document.querySelector('input[aria-label="Search jobs"]')),
        selectAll: getRect(document.querySelector('input[aria-label="Select all visible jobs"]')),
        count: getRect(document.querySelector('[aria-live="polite"]')),
        scroll: getRect(scroll),
        firstRow: getRect(firstRow),
        scrollTop: scroll.scrollTop,
        documentWidth: document.documentElement.scrollWidth,
      };
    });
    for (const key of ['title', 'tab', 'filter', 'search', 'selectAll', 'count', 'scroll']) {
      assert.ok(
        Math.abs(before[key].y - after[key].y) <= 0.5,
        `${key} moved during list scroll: ${before[key].y} → ${after[key].y}`,
      );
    }
    assert.ok(after.firstRow.y < before.firstRow.y, 'A Job row should move when its list scrolls');
    assert.ok(after.scrollTop > 0, 'Jobs list should have a nonzero scrollTop');
    assert.ok(
      after.documentWidth <= viewport.width,
      `Horizontal overflow after scroll: ${after.documentWidth}px`,
    );
    console.log(`${viewport.width}x${viewport.height}: ${JSON.stringify({ before, after })}`);
  }

  assert.deepEqual(pageErrors, [], `Browser errors: ${pageErrors.join('; ')}`);
  assert.deepEqual(
    protectedRequests,
    [],
    `Unexpected protected-port requests: ${protectedRequests.join(', ')}`,
  );
} finally {
  await browser?.close();
  if (server.exitCode === null) {
    if (process.platform === 'win32') {
      try {
        execFileSync('taskkill.exe', ['/PID', String(server.pid), '/T', '/F'], { stdio: 'ignore' });
      } catch {
        // The process may have exited between the state check and taskkill.
      }
    } else {
      server.kill('SIGTERM');
    }
    await Promise.race([new Promise((resolve) => server.once('exit', resolve)), delay(3000)]);
  }
}
