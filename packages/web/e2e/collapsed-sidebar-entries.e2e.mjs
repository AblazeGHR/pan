/* global Element, EventTarget, URL, console, document, fetch, process, window */
// Real-browser verification for the desktop collapsed sidebar page entries.
//
// Regression context: commit 95a9d4b6 extracted ViewEntryGroups for the
// expanded strip but dropped the collapsed rail's NavLinks, so on desktop a
// collapsed sidebar showed only expand/settings/theme and every page entry
// was unreachable until the sidebar was expanded again.
//
// This harness boots the REAL app (main.tsx + App layout + Sidebar +
// ViewEntryRail) through the Vite dev server in built-in mock mode (?mock=1),
// with the API/WebSocket stubbed and the proxy disabled on an isolated port.
// It walks desktop expand → collapse → every entry navigation → expand, and
// then verifies the mobile collapsed behavior stays unchanged (no rail).
import assert from 'node:assert/strict';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium, expect } from '@playwright/test';
import { createServer } from 'vite';

const cwd = fileURLToPath(new URL('..', import.meta.url));

// Owned dynamic port: scan a private range, never 8767/8768.
async function findFreePort(from = 5290, to = 5299) {
  for (const port of [...Array(to - from + 1).keys()].map((index) => from + index)) {
    assert.notEqual(port, 8767, 'must not probe 8767');
    assert.notEqual(port, 8768, 'must not probe 8768');
    const free = await new Promise((resolve) => {
      const server = net.createServer();
      server.once('error', () => resolve(false));
      server.listen(port, '127.0.0.1', () => server.close(() => resolve(true)));
    });
    if (free) return port;
  }
  throw new Error(`no free port in ${from}-${to}`);
}

const port = await findFreePort();
const baseURL = `http://127.0.0.1:${port}/?mock=1`;

const server = await createServer({
  configFile: path.join(cwd, 'vite.config.ts'),
  cacheDir: path.join(cwd, 'test-results/collapsed-sidebar-entries/vite-cache'),
  server: { host: '127.0.0.1', port, strictPort: true, proxy: {} },
});
await server.listen();

const entries = [
  { name: 'Chat', path: '/' },
  { name: 'Editor', path: '/editor' },
  { name: 'Jobs', path: '/jobs' },
  { name: 'Terminal', path: '/terminals' },
];

const railNav = (page) => page.locator('nav[aria-label="页面入口"]');
const groupBox = (page) => page.locator('div[aria-label^="界面入口，第"]');

const computedColor = (page, name) =>
  page.getByRole('link', { name }).evaluate((element) => getComputedStyle(element).color);

let browser;
try {
  let ready = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(baseURL);
      if (response.ok) {
        ready = true;
        break;
      }
    } catch {
      /* vite warming up */
    }
    await delay(250);
  }
  assert.ok(ready, `vite did not become ready at ${baseURL}`);

  browser = await chromium.launch({ headless: true });

  const stubPage = async (page) => {
    const pageErrors = [];
    const consoleErrors = [];
    const protectedRequests = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    page.on('request', (request) => {
      if (/:(8767|8768)(\/|$)/.test(request.url())) protectedRequests.push(request.url());
    });
    // One handler: terminal list needs an array result, everything else an object.
    await page.route('**/api/**', (route) => {
      const url = route.request().url();
      const body = url.includes('/api/terminals')
        ? JSON.stringify({ ok: true, result: [] })
        : JSON.stringify({ ok: true, result: {} });
      route.fulfill({ status: 200, contentType: 'application/json', body });
    });
    await page.addInitScript(() => {
      class StubWebSocket extends EventTarget {
        static OPEN = 1;
        static CLOSED = 3;
        readyState = 1;
        send() {}
        close() {}
      }
      window.WebSocket = StubWebSocket;
    });
    return { pageErrors, consoleErrors, protectedRequests };
  };

  // ── Desktop: expand → collapse → every entry → expand ──────────────
  {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    const { pageErrors, consoleErrors, protectedRequests } = await stubPage(page);
    await page.goto(baseURL, { waitUntil: 'networkidle' });

    // Expanded: the grouped strip is present, the collapsed rail is not.
    await groupBox(page).waitFor({ state: 'visible', timeout: 15000 });
    assert.equal(
      await railNav(page).count(),
      0,
      'expanded sidebar must not render the collapsed rail',
    );
    await page.getByRole('button', { name: 'Collapse sidebar' }).waitFor({ state: 'visible' });

    // Collapse via the header button.
    await page.getByRole('button', { name: 'Collapse sidebar' }).click();
    const nav = railNav(page);
    await nav.waitFor({ state: 'visible', timeout: 5000 });
    assert.equal(
      await groupBox(page).count(),
      0,
      'collapsed sidebar must not render the expanded strip',
    );
    await page.getByTitle('Expand sidebar').waitFor({ state: 'visible' });

    // The rail is the 48px vertical bar.
    const navBox = await nav.boundingBox();
    assert.equal(
      Math.round(navBox.width),
      48,
      `collapsed rail must be 48px wide (got ${navBox.width})`,
    );

    // Every shared entry is present with tooltip + accessible name, and the
    // rail holds exactly the four page links (expand/settings/theme are buttons).
    for (const entry of entries) {
      const link = page.getByRole('link', { name: entry.name });
      await link.waitFor({ state: 'visible', timeout: 5000 });
      assert.equal(await link.getAttribute('title'), entry.name, `${entry.name} tooltip`);
      assert.equal(await link.getAttribute('href'), entry.path, `${entry.name} href`);
    }
    assert.equal(
      await nav.locator('a').count(),
      entries.length,
      'rail must hold exactly the shared entries',
    );

    // Active state on the initial route (Chat), visually distinct from the rest.
    const accentColor = await computedColor(page, 'Chat');
    const idleColor = await computedColor(page, 'Editor');
    assert.notEqual(accentColor, idleColor, 'active entry must be visually distinct');
    await expect(page.getByRole('link', { name: 'Chat' })).toHaveClass(/text-accent/);

    // Click every entry: navigation + active state follows the route, and the
    // sidebar stays collapsed across navigation. The URL changes before the
    // router state re-render lands, so class checks use retrying web-first
    // assertions instead of one-shot reads.
    for (const entry of entries) {
      await page.getByRole('link', { name: entry.name }).click();
      await page.waitForURL(`**${entry.path}`, { timeout: 5000 });
      assert.equal(new URL(page.url()).pathname, entry.path, `${entry.name} navigation`);
      await nav.waitFor({ state: 'visible' });
      const link = page.getByRole('link', { name: entry.name });
      await expect(link).toHaveClass(/text-accent/, { timeout: 5000 });
      await expect(link).toHaveClass(/bg-accent\/10/);
      for (const other of entries) {
        if (other.name === entry.name) continue;
        await expect(page.getByRole('link', { name: other.name })).not.toHaveClass(/text-accent/);
      }
    }

    // Expand restores the full sidebar with the grouped strip.
    await page.getByTitle('Expand sidebar').click();
    await groupBox(page).waitFor({ state: 'visible', timeout: 5000 });
    assert.equal(
      await railNav(page).count(),
      0,
      'expanded sidebar must not render the collapsed rail',
    );

    assert.deepEqual(protectedRequests, [], 'protected ports were requested');
    assert.deepEqual(pageErrors, [], 'desktop page errors');
    assert.deepEqual(consoleErrors, [], 'desktop console errors');
    console.log(JSON.stringify({ width: 1280, desktop: 'ok', port }));
    await context.close();
  }

  // ── Mobile regression: collapsed stays drawer-only, no vertical rail ──
  {
    const context = await browser.newContext({
      viewport: { width: 390, height: 844 },
      deviceScaleFactor: 2,
      isMobile: true,
      hasTouch: true,
    });
    const page = await context.newPage();
    const { pageErrors, consoleErrors, protectedRequests } = await stubPage(page);
    // Persisted collapsed state from desktop must NOT produce a rail on mobile.
    await page.addInitScript(() => {
      localStorage.setItem('pan:sidebarCollapsed', '1');
    });
    await page.goto(baseURL, { waitUntil: 'networkidle' });

    assert.equal(await railNav(page).count(), 0, 'mobile must never render the collapsed rail');
    await page
      .getByRole('button', { name: '打开侧边栏' })
      .waitFor({ state: 'visible', timeout: 15000 });

    // Existing mobile behavior: the drawer carries the grouped entries.
    await page.getByRole('button', { name: '打开侧边栏' }).click();
    const box = groupBox(page);
    await box.waitFor({ state: 'visible', timeout: 5000 });
    await page.waitForTimeout(350);
    assert.match(
      (await box.getAttribute('aria-label')) ?? '',
      /第 1 组/,
      'mobile drawer shows the entry strip',
    );
    assert.equal(await railNav(page).count(), 0, 'mobile drawer must not gain a rail');
    for (const name of ['Chat', 'Editor', 'Jobs']) {
      await page.getByRole('link', { name }).waitFor({ state: 'visible' });
    }

    // Closing the drawer leaves no rail behind (collapsed on mobile = closed).
    await page.getByTestId('mobile-sidebar-close').click();
    await page
      .getByRole('button', { name: '打开侧边栏' })
      .waitFor({ state: 'visible', timeout: 5000 });
    assert.equal(await railNav(page).count(), 0, 'closed mobile drawer must not leave a rail');

    assert.deepEqual(protectedRequests, [], 'protected ports were requested');
    assert.deepEqual(pageErrors, [], 'mobile page errors');
    assert.deepEqual(consoleErrors, [], 'mobile console errors');
    console.log(JSON.stringify({ width: 390, mobile: 'ok', port }));
    await context.close();
  }

  console.log('ALL PASS');
} finally {
  await browser?.close();
  await server.close();
}
