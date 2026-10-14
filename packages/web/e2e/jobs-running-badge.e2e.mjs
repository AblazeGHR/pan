/* global Element, EventTarget, URL, console, document, fetch, process, window */
// Real-browser verification for the Jobs nav running-count badge.
//
// Boots the REAL app entry (main.tsx + App layout + Sidebar + ViewEntryGroups)
// in built-in mock mode (?mock=1). Mock mode intentionally has no WebSocket
// (useWebSocket short-circuits), so the store hydrates through the Jobs page
// mount refresh, which serves /api/jobs from this script via the client-side
// fetch patch; everything else keeps flowing through the installed mock
// backend. The global WS-open hydration and job.* routing are covered by the
// useWebSocket unit tests. No request can reach a real Pan instance —
// 8767/8768 are asserted untouched, the dev proxy is disabled and the Vite
// cacheDir is isolated. Gestures go through CDP touch events (never synthetic
// DOM events), exactly like the sibling view-entry-groups-touch e2e.
import assert from 'node:assert/strict';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';

const cwd = fileURLToPath(new URL('..', import.meta.url));
const port = 5232;
const baseURL = `http://127.0.0.1:${port}/?mock=1`;

await new Promise((resolve, reject) => {
  const server = net.createServer();
  server.once('error', reject);
  server.listen(port, '127.0.0.1', () => server.close(resolve));
});

const server = await createServer({
  configFile: path.join(cwd, 'vite.config.ts'),
  cacheDir: path.join(cwd, 'test-results/jobs-running-badge/vite-cache'),
  server: { host: '127.0.0.1', port, strictPort: true, proxy: {} },
});
await server.listen();

const runningJob = (id) => ({
  jobId: id,
  kind: 'session-message',
  status: 'running',
  name: `Job ${id}`,
  description: '',
  source: { type: 'agent' },
  target: { sessionId: 'session-1' },
  paused: false,
  schedule: null,
  runCount: 0,
  createdAt: 1,
  updatedAt: 1,
});
const settledJob = (id, status) => ({ ...runningJob(id), status });

let browser;
try {
  let ready = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(baseURL);
      if (response.ok) { ready = true; break; }
    } catch { /* vite warming up */ }
    await delay(250);
  }
  assert.ok(ready, `vite did not become ready at ${baseURL}`);

  browser = await chromium.launch({ headless: true });

  const openApp = async ({ width, height, mobile }) => {
    const context = await browser.newContext({
      viewport: { width, height },
      deviceScaleFactor: 2,
      ...(mobile ? { isMobile: true, hasTouch: true } : {}),
    });
    const page = await context.newPage();
    const pageErrors = [];
    const consoleErrors = [];
    const protectedRequests = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
    page.on('request', (request) => {
      if (/:(8767|8768)(\/|$)/.test(request.url())) protectedRequests.push(request.url());
    });
    // Safety net: nothing may reach a real Pan instance even if a request
    // escapes the client-side mock.
    await page.route('**/api/**', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, result: {} }) }));
    await page.addInitScript(() => {
      window.__jobsPayload = null;
    });
    await page.goto(baseURL, { waitUntil: 'networkidle' });

    // Serve GET /api/jobs from this script (the mock backend does not know
    // that endpoint and would answer { ok: true }); delegate the rest.
    await page.evaluate(() => {
      const mockFetch = window.fetch.bind(window);
      window.fetch = async (input, init) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        const path = url.replace(/^https?:\/\/[^/]+/, '').split('?')[0] ?? '';
        if (path.startsWith('/api/jobs')) {
          await new Promise((resolve) => setTimeout(resolve, 20));
          return new Response(JSON.stringify(window.__jobsPayload), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          });
        }
        return mockFetch(input, init);
      };
    });
    return { context, page, pageErrors, consoleErrors, protectedRequests };
  };

  const setJobsPayload = (page, jobs) =>
    page.evaluate((payload) => {
      window.__jobsPayload = payload;
    }, { jobs, pinMetadataStatus: 'available' });

  const badge = (page) => page.locator('[data-testid="jobs-running-badge"]').first();
  const jobsLink = (page) => page.getByRole('link', { name: 'Jobs' });
  const chatLink = (page) => page.getByRole('link', { name: 'Chat' });

  const badgeText = async (page) => {
    const locator = badge(page);
    if (await locator.count() === 0) return null;
    return ((await locator.textContent()) ?? '').trim();
  };

  const waitForBadge = async (page, expected, label) => {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      if (await badgeText(page) === expected) return;
      await delay(100);
    }
    const state = await page.evaluate(() => ({
      payload: window.__jobsPayload?.jobs?.map((j) => `${j.jobId}:${j.status}`) ?? null,
      badgeInDom: document.querySelectorAll('[data-testid="jobs-running-badge"]').length,
      hasR1Row: document.body.innerText.includes('Job r1'),
    })).catch((e) => `evaluate failed: ${e.message}`);
    console.log('DEBUG', label, JSON.stringify(state));
    assert.equal(await badgeText(page), expected, label);
  };

  // Re-enter the Jobs page: its mount refresh reloads the unified store from
  // GET /api/jobs with the current payload.
  const reenterJobs = async (page, expected, label) => {
    await chatLink(page).click();
    await page.waitForURL((url) => url.pathname === '/', { timeout: 5000 });
    await jobsLink(page).click();
    await page.waitForURL('**/jobs', { timeout: 5000 });
    await waitForBadge(page, expected, label);
  };

  // ── Desktop: appear / persist / increment / decrement / hide ──
  {
    const { context, page, pageErrors, consoleErrors, protectedRequests } =
      await openApp({ width: 1280, height: 900 });
    await page.locator('div[aria-label^="界面入口，第"]').waitFor({ state: 'visible', timeout: 15000 });

    // Zero running: the badge must not exist at all. Measure the layout in
    // this state: the badge slot is reserved, so nothing may shift later.
    await setJobsPayload(page, [settledJob('c1', 'completed')]);
    await jobsLink(page).click();
    await page.waitForURL('**/jobs', { timeout: 5000 });
    await waitForBadge(page, null, 'no running jobs: badge must be hidden');
    const groupBox = () => page.locator('div[aria-label^="界面入口，第"]').boundingBox();
    const jobsLinkBox = () => page.getByRole('link', { name: 'Jobs' }).boundingBox();
    const navBoxZero = await groupBox();
    const linkBoxZero = await jobsLinkBox();

    // Two running: the badge shows the count on the Jobs entry.
    await setJobsPayload(page, [runningJob('r1'), runningJob('r2'), settledJob('c1', 'completed')]);
    await reenterJobs(page, '2', 'two running jobs must show 2');

    // Real layout: the badge sits BELOW the entry label, inside the entry box
    // (reserved slot, nothing clipped), and nothing shifts versus zero.
    const labelBox = await page.getByRole('link', { name: 'Jobs' }).locator('span').first().boundingBox();
    const badgeBox = await badge(page).boundingBox();
    const linkBox = await jobsLinkBox();
    assert.ok(badgeBox.y >= labelBox.y + labelBox.height - 1,
      `badge must sit below the entry label (badge.y=${badgeBox.y}, label bottom=${labelBox.y + labelBox.height})`);
    assert.ok(badgeBox.y + badgeBox.height <= linkBox.y + linkBox.height + 1,
      'badge must stay inside the entry box (reserved slot, not clipped)');
    const navBoxShown = await groupBox();
    assert.equal(navBoxShown.height, navBoxZero.height, 'nav height must not shift when the badge appears');
    const linkBoxShown = await jobsLinkBox();
    assert.equal(linkBoxShown.height, linkBoxZero.height, 'entry height must not shift when the badge appears');

    // Reuse of the Session queue badge style: accent-blue round pill.
    const style = await badge(page).evaluate((el) => {
      const computed = getComputedStyle(el);
      return { borderRadius: computed.borderRadius, minWidth: computed.minWidth };
    });
    assert.equal(style.borderRadius, '999px', 'badge must reuse the session-queue-badge pill shape');
    assert.equal(style.minWidth, '14px', 'badge must reuse the session-queue-badge size');

    // The unified store keeps the list off the Jobs page: the badge persists.
    await chatLink(page).click();
    await page.waitForURL((url) => url.pathname === '/', { timeout: 5000 });
    await waitForBadge(page, '2', 'badge persists after leaving the Jobs page');

    // Clicking the badge itself navigates (it must not swallow the click).
    await badge(page).click();
    await page.waitForURL('**/jobs', { timeout: 5000 });

    // Increment 2 -> 3 through the Jobs page mount refresh.
    await setJobsPayload(page, [runningJob('r1'), runningJob('r2'), runningJob('r3')]);
    await reenterJobs(page, '3', 'three running jobs must show 3');

    // Decrement 3 -> 1.
    await setJobsPayload(page, [runningJob('r1'), settledJob('c1', 'completed'), settledJob('c2', 'failed')]);
    await reenterJobs(page, '1', 'one running job must show 1');

    // Zero running: the badge hides again.
    await setJobsPayload(page, [settledJob('c1', 'completed')]);
    await reenterJobs(page, null, 'zero running jobs: badge must hide again');

    assert.deepEqual(protectedRequests, [], 'desktop: protected ports were requested');
    assert.deepEqual(pageErrors, [], 'desktop: page errors');
    assert.deepEqual(consoleErrors, [], 'desktop: console errors');
    await context.close();
  }

  // ── Mobile drawer: badge + CDP touch gestures ─────────────────────
  {
    const { context, page, pageErrors, consoleErrors, protectedRequests } =
      await openApp({ width: 390, height: 844, mobile: true });
    const openMobileDrawer = async () => {
      const hamburger = page.getByRole('button', { name: '打开侧边栏' });
      await hamburger.waitFor({ state: 'visible', timeout: 15000 });
      await hamburger.click();
      const groupBox = page.locator('div[aria-label^="界面入口，第"]');
      await groupBox.waitFor({ state: 'visible', timeout: 5000 });
      await page.waitForTimeout(350);
      return groupBox;
    };
    const groupLabel = (page) =>
      page.locator('div[aria-label^="界面入口，第"]').getAttribute('aria-label');
    const touchSwipe = async (page, { x, y, dx, dy = 0, steps = 8, stepDelay = 16 }) => {
      const cdp = await page.context().newCDPSession(page);
      const points = [];
      for (let i = 1; i <= steps; i += 1) {
        points.push({ x: x + (dx * i) / steps, y: y + (dy * i) / steps });
      }
      await cdp.send('Input.dispatchTouchEvent', {
        type: 'touchStart',
        touchPoints: [{ x, y, id: 1, radiusX: 2, radiusY: 2, force: 1 }],
      });
      for (const point of points) {
        await cdp.send('Input.dispatchTouchEvent', {
          type: 'touchMove',
          touchPoints: [{ x: point.x, y: point.y, id: 1, radiusX: 2, radiusY: 2, force: 1 }],
        });
        if (stepDelay) await delay(stepDelay);
      }
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      await cdp.detach();
      await page.waitForTimeout(300);
    };
    const swipeEntryRow = async (page, dx, drift = 0, fraction = 0.85) => {
      const box = await page.locator('div[aria-label^="界面入口，第"]').boundingBox();
      assert.ok(box, 'entry group container box');
      const startX = box.x + box.width * fraction;
      const endDx = dx > 0 ? Math.min(dx, box.x + box.width - startX - 2) : dx;
      await touchSwipe(page, { x: startX, y: box.y + box.height / 2, dx: endDx, dy: drift });
    };

    await openMobileDrawer();
    await setJobsPayload(page, [runningJob('r1'), runningJob('r2')]);
    await jobsLink(page).click();
    await page.waitForURL('**/jobs', { timeout: 5000 });
    await waitForBadge(page, '2', 'mobile drawer badge shows the running count');

    // Real mobile layout: badge below the label inside the entry box, and the
    // drawer entry height identical to the badge-hidden state.
    const drawerGroupBox = () => page.locator('div[aria-label^="界面入口，第"]').boundingBox();
    const mobileLabelBox = await page.getByRole('link', { name: 'Jobs' }).locator('span').first().boundingBox();
    const mobileBadgeBox = await badge(page).boundingBox();
    const mobileLinkBox = await page.getByRole('link', { name: 'Jobs' }).boundingBox();
    assert.ok(mobileBadgeBox.y >= mobileLabelBox.y + mobileLabelBox.height - 1,
      'mobile badge must sit below the entry label');
    assert.ok(mobileBadgeBox.y + mobileBadgeBox.height <= mobileLinkBox.y + mobileLinkBox.height + 1,
      'mobile badge must stay inside the entry box');
    const mobileNavShown = await drawerGroupBox();
    const mobileLinkShown = mobileLinkBox;

    // Left swipe reaches the Terminal group without navigating.
    await swipeEntryRow(page, -(390 * 0.6), Math.round(390 * 0.03));
    assert.match(await groupLabel(page), /第 2 组/, 'mobile left swipe enters the Terminal group');
    assert.equal(new URL(page.url()).pathname, '/jobs', 'mobile swipe must not navigate');
    // Right swipe returns to the group that owns the Jobs entry.
    await swipeEntryRow(page, 390 * 0.6, Math.round(390 * 0.03), 0.15);
    assert.match(await groupLabel(page), /第 1 组/, 'mobile right swipe returns to group 1');
    await waitForBadge(page, '2', 'badge is back with the Jobs entry after the swipe');

    // Tapping the Jobs entry in the drawer still navigates. The swipe arms a
    // 400ms trailing-click suppression window; wait for it to expire first.
    await page.waitForTimeout(500);
    await chatLink(page).click();
    await page.waitForURL((url) => url.pathname === '/', { timeout: 5000 });
    await jobsLink(page).click();
    await page.waitForURL('**/jobs', { timeout: 5000 });
    await waitForBadge(page, '2', 'badge visible on the Jobs page inside the drawer');

    // Zero running hides the badge on mobile too — with zero layout shift.
    await setJobsPayload(page, [settledJob('c1', 'completed')]);
    await chatLink(page).click();
    await page.waitForURL((url) => url.pathname === '/', { timeout: 5000 });
    await jobsLink(page).click();
    await page.waitForURL('**/jobs', { timeout: 5000 });
    await waitForBadge(page, null, 'mobile: zero running jobs hides the badge');
    assert.equal((await drawerGroupBox()).height, mobileNavShown.height,
      'mobile: nav height must not shift between badge shown and hidden');
    const mobileLinkZero = await page.getByRole('link', { name: 'Jobs' }).boundingBox();
    assert.equal(mobileLinkZero.height, mobileLinkShown.height,
      'mobile: entry height is stable at 0');

    assert.deepEqual(protectedRequests, [], 'mobile: protected ports were requested');
    assert.deepEqual(pageErrors, [], 'mobile: page errors');
    assert.deepEqual(consoleErrors, [], 'mobile: console errors');
    await context.close();
  }

  console.log('ALL PASS');
} finally {
  await browser?.close();
  await server.close();
}
