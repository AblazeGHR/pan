/* global URL, process, fetch, document, innerWidth, innerHeight, console */
import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';

const port = 5190;
const baseURL = `http://127.0.0.1:${port}/jobs-list-layout.html`;
const cwd = fileURLToPath(new URL('..', import.meta.url));
const jobs = Array.from({ length: 48 }, (_, index) => {
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
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  const pageErrors = [];
  const protectedRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => {
    if (/:(8767|8768)(\/|$)/.test(request.url())) protectedRequests.push(request.url());
  });
  page.on('websocket', (socket) => {
    if (/:(8767|8768)(\/|$)/.test(socket.url())) protectedRequests.push(socket.url());
  });
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    let payload = { ok: true };
    if (url.pathname === '/api/jobs') payload = { ok: true, jobs };
    else if (url.pathname === '/api/jobs/kinds') {
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
        ],
      };
    } else if (url.pathname === '/api/sessions') payload = { ok: true, sessions: [] };
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
  });
  await page.goto(baseURL, { waitUntil: 'networkidle' });

  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
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
