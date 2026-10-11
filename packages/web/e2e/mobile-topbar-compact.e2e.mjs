/* global URL, fetch, console, document, innerWidth, getComputedStyle */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const cwd = fileURLToPath(new URL('..', import.meta.url));
const port = 5197;
const baseURL = `http://127.0.0.1:${port}/topbar-compact-harness.html`;
const viewports = [320, 360, 390];

async function assertPortAvailable() {
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  });
}

await assertPortAvailable();
// Programmatic server (no app vite.config proxy): cacheDir is isolated under
// test-results so the shared canonical node_modules is never written to, and
// /api traffic is intercepted by Playwright instead of any Pan instance.
const server = await createServer({
  configFile: false,
  root: path.join(cwd, 'src'),
  cacheDir: path.join(cwd, 'test-results/topbar-compact/vite-cache'),
  resolve: { alias: { '@': path.join(cwd, 'src') } },
  plugins: [react(), tailwindcss()],
  server: { host: '127.0.0.1', port, strictPort: true, proxy: {} },
});
await server.listen();
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
      // Vite is still starting.
    }
    await delay(250);
  }
  assert.ok(ready, `Vite did not become ready at ${baseURL}`);

  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 320, height: 844 }, deviceScaleFactor: 1 });
  const pageErrors = [];
  const consoleErrors = [];
  const failedRequests = [];
  const protectedRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('requestfailed', (request) => failedRequests.push(`${request.url()}: ${request.failure()?.errorText}`));
  page.on('request', (request) => {
    if (/:(8767|8768)(\/|$)/.test(request.url())) protectedRequests.push(request.url());
  });
  // All API traffic is mocked; nothing reaches a Pan instance or provider.
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.includes('/native-control')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, result: { workerId: 'worker-123', mode: 'pan', generation: 1, error: null, enabled: false } }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, result: {} }) });
  });
  await page.goto(baseURL, { waitUntil: 'networkidle' });
  try {
    await page.waitForFunction(() => typeof window.__setTopBarScenario === 'function', null, { timeout: 10000 });
  } catch {
    throw new Error(`Harness failed to initialize. Page errors: ${pageErrors.join('; ')}; console: ${consoleErrors.join('; ')}; requests: ${failedRequests.join('; ')}`);
  }

  async function setScenario(scenario) {
    await page.evaluate((value) => window.__setTopBarScenario(value), scenario);
  }

  async function measure(width, scenario) {
    await page.setViewportSize({ width, height: 844 });
    await setScenario(scenario);
    const nav = page.getByRole('button', { name: /message navigation rail/ });
    const topbar = page.getByTestId('topbar');
    await topbar.waitFor({ state: 'visible' });
    if (scenario === 'nav-off') await nav.waitFor({ state: 'detached' });
    else await nav.waitFor({ state: 'visible' });
    // The chat-view group only exists when React has flushed isMobile=false;
    // waiting for it (or its absence) removes the viewport-change race from
    // the geometry snapshot below.
    if (scenario !== 'none') {
      const chatViewGroup = page.getByRole('group', { name: 'Chat view' });
      if (width >= 768) await chatViewGroup.waitFor({ state: 'visible' });
      else await chatViewGroup.waitFor({ state: 'detached' });
    }
    const geometry = await page.evaluate(() => {
      const rect = (element) => {
        const { x, y, width: w, height: h, right, bottom } = element.getBoundingClientRect();
        return { x, y, width: w, height: h, right, bottom };
      };
      const bar = document.querySelector('[data-testid="topbar"]');
      const actions = document.querySelector('[data-testid="topbar-actions"]');
      const title = bar?.querySelector('.font-medium.truncate');
      const status = bar?.querySelector('span[title="running"], span[title="offline"]');
      const style = bar ? getComputedStyle(bar) : null;
      // Denominator for the 50% name check: the top bar root element's FULL
      // visible width (hamburger-reserved padding and every control included,
      // nothing subtracted).
      const contentWidth = bar ? bar.getBoundingClientRect().width : 0;
      const padding = style
        ? { left: parseFloat(style.paddingLeft), right: parseFloat(style.paddingRight) }
        : null;
      const controls = [...(bar?.querySelectorAll('button') ?? [])].map((button) => ({
        title: button.title,
        label: button.getAttribute('aria-label'),
        rect: rect(button),
      }));
      return {
        viewport: { width: innerWidth, documentWidth: document.documentElement.scrollWidth },
        topbar: bar ? rect(bar) : null,
        contentWidth,
        padding,
        status: status ? rect(status) : null,
        title: title ? { text: title.textContent, fullTitle: title.title, rect: rect(title) } : null,
        actions: actions ? rect(actions) : null,
        controls,
      };
    });
    assert.ok(geometry.topbar, 'TopBar missing');
    if (width < 768) {
      assert.ok(geometry.topbar.height <= 48, `Mobile TopBar too tall: ${JSON.stringify(geometry)}`);
    } else {
      // Desktop: 43 without the 32px navigation toggle, 49 with it (baseline
      // behavior, unchanged).
      assert.ok(geometry.topbar.height <= 49, `Desktop TopBar too tall: ${JSON.stringify(geometry)}`);
    }
    assert.equal(geometry.viewport.documentWidth, width, `Horizontal overflow at ${width}px: ${JSON.stringify(geometry)}`);
    assert.ok(geometry.controls.every(({ rect }) => rect.x >= 0 && rect.right <= width), `Control outside viewport: ${JSON.stringify(geometry)}`);
    assert.ok(geometry.controls.every(({ rect }) => rect.y >= geometry.topbar.y && rect.bottom <= geometry.topbar.bottom), `Control outside TopBar: ${JSON.stringify(geometry)}`);
    if (scenario !== 'none') {
      assert.ok(geometry.status, `Worker status indicator missing in ${scenario}`);
      assert.ok(geometry.status.width >= 8, `Worker status indicator was compressed in ${scenario}: ${JSON.stringify(geometry.status)}`);
      assert.ok(geometry.status.x >= 0 && geometry.status.right <= width && geometry.status.y >= geometry.topbar.y && geometry.status.bottom <= geometry.topbar.bottom);
      assert.ok(geometry.title, 'Session title missing');
      const expectedTitle = scenario === 'short'
        ? 'Session'
        : 'A very long session name with an unbreakable identifier abcdefghijklmnopqrstuvwxyz0123456789';
      assert.equal(geometry.title.fullTitle, expectedTitle);
      if (width >= 768) {
        assert.ok(geometry.title.rect.width <= 200, `Desktop title exceeded its 200px cap: ${JSON.stringify(geometry.title)}`);
      } else {
        // Requirement: the Session name region takes at least half of the top
        // bar root element's full visible width (hamburger padding included,
        // all buttons inside the denominator).
        assert.ok(
          geometry.title.rect.width >= geometry.topbar.width / 2 - 0.5,
          `Mobile name region ${geometry.title.rect.width}px is below half of the ${geometry.topbar.width}px top bar width: ${JSON.stringify(geometry)}`,
        );
        // Small buttons stay reasonably tappable.
        assert.ok(
          geometry.controls.every(({ rect }) => rect.width >= 24 && rect.height >= 24),
          `Control below 24px tap size at ${width}px: ${JSON.stringify(geometry.controls)}`,
        );
      }
      assert.ok(geometry.actions && geometry.actions.y >= geometry.topbar.y && geometry.actions.bottom <= geometry.topbar.bottom);
      const expectedActions = width >= 768
        ? ['Restart worker', 'Delayed restart: after task completion, before queued work', 'Interrupt', 'Kill worker']
        : ['Restart worker', 'Kill worker'];
      if (scenario === 'worker' || scenario === 'codex') {
        for (const expected of expectedActions) {
          assert.ok(geometry.controls.some((control) => control.title === expected), `${expected} missing in ${scenario} at ${width}px`);
        }
        const removed = ['Interrupt', 'Delayed restart: after task completion, before queued work'];
        if (width < 768) {
          for (const gone of removed) {
            assert.ok(!geometry.controls.some((control) => control.title === gone), `${gone} must be removed from the mobile top bar`);
          }
        }
      }
      if (scenario === 'codex') {
        const terminalEntry = geometry.controls.find((control) => control.label === 'Terminal TUI');
        if (width >= 768) {
          assert.ok(terminalEntry, 'Desktop Terminal TUI entry missing for codex session');
        } else {
          assert.ok(!terminalEntry, 'Mobile top bar must not host the Terminal TUI entry');
        }
      }
    }
    if (scenario === 'offline') {
      assert.ok(geometry.controls.some(({ rect }) => rect.width > 0));
      assert.match(await page.getByRole('button', { name: 'Start', exact: true }).textContent(), /Start/);
    }
    if (scenario !== 'nav-off') {
      const navRect = geometry.controls.find(({ label }) => label === 'Open message navigation rail')?.rect;
      assert.ok(navRect, 'Navigation toggle missing');
      assert.equal(navRect.width, 28);
      assert.equal(navRect.height, 28);
      const rightInset = scenario === 'none' || width >= 768 ? 16 : 4;
      assert.ok(Math.abs(navRect.right - (width - rightInset)) <= 1, `Navigation toggle is not rightmost: ${JSON.stringify(geometry)}`);
    }
    console.log(`${width}px ${scenario}: ${JSON.stringify(geometry)}`);
    return geometry;
  }

  for (const width of viewports) {
    const long = await measure(width, 'worker');
    assert.ok(long.controls.every(({ rect }) => Math.abs((rect.y + rect.height / 2) - (long.topbar.y + long.topbar.height / 2)) <= 0.5), `Controls are not on one centered row at ${width}px`);
    await measure(width, 'offline');
    await measure(width, 'none');
    await measure(width, 'short');
    await measure(width, 'nav-off');
    await measure(width, 'codex');
  }

  const desktop = await measure(1440, 'nav-off');
  // 43px = 8+8 padding + 26px chat-view group + 1px border, measured against
  // the baseline checkout in this environment (the old 41 came from racing
  // the isMobile flush; the group wait above removes that race).
  assert.equal(desktop.topbar.height, 43, `Desktop TopBar height changed: ${JSON.stringify(desktop.topbar)}`);
  assert.equal(desktop.controls.find(({ title }) => title === 'Restart worker')?.rect.height, 24);
  assert.equal(desktop.controls.find(({ title }) => title === 'Interrupt')?.rect.height, 24);
  assert.equal(desktop.controls.find(({ title }) => title === 'Kill worker')?.rect.height, 24);
  await measure(1440, 'worker');
  const desktopCodex = await measure(1440, 'codex');
  assert.ok(desktopCodex.controls.some((control) => control.label === 'Terminal TUI'), 'Desktop codex session keeps the Terminal TUI entry');

  await setScenario('worker');
  const nav = page.getByRole('button', { name: 'Open message navigation rail' });
  await nav.click();
  assert.equal(await page.getByRole('button', { name: 'Close message navigation rail' }).getAttribute('aria-expanded'), 'true');
  await page.locator('body').click({ position: { x: 5, y: 100 } });
  let navFocusedByKeyboard = false;
  // Desktop DOM has 8 focusable controls before the toggle, mobile 3 — allow
  // enough Tab presses for either viewport.
  for (let attempt = 0; attempt < 14; attempt += 1) {
    await page.keyboard.press('Tab');
    navFocusedByKeyboard = await page.evaluate(() => document.activeElement?.getAttribute('aria-label') === 'Close message navigation rail');
    if (navFocusedByKeyboard) break;
  }
  assert.ok(navFocusedByKeyboard, 'Navigation toggle was not reachable by keyboard Tab');
  const focusRing = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
  assert.notEqual(focusRing, 'none', 'Navigation keyboard focus ring missing');
  await page.keyboard.press('Space');
  assert.equal(await page.getByRole('button', { name: 'Open message navigation rail' }).getAttribute('aria-expanded'), 'false');
  await page.getByRole('button', { name: 'Open message navigation rail' }).click();
  assert.equal(await page.getByRole('button', { name: 'Close message navigation rail' }).getAttribute('aria-expanded'), 'true');

  assert.deepEqual(pageErrors, [], `Browser errors: ${pageErrors.join('; ')}`);
  assert.deepEqual(consoleErrors, [], `Console errors: ${consoleErrors.join('; ')}`);
  assert.deepEqual(protectedRequests, [], `Unexpected protected-port requests: ${protectedRequests.join(', ')}`);
} finally {
  await browser?.close();
  await server.close();
  await delay(300);
}
