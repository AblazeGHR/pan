/* Navigation-tag click highlight — real-browser verification.
 *
 * Drives the existing mobile-message-navigation harness, which mounts the
 * REAL MessageNavigationDock/MessageNavigationRail with mocked stores and a
 * routed fixture history (no Pan instance involved).  Verifies the accepted
 * change: after a tag click completes a jump, the clicked tag reuses the
 * nearest-tag highlight state and CSS (is-viewport-target: inverted color +
 * scale 1.16), earlier nearest highlight is superseded, and real user input
 * releases the highlight so it is never locked.
 *
 * The Vite server binds an OS-assigned owned port (never 8767/8768).
 */
/* global URL, fetch, console, document, process, window, getComputedStyle */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const cwd = fileURLToPath(new URL('..', import.meta.url));
const shotsDir = path.join(cwd, 'test-results', 'navigation-jump-highlight');
const fixtureMessages = Array.from({ length: 48 }, (_, index) => ({
  role: 'user',
  content: `Chromium jump highlight ${String(index + 1).padStart(2, '0')} ${'preview detail '.repeat(4)}`,
}));

const server = await createServer({
  configFile: false,
  root: path.join(cwd, 'src'),
  cacheDir: path.join(cwd, 'test-results/navigation-jump-highlight/vite-cache'),
  resolve: { alias: { '@': path.join(cwd, 'src') } },
  plugins: [react(), tailwindcss()],
  server: { host: '127.0.0.1', port: 0, strictPort: false, proxy: {} },
});
await server.listen();
const address = server.httpServer?.address();
assert.ok(address && typeof address === 'object', 'Vite did not listen');
const port = address.port;
assert.ok(port !== 8767 && port !== 8768, `owned port must not be a Pan port (got ${port})`);
const baseURL = `http://127.0.0.1:${port}/mobile-message-navigation-harness.html`;
console.log(`[jump-highlight-e2e] vite ready on owned dynamic port ${port}`);

let browser;
const failures = [];
const check = (name, condition, detail = '') => {
  if (condition) console.log(`[jump-highlight-e2e] ok: ${name}`);
  else { failures.push(name); console.error(`[jump-highlight-e2e] FAIL: ${name} ${detail}`); }
};

try {
  let ready = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try { if ((await fetch(baseURL)).ok) { ready = true; break; } } catch { /* starting */ }
    await delay(250);
  }
  assert.ok(ready, `Vite did not become ready at ${baseURL}`);

  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
  const pageErrors = [];
  const panRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => {
    if (/:(8767|8768)(\/|$)/.test(request.url())) panRequests.push(request.url());
  });
  const fixture = JSON.stringify({
    history: fixtureMessages, total: fixtureMessages.length, start: 0, hasMore: false,
  });
  await page.route('**/api/**', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: fixture,
  }));

  await page.goto(baseURL, { waitUntil: 'networkidle' });
  await page.locator('.message-navigation-marker').first().waitFor({ state: 'visible' });
  await page.waitForFunction(() =>
    document.querySelector('.message-navigation-rail')?.getAttribute('data-index-status') === 'ready');

  const markerState = () => page.evaluate(() => {
    const highlighted = document.querySelector('.message-navigation-marker.is-viewport-target');
    const normal = [...document.querySelectorAll('.message-navigation-marker')]
      .find((element) => element !== highlighted);
    const transform = highlighted ? getComputedStyle(highlighted).transform : 'none';
    const scale = !highlighted || transform === 'none' ? 1 : Number(transform.slice(7).split(',')[0]);
    return {
      count: document.querySelectorAll('.message-navigation-marker.is-viewport-target').length,
      fromEnd: highlighted?.getAttribute('data-from-end') ?? null,
      ariaCurrent: highlighted?.getAttribute('aria-current') ?? null,
      scale,
      color: highlighted ? getComputedStyle(highlighted).color : null,
      normalColor: normal ? getComputedStyle(normal).color : null,
    };
  });
  const readFromEnd = (index) => page.evaluate((position) =>
    document.querySelectorAll('.message-navigation-marker')[position]?.getAttribute('data-from-end'),
  index);

  // 1. Click a tag: the clicked tag takes the nearest-tag highlight.
  const clickedFromEnd = await readFromEnd(2);
  await page.locator('.message-navigation-marker').nth(2).click();
  await page.waitForFunction((fromEnd) =>
    document.querySelector(`.message-navigation-marker[data-from-end="${fromEnd}"]`)
      ?.classList.contains('is-viewport-target'), clickedFromEnd);
  await delay(300); // the shared CSS transitions transform over 150ms
  let state = await markerState();
  check('exactly the clicked tag is highlighted', state.count === 1 && state.fromEnd === clickedFromEnd,
    JSON.stringify(state));
  check('highlighted tag exposes aria-current and the enlarged scale',
    // This harness runs at a mobile width, where the shared CSS reduces the
    // is-viewport-target scale to 1.04 (1.16 is the desktop value).
    state.ariaCurrent === 'location' && Math.abs(state.scale - 1.04) < 0.02, JSON.stringify(state));
  check('highlighted tag uses the inverted color, not the normal marker color',
    state.color !== null && state.normalColor !== null && state.color !== state.normalColor);
  const jumps = await page.evaluate(() => window.__scrubJumpCalls);
  check('the real jump path ran before the highlight', jumps >= 1, `jumps=${jumps}`);
  await page.screenshot({ path: path.join(shotsDir, 'mobile-clicked-tag-highlight.png') });

  // 2. Real user input releases the clicked highlight (never locked).
  await page.mouse.move(195, 400);
  await page.mouse.wheel(0, 160);
  await page.waitForFunction(() =>
    document.querySelectorAll('.message-navigation-marker.is-viewport-target').length === 0);
  check('wheel input releases the clicked-tag highlight', (await markerState()).count === 0);

  // 3. Keyboard activation still jumps and highlights.
  const keyboardFromEnd = await readFromEnd(3);
  await page.locator('.message-navigation-marker').nth(3).focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction((fromEnd) =>
    document.querySelector(`.message-navigation-marker[data-from-end="${fromEnd}"]`)
      ?.classList.contains('is-viewport-target'), keyboardFromEnd);
  check('keyboard activation highlights the activated tag',
    (await markerState()).fromEnd === keyboardFromEnd);

  // 4. A fresh click after release highlights again (not permanently off).
  const againFromEnd = await readFromEnd(4);
  await page.locator('.message-navigation-marker').nth(4).click();
  await page.waitForFunction((fromEnd) =>
    document.querySelector(`.message-navigation-marker[data-from-end="${fromEnd}"]`)
      ?.classList.contains('is-viewport-target'), againFromEnd);
  state = await markerState();
  check('a new click moves the highlight to the new tag',
    state.count === 1 && state.fromEnd === againFromEnd, JSON.stringify(state));

  check('no page errors', pageErrors.length === 0, pageErrors.join('; '));
  check('no 8767/8768 traffic', panRequests.length === 0, panRequests.join('; '));
} finally {
  await browser?.close();
  await server.close();
}

if (failures.length) {
  console.error(`[jump-highlight-e2e] ${failures.length} check(s) failed: ${failures.join(', ')}`);
  process.exit(1);
}
console.log('[jump-highlight-e2e] all checks passed; screenshot in packages/web/test-results/navigation-jump-highlight/');
