/* Production bookmark rail — real-browser verification.
 *
 * Mounts the REAL MessageNavigationRail (bookmark-rail-harness.html) against
 * fixture rows carrying the same `bookmarked` projection the backend emits,
 * then drives the real bookmark store overlay.  The Vite server binds an
 * OS-assigned owned port (never 8767/8768) and all API traffic is routed to
 * the fixture — no Pan instance is involved.
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
const shotsDir = path.join(cwd, 'test-results', 'message-bookmark-rail');
const ID = (n) => `pan:${String(n).padStart(32, '0')}`;
const fixture = Array.from({ length: 6 }, (_, index) => ({
  role: index === 0 || index === 3 ? 'user' : 'assistant',
  content: `fixture row ${index}`,
  messageId: ID(index + 1),
  ...(index === 0 || index === 2 ? { bookmarked: true } : {}),
}));

const server = await createServer({
  configFile: false,
  root: path.join(cwd, 'src'),
  cacheDir: path.join(cwd, 'test-results/message-bookmark-rail/vite-cache'),
  resolve: { alias: { '@': path.join(cwd, 'src') } },
  plugins: [react(), tailwindcss()],
  server: { host: '127.0.0.1', port: 0, strictPort: false, proxy: {} },
});
await server.listen();
const address = server.httpServer?.address();
assert.ok(address && typeof address === 'object', 'Vite did not listen');
const port = address.port;
assert.ok(port !== 8767 && port !== 8768, `owned port must not be a Pan port (got ${port})`);
const baseURL = `http://127.0.0.1:${port}/bookmark-rail-harness.html`;
console.log(`[bookmark-rail-e2e] vite ready on owned dynamic port ${port}`);

let browser;
const failures = [];
const check = (name, condition, detail = '') => {
  if (condition) console.log(`[bookmark-rail-e2e] ok: ${name}`);
  else { failures.push(name); console.error(`[bookmark-rail-e2e] FAIL: ${name} ${detail}`); }
};

try {
  let ready = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try { if ((await fetch(baseURL)).ok) { ready = true; break; } } catch { /* starting */ }
    await delay(250);
  }
  assert.ok(ready, `Vite did not become ready at ${baseURL}`);

  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 });
  const pageErrors = [];
  const panRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => {
    if (/:(8767|8768)(\/|$)/.test(request.url())) panRequests.push(request.url());
  });
  await page.route('**/api/**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ history: fixture, total: fixture.length, start: 0, hasMore: false }),
  }));

  await page.goto(baseURL, { waitUntil: 'networkidle' });
  await page.locator('.message-navigation-marker').first().waitFor({ state: 'visible' });
  await page.waitForFunction(() =>
    document.querySelector('.message-navigation-rail')?.getAttribute('data-index-status') === 'ready');

  const marker = (id) => page.locator(`[data-message-id="${id}"]`);
  const markerState = (id) => page.evaluate((messageId) => {
    const element = document.querySelector(`[data-message-id="${messageId}"]`);
    if (!element) return null;
    const badge = element.querySelector('.message-navigation-marker-bookmark-badge');
    const glyph = element.querySelector('.message-navigation-marker-tag')
      ?? element.querySelector('svg');
    let badgeGeometry = null;
    if (badge && glyph) {
      const glyphBox = glyph.getBoundingClientRect();
      const badgeBox = badge.getBoundingClientRect();
      badgeGeometry = {
        overlaps: badgeBox.left < glyphBox.right && badgeBox.top < glyphBox.bottom,
        bottomRight: badgeBox.right >= glyphBox.right - 2 && badgeBox.bottom >= glyphBox.bottom - 2,
      };
    }
    return {
      kind: element.getAttribute('data-kind'),
      bookmarked: element.getAttribute('data-bookmarked'),
      label: element.getAttribute('aria-label'),
      hasBadge: Boolean(badge),
      badgeGeometry,
      color: getComputedStyle(element).color,
      badgeColor: badge ? getComputedStyle(badge).color : null,
    };
  }, id);

  // 1. Projection-driven decoration: an existing tag keeps its kind and gains
  //    the composite badge + suffix; a bookmarked plain block gets its own tag.
  const decoratedUser = await markerState(ID(1));
  check('bookmarked user tag is decorated with the composite badge',
    decoratedUser?.kind === 'user' && decoratedUser.bookmarked === 'true'
      && decoratedUser.label.startsWith('User (Bookmark)') && decoratedUser.hasBadge,
    JSON.stringify(decoratedUser));
  check('composite badge overlays the glyph bottom-right in unified color',
    decoratedUser?.badgeGeometry?.overlaps === true && decoratedUser?.badgeGeometry?.bottomRight === true
      && decoratedUser.badgeColor === decoratedUser.color,
    JSON.stringify(decoratedUser));
  const plainUser = await markerState(ID(4));
  check('an unbookmarked tag keeps its own kind color, un-decorated',
    plainUser?.kind === 'user' && plainUser.bookmarked === 'false' && !plainUser.hasBadge
      && !plainUser.label.includes('(Bookmark)') && plainUser.color === decoratedUser?.color,
    JSON.stringify({ plainUser, decoratedUser }));
  const bookmarkTag = await markerState(ID(3));
  check('a bookmarked plain block gains a dedicated bookmark tag',
    bookmarkTag?.kind === 'bookmark' && bookmarkTag.bookmarked === 'true'
      && bookmarkTag.hasBadge === false && bookmarkTag.label.startsWith('Bookmark:'),
    JSON.stringify(bookmarkTag));

  await page.screenshot({ path: path.join(shotsDir, 'rail-bookmarked.png') });

  // 2. Filter (multi-select OR, empty = no filter) on the real rail.
  await page.locator('[data-navigation-filter-toggle]').click();
  await page.locator('[data-navigation-filter-menu]').waitFor({ state: 'visible' });
  await page.locator('[data-navigation-filter-kind="bookmark"]').click();
  check('checking the bookmark kind leaves only bookmark tags visible',
    (await page.locator('[data-kind="bookmark"]').count()) === 1
      && (await page.locator('[data-kind="user"]').count()) === 0
      && (await page.locator('.message-navigation-rail').getAttribute('data-visible-targets')) === '1');
  await page.locator('[data-navigation-filter-kind="user"]').click();
  check('multi-select combines with OR on the real rail',
    (await page.locator('[data-kind="bookmark"]').count()) === 1
      && (await page.locator('[data-kind="user"]').count()) === 2);
  await page.locator('[data-navigation-filter-clear]').click();
  check('clearing restores every tag',
    (await page.locator('.message-navigation-rail').getAttribute('data-visible-targets')) === '3');
  await page.keyboard.press('Escape');
  check('filter menu closes on Escape',
    (await page.locator('[data-navigation-filter-menu]').count()) === 0);

  // 3. Store overlay (optimistic mutation / dual-client broadcast): a mark
  //    adds a tag without a refetch, an unmark restores the original one.
  await page.evaluate((id) => window.__markBookmark(id, true), ID(2));
  await page.waitForFunction((id) =>
    document.querySelector(`[data-message-id="${id}"]`)?.getAttribute('data-kind') === 'bookmark',
  ID(2));
  check('overlay mark materialises a bookmark tag without a refetch',
    (await markerState(ID(2)))?.kind === 'bookmark');
  await page.evaluate((id) => window.__markBookmark(id, false), ID(1));
  await page.waitForFunction((id) =>
    document.querySelector(`[data-message-id="${id}"]`)?.getAttribute('data-bookmarked') === 'false',
  ID(1));
  const released = await markerState(ID(1));
  check('overlay unmark restores the original tag',
    released?.kind === 'user' && released.hasBadge === false
      && !released.label.startsWith('User (Bookmark)'),
    JSON.stringify(released));

  // 4. A clicked tag still takes the shared nearest-tag highlight.
  await marker(ID(4)).click();
  await page.waitForFunction((id) =>
    document.querySelector(`[data-message-id="${id}"]`)?.classList.contains('is-viewport-target'),
  ID(4));
  await delay(250);
  const scale = (await marker(ID(4)).evaluate((element) => getComputedStyle(element).transform));
  check('clicked tag highlight still applies (scale 1.16)',
    scale !== 'none' && Number(scale.slice(7).split(',')[0]) > 1.1, scale);

  await page.screenshot({ path: path.join(shotsDir, 'rail-overlay-updated.png') });

  // 5. Highlight state (clicked-selected reuses the nearest-tag inversion):
  //    the composite bookmark icon must keep its layering in BOTH themes.
  //    Real computed-style contrast evidence + screenshots — the badge used
  //    to collapse into one low-contrast blob (near-black glyph on the dark
  //    plate in dark, washed-out on the light plate in light).
  const highlightEvidence = (id) => page.evaluate((messageId) => {
    const element = document.querySelector(`[data-message-id="${messageId}"]`);
    if (!element) return null;
    const badge = element.querySelector('.message-navigation-marker-bookmark-badge');
    const parse = (value) => {
      if (!value) return null;
      let match = /rgba?\(([^)]+)\)/.exec(value);
      if (match) {
        const parts = match[1].split(',').map((part) => parseFloat(part));
        return { r: parts[0], g: parts[1], b: parts[2], a: parts[3] === undefined ? 1 : parts[3] };
      }
      // Chromium serialises resolved color-mix() values as color(srgb ...).
      match = /color\(srgb\s+([^)]+)\)/.exec(value);
      if (match) {
        const parts = match[1].split(/[\s/]+/).filter(Boolean).map((part) => parseFloat(part));
        return { r: parts[0] * 255, g: parts[1] * 255, b: parts[2] * 255, a: parts[3] === undefined ? 1 : parts[3] };
      }
      return null;
    };
    const luminance = ({ r, g, b }) => {
      const channel = (value) => {
        const scaled = value / 255;
        return scaled <= 0.03928 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
    };
    const contrast = (a, b) => {
      if (!a || !b) return null;
      const la = luminance(a);
      const lb = luminance(b);
      return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
    };
    const markerStyle = getComputedStyle(element);
    const badgeStyle = badge ? getComputedStyle(badge) : null;
    const markerBg = parse(markerStyle.backgroundColor);
    const markerFg = parse(markerStyle.color);
    const plate = badgeStyle ? parse(badgeStyle.backgroundColor) : null;
    const glyph = badgeStyle ? parse(badgeStyle.color) : null;
    return {
      highlighted: element.classList.contains('is-viewport-target'),
      kind: element.getAttribute('data-kind'),
      markerGlyphContrast: contrast(markerFg, markerBg),
      badgeGlyphContrast: contrast(glyph, plate),
      badgePlateContrast: contrast(plate, markerBg),
      colors: {
        markerFg: markerStyle.color, markerBg: markerStyle.backgroundColor,
        badgeGlyph: badgeStyle?.color ?? null, badgePlate: badgeStyle?.backgroundColor ?? null,
      },
    };
  }, id);

  const markerShot = async (id, name) => {
    const box = await marker(id).boundingBox();
    assert.ok(box, `marker ${id} has no box`);
    await page.screenshot({
      path: path.join(shotsDir, name),
      clip: { x: box.x - 10, y: box.y - 10, width: box.width + 20, height: box.height + 20 },
    });
  };

  for (const theme of ['dark', 'light']) {
    await page.evaluate((next) => { document.documentElement.dataset.theme = next; }, theme);
    // Clicked selection and nearest location share the same inverted state,
    // so one click per marker evidences both paths.  (Section 3 released
    // ID(1); re-mark it through the same real store overlay.)
    await page.evaluate((id) => window.__markBookmark(id, true), ID(1));
    await page.waitForFunction((id) =>
      document.querySelector(`[data-message-id="${id}"]`)?.getAttribute('data-bookmarked') === 'true',
    ID(1));
    await marker(ID(1)).click();
    await page.waitForFunction((id) =>
      document.querySelector(`[data-message-id="${id}"]`)?.classList.contains('is-viewport-target'),
    ID(1));
    await delay(250);
    const composite = await highlightEvidence(ID(1));
    check(`${theme}: highlighted composite icon body stays readable`,
      composite?.highlighted === true && composite.markerGlyphContrast >= 3,
      JSON.stringify(composite));
    check(`${theme}: highlighted bookmark badge keeps its layering (glyph on plate, plate on marker)`,
      composite?.badgeGlyphContrast >= 3 && composite.badgePlateContrast >= 1.5,
      JSON.stringify(composite));
    await page.screenshot({ path: path.join(shotsDir, `rail-highlight-composite-${theme}.png`) });
    await markerShot(ID(1), `rail-highlight-composite-marker-${theme}.png`);

    await marker(ID(3)).click();
    await page.waitForFunction((id) =>
      document.querySelector(`[data-message-id="${id}"]`)?.classList.contains('is-viewport-target'),
    ID(3));
    await delay(250);
    const bookmarkTag = await highlightEvidence(ID(3));
    check(`${theme}: highlighted standalone bookmark tag glyph stays readable`,
      bookmarkTag?.highlighted === true && bookmarkTag.markerGlyphContrast >= 3,
      JSON.stringify(bookmarkTag));
    await page.screenshot({ path: path.join(shotsDir, `rail-highlight-bookmark-tag-${theme}.png`) });
    await markerShot(ID(3), `rail-highlight-bookmark-tag-marker-${theme}.png`);
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });

  check('no page errors', pageErrors.length === 0, pageErrors.join('; '));
  check('no 8767/8768 traffic', panRequests.length === 0, panRequests.join('; '));
} finally {
  await browser?.close();
  await server.close();
}

if (failures.length) {
  console.error(`[bookmark-rail-e2e] ${failures.length} check(s) failed: ${failures.join(', ')}`);
  process.exit(1);
}
console.log('[bookmark-rail-e2e] all checks passed; screenshots in packages/web/test-results/message-bookmark-rail/');
