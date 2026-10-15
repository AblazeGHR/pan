/* Message bookmarks phase-1 mock harness — real browser interaction check.
 *
 * Standalone and isolated: a programmatic Vite server on an OS-assigned
 * dynamic port (never 8767/8768), its own cache dir, no Pan API routes
 * reachable (the harness has no backend calls; the script asserts that).
 * Verifies the interactive mock only — it is NOT evidence of a production
 * bookmark frontend.
 */
/* global URL, fetch, console, document, process, getComputedStyle */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const cwd = fileURLToPath(new URL('..', import.meta.url));
const shotsDir = path.join(cwd, 'test-results', 'message-bookmarks');
const SESSION_A_USER = 'pan:1111111111111111111111111111aa01';
const SESSION_A_PLAIN = 'pan:1111111111111111111111111111aa02';
const SESSION_B_ID = 'ses_bookmarkdemobbbb';
const SESSION_A_ID = 'ses_bookmarkdemoaaaa';

const server = await createServer({
  configFile: false,
  root: path.join(cwd, 'src'),
  cacheDir: path.join(cwd, 'test-results/message-bookmarks/vite-cache'),
  resolve: { alias: { '@': path.join(cwd, 'src') } },
  plugins: [react(), tailwindcss()],
  // Port 0: the OS assigns a free owned port; never a shared Pan port.
  server: { host: '127.0.0.1', port: 0, strictPort: false, proxy: {} },
});
await server.listen();
const address = server.httpServer?.address();
assert.ok(address && typeof address === 'object', 'Vite did not listen');
const port = address.port;
assert.ok(port !== 8767 && port !== 8768, `owned port must not be a Pan port (got ${port})`);
const baseURL = `http://127.0.0.1:${port}/message-bookmarks-harness.html`;
console.log(`[mock-e2e] vite ready on owned dynamic port ${port}`);

let browser;
const failures = [];
const check = (name, condition, detail = '') => {
  if (condition) console.log(`[mock-e2e] ok: ${name}`);
  else { failures.push(name); console.error(`[mock-e2e] FAIL: ${name} ${detail}`); }
};

const railLabels = (page) => page.locator('[data-testid="rail-labels"] li')
  .evaluateAll((items) => items.map((item) => ({
    label: item.getAttribute('data-rail-label'),
    bookmarked: item.getAttribute('data-bookmarked'),
  })));

async function openMenu(page, messageId) {
  await page.locator(`[data-bookmark-strip="${messageId}"]`).click();
  await page.locator('[data-bookmark-menu]').waitFor({ state: 'visible' });
}

try {
  let ready = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try { if ((await fetch(baseURL)).ok) { ready = true; break; } } catch { /* starting */ }
    await delay(250);
  }
  assert.ok(ready, `Vite did not become ready at ${baseURL}`);

  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const pageErrors = [];
  const apiRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => {
    const url = request.url();
    if (/:(8767|8768)(\/|$)/.test(url)) apiRequests.push(url);
    if (new URL(url).pathname.startsWith('/api/')) apiRequests.push(url);
  });

  await page.goto(baseURL, { waitUntil: 'networkidle' });
  await page.locator('[data-testid="bookmark-chat"]').waitFor();

  // 1. Initial state: five existing navigation tags, no bookmark decoration,
  //    no body bookmark tags.
  let labels = await railLabels(page);
  check('initial rail shows the five existing nav tags only',
    JSON.stringify(labels.map((entry) => entry.label)) ===
      JSON.stringify(['User', 'TA report', 'MA assign', 'System', 'MA msg']), JSON.stringify(labels));
  check('initial state has no body bookmark tag',
    (await page.locator('[data-bookmark-tag]').count()) === 0);

  // 1b. Rail tag-type filter: checked kinds are shown (whitelist), multi-select
  //     with OR semantics; an empty selection means no filter.
  await page.locator('[data-filter-toggle]').click();
  await page.locator('[data-filter-menu]').waitFor({ state: 'visible' });
  check('filter menu lists every tag kind',
    (await page.locator('[data-filter-kind]').count()) === 6);
  await page.locator('[data-filter-kind="user"]').click();
  check('checked kind is the only shown kind',
    JSON.stringify((await railLabels(page)).map((entry) => entry.label)) === JSON.stringify(['User']),
    JSON.stringify(await railLabels(page)));
  check('filter toggle shows the active selection count',
    (await page.locator('[data-filter-toggle] .bookmark-filter-count').textContent()) === '1');
  await page.locator('[data-filter-kind="system"]').click();
  const filteredLabels = (await railLabels(page)).map((entry) => entry.label).sort();
  check('multi-select combines with OR',
    JSON.stringify(filteredLabels) === JSON.stringify(['System', 'User']),
    JSON.stringify(filteredLabels));
  await page.locator('[data-filter-kind="user"]').click();
  check('toggling a kind off removes just that kind',
    JSON.stringify((await railLabels(page)).map((entry) => entry.label)) === JSON.stringify(['System']),
    JSON.stringify(await railLabels(page)));
  await page.locator('[data-filter-clear]').click();
  check('clearing the filter restores every tag',
    (await page.locator('[data-rail-marker]').count()) === 5
      && (await page.locator('[data-filter-toggle] .bookmark-filter-count').count()) === 0);
  await page.keyboard.press('Escape');
  check('filter menu closes on Escape',
    (await page.locator('[data-filter-menu]').count()) === 0);

  // 2. Existing User tag: decorate, never duplicate.
  await openMenu(page, SESSION_A_USER);
  const addItem = page.locator('[data-bookmark-action="add"]');
  check('menu offers Add bookmark while unbookmarked',
    (await addItem.count()) === 1 && (await addItem.textContent())?.includes('Add bookmark'));
  await addItem.click();
  check('user row shows bookmarked state',
    (await page.locator(`[data-message-id="${SESSION_A_USER}"]`).getAttribute('data-bookmark-state')) === 'on');
  labels = await railLabels(page);
  check('existing User tag gains the Bookmark suffix in place',
    JSON.stringify(labels.map((entry) => entry.label)) ===
      JSON.stringify(['User (Bookmark)', 'TA report', 'MA assign', 'System', 'MA msg']), JSON.stringify(labels));
  check('no duplicate RAIL tag was added for the user block',
    (await railLabels(page)).length === 5);
  check('every bookmarked block carries the body bookmark tag',
    (await page.locator(`[data-message-id="${SESSION_A_USER}"] [data-bookmark-tag]`).count()) === 1);
  check('mock annotates the 用户（书签）mapping',
    (await page.locator('[data-testid="rail-labels"]').textContent())?.includes('对应用户（书签）'));

  // 3. Plain assistant block (no nav tag): a new bookmark tag appears in
  //    body order.
  await openMenu(page, SESSION_A_PLAIN);
  await page.locator('[data-bookmark-action="add"]').click();
  check('plain assistant row receives a body bookmark tag',
    (await page.locator(`[data-message-id="${SESSION_A_PLAIN}"] [data-bookmark-tag]`).count()) === 1);
  labels = await railLabels(page);
  check('new bookmark tag is inserted in message order between the surrounding tags',
    JSON.stringify(labels.map((entry) => entry.label)) ===
      JSON.stringify(['User (Bookmark)', 'Bookmark', 'TA report', 'MA assign', 'System', 'MA msg']),
    JSON.stringify(labels));

  // 4. Remove/restore: user tag returns to its original type; the added tag
  //    is removed entirely.
  await openMenu(page, SESSION_A_USER);
  const removeItem = page.locator('[data-bookmark-action="remove"]');
  check('menu offers Remove bookmark while bookmarked',
    (await removeItem.count()) === 1 && (await removeItem.textContent())?.includes('Remove bookmark'));
  await removeItem.click();
  labels = await railLabels(page);
  check('removing restores the original User tag',
    JSON.stringify(labels.map((entry) => entry.label)) ===
      JSON.stringify(['User', 'Bookmark', 'TA report', 'MA assign', 'System', 'MA msg']), JSON.stringify(labels));
  await openMenu(page, SESSION_A_PLAIN);
  await page.locator('[data-bookmark-action="remove"]').click();
  check('removing the added tag deletes it',
    (await page.locator('[data-bookmark-tag]').count()) === 0 &&
    (await railLabels(page)).length === 5);

  // 4b. Scope: tool and thinking blocks have no bookmark entry at all.
  check('tool and thinking rows have no bookmark strip/menu entry',
    (await page.locator('[data-bookmark-strip="pan:1111111111111111111111111111aa03"]').count()) === 0 &&
    (await page.locator('[data-bookmark-strip="pan:1111111111111111111111111111aa04"]').count()) === 0);

  // 4a. The hit area is the left gutter up to the colored vertical line:
  //     clicking the gutter opens the menu; clicking the content does not.
  await openMenu(page, SESSION_A_PLAIN);
  check('clicking the left gutter (up to the colored line) opens the menu',
    (await page.locator('[data-bookmark-action]').count()) === 1);
  await page.keyboard.press('Escape');
  const geometry = await page.evaluate((id) => {
    const row = document.querySelector(`[data-message-id="${id}"]`);
    const strip = row?.querySelector('[data-bookmark-strip]');
    const msg = row?.querySelector('.msg');
    if (!row || !strip || !msg) return null;
    const rowBox = row.getBoundingClientRect();
    const stripBox = strip.getBoundingClientRect();
    const msgBox = msg.getBoundingClientRect();
    return {
      leftAtEdge: Math.abs(stripBox.left - rowBox.left) <= 1,
      rightAtLine: Math.abs(stripBox.right - msgBox.left) <= 1,
    };
  }, SESSION_A_PLAIN);
  check('strip spans from the left edge to the colored vertical line',
    geometry !== null && geometry.leftAtEdge && geometry.rightAtLine, JSON.stringify(geometry));
  await page.locator(`[data-message-id="${SESSION_A_PLAIN}"] .msg.assistant`)
    .click({ position: { x: 240, y: 8 } });
  check('clicking the content itself does not open the menu',
    (await page.locator('[data-bookmark-menu]').count()) === 0);

  // 4c. An existing tag keeps its kind color and gains a composite bookmark
  //     icon (original glyph + bottom-right badge); the body adds no blue
  //     bar; the leftmost trigger display stays on the strip ::before.
  const workerMarker = page.locator('[data-rail-marker="pan:1111111111111111111111111111aa05"]');
  const workerColorBefore = await workerMarker.evaluate((element) => getComputedStyle(element).color);
  await openMenu(page, 'pan:1111111111111111111111111111aa05');
  await page.locator('[data-bookmark-action="add"]').click();
  const workerColorAfter = await workerMarker.evaluate((element) => getComputedStyle(element).color);
  check('existing tag keeps its kind color when bookmarked',
    workerColorBefore === workerColorAfter, `${workerColorBefore} -> ${workerColorAfter}`);
  const composite = await page.evaluate((id) => {
    const marker = document.querySelector(`[data-rail-marker="${id}"]`);
    const badge = marker?.querySelector('.bookmark-icon-badge');
    const glyph = marker?.querySelector('.message-navigation-marker-tag');
    if (!marker || !badge || !glyph) return null;
    const glyphBox = glyph.getBoundingClientRect();
    const badgeBox = badge.getBoundingClientRect();
    return {
      glyphText: glyph.textContent,
      // The badge overlays the original icon's bottom-right corner.
      overlapsGlyph: badgeBox.left < glyphBox.right && badgeBox.top < glyphBox.bottom,
      anchoredBottomRight: badgeBox.right >= glyphBox.right - 2 && badgeBox.bottom >= glyphBox.bottom - 2,
      badgeColor: getComputedStyle(badge).color,
      markerColor: getComputedStyle(marker).color,
    };
  }, 'pan:1111111111111111111111111111aa05');
  check('bookmarked existing tag composites a bookmark badge over its own glyph',
    composite !== null && composite.glyphText === 'Re' && composite.overlapsGlyph
      && composite.anchoredBottomRight && composite.badgeColor === composite.markerColor,
    JSON.stringify(composite));
  const bodyShadow = await page.locator('[data-message-id="pan:1111111111111111111111111111aa05"] .msg.assistant')
    .evaluate((element) => getComputedStyle(element).boxShadow);
  check('bookmarked body adds no blue bar over the role rail', bodyShadow === 'none', bodyShadow);
  const triggerBackground = await page.locator('[data-bookmark-strip="pan:1111111111111111111111111111aa05"]')
    .evaluate((element) => getComputedStyle(element).backgroundColor);
  check('leftmost gutter keeps the trigger-position display',
    triggerBackground !== 'rgba(0, 0, 0, 0)', triggerBackground);

  // 4d. The manual report button is consolidated into the same menu for
  //     reportable blocks, next to the bookmark action.
  await openMenu(page, 'pan:1111111111111111111111111111aa05');
  const reportItem = page.locator('[data-manual-report="pan:1111111111111111111111111111aa05"]');
  check('reportable block menu carries the manual report action',
    (await reportItem.count()) === 1 && (await reportItem.textContent())?.includes('手动汇报'));
  await reportItem.click();
  check('reporting marks the block (mock demo state)',
    (await page.locator('[data-manual-reported="true"]').count()) === 1);
  await openMenu(page, 'pan:1111111111111111111111111111aa05');
  check('reported block keeps a disabled 已汇报 menu entry',
    await reportItem.evaluate((element) => element.disabled)
      && (await reportItem.textContent())?.includes('已汇报'));
  await page.keyboard.press('Escape');
  await openMenu(page, 'pan:1111111111111111111111111111aa05');
  await page.locator('[data-bookmark-action="remove"]').click();

  await page.screenshot({ path: path.join(shotsDir, 'desktop-dark-initial.png') });

  // Re-add one of each for the screenshots.
  await openMenu(page, SESSION_A_USER);
  await page.locator('[data-bookmark-action="add"]').click();
  await openMenu(page, SESSION_A_PLAIN);
  await page.locator('[data-bookmark-action="add"]').click();

  // 5. Session isolation: B keeps only its seeded bookmark; A keeps its own.
  await page.locator(`[data-harness-session="${SESSION_B_ID}"]`).click();
  labels = await railLabels(page);
  check('session B shows only its own seeded bookmark',
    JSON.stringify(labels.map((entry) => entry.label)) === JSON.stringify(['User', 'Bookmark']),
    JSON.stringify(labels));
  await page.locator(`[data-harness-session="${SESSION_A_ID}"]`).click();
  labels = await railLabels(page);
  check('switching back to A restores A bookmarks untouched',
    JSON.stringify(labels.map((entry) => entry.label)) ===
      JSON.stringify(['User (Bookmark)', 'Bookmark', 'TA report', 'MA assign', 'System', 'MA msg']));

  // 6. Clicked tag reuses the nearest-tag highlight and is released by input.
  const userMarker = page.locator(`[data-rail-marker="${SESSION_A_USER}"]`);
  await userMarker.click();
  await page.waitForFunction((id) =>
    document.querySelector(`[data-rail-marker="${id}"]`)?.classList.contains('is-viewport-target'), SESSION_A_USER);
  check('clicked tag takes the shared nearest-tag highlight',
    await userMarker.evaluate((element) => element.classList.contains('is-viewport-target')));
  // The shared CSS transitions transform over 150ms; measure after it settles.
  await delay(300);
  const scale = await userMarker.evaluate((element) => {
    const value = getComputedStyle(element).transform;
    return value === 'none' ? 1 : Number(value.slice(7).split(',')[0]);
  });
  check('clicked tag is enlarged by the shared CSS (scale 1.16)', Math.abs(scale - 1.16) < 0.02, `scale=${scale}`);
  await page.mouse.move(720, 450);
  await page.mouse.wheel(0, 120);
  await page.waitForFunction((id) =>
    !document.querySelector(`[data-rail-marker="${id}"]`)?.classList.contains('is-viewport-target'), SESSION_A_USER);
  check('real input releases the clicked-tag highlight (not locked)',
    !(await userMarker.evaluate((element) => element.classList.contains('is-viewport-target'))));

  await page.screenshot({ path: path.join(shotsDir, 'desktop-dark-bookmarked.png') });

  // 7. Light theme.
  await page.locator('[data-harness-theme="light"]').click();
  check('light theme token switch applied',
    (await page.evaluate(() => document.documentElement.dataset.theme)) === 'light');
  await page.screenshot({ path: path.join(shotsDir, 'desktop-light-bookmarked.png') });
  await page.locator('[data-harness-theme="dark"]').click();

  // 8. Mobile layout: rail collapses behind the toggle; menu still works.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('[data-harness-layout="auto"]').click();
  await page.locator('[data-bookmark-rail-toggle]').waitFor({ state: 'visible' });
  check('mobile hides the rail until toggled',
    !(await page.locator('[data-testid="bookmark-rail"]').evaluate((element) => element.classList.contains('is-open'))));
  await page.locator('[data-bookmark-rail-toggle]').click();
  check('mobile toggle opens the rail',
    await page.locator('[data-testid="bookmark-rail"]').evaluate((element) => element.classList.contains('is-open')));
  await page.locator('[data-bookmark-strip="pan:1111111111111111111111111111aa05"]').click();
  await page.locator('[data-bookmark-menu]').waitFor({ state: 'visible' });
  check('mobile strip menu opens with the state-derived action',
    (await page.locator('[data-bookmark-action]').count()) === 1);
  await page.keyboard.press('Escape');
  await page.screenshot({ path: path.join(shotsDir, 'mobile-dark-bookmarked.png') });

  check('no page errors', pageErrors.length === 0, pageErrors.join('; '));
  check('no Pan API or 8767/8768 traffic', apiRequests.length === 0, apiRequests.join('; '));
} finally {
  await browser?.close();
  await server.close();
}

if (failures.length) {
  console.error(`[mock-e2e] ${failures.length} check(s) failed: ${failures.join(', ')}`);
  process.exit(1);
}
console.log('[mock-e2e] all checks passed; screenshots in packages/web/test-results/message-bookmarks/');
