/* global URL, fetch, console, document, innerWidth, getComputedStyle */
import assert from 'node:assert/strict';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// Narrow-screen readability of the queue row-end lock/unlock button glyphs:
// Illegal (i + lock), Pause-reports report lock (Re + lock), manual lock,
// display-only Pause-everything P, and the plain Unlock icon.
// Standalone vite server (no app config/proxy): nothing reaches a Pan
// instance, and the isolated cacheDir never writes the shared node_modules.
const cwd = fileURLToPath(new URL('..', import.meta.url));
const port = 5199;
const baseURL = `http://127.0.0.1:${port}/queue-row-lock-icon-harness.html`;
const viewports = [320, 360, 390];
const shotDir = path.join(cwd, 'test-results/queue-row-lock-icon');

// The P glyph is session-scoped: with Pause everything on, every unlocked row
// (4 and 5) shows P; with the queue running they fall back to Unlock. Real
// locks (rows 1-3) never change with the pause flag.
const scenarios = [
  {
    name: 'paused',
    queuePaused: true,
    rows: [
      { label: 'Unlock queued message 1', pressed: 'true', glyph: 'i', fontSize: 13, svg: '8' },
      { label: 'Unlock queued message 2', pressed: 'true', glyph: 'Re', fontSize: 11, svg: '8' },
      { label: 'Unlock queued message 3', pressed: 'true', glyph: null, fontSize: null, svg: '12' },
      { label: 'Lock queued message 4', pressed: 'false', glyph: 'P', fontSize: 12, svg: null },
      { label: 'Lock queued message 5', pressed: 'false', glyph: 'P', fontSize: 12, svg: null },
    ],
  },
  {
    name: 'running',
    queuePaused: false,
    rows: [
      { label: 'Unlock queued message 1', pressed: 'true', glyph: 'i', fontSize: 13, svg: '8' },
      { label: 'Unlock queued message 2', pressed: 'true', glyph: 'Re', fontSize: 11, svg: '8' },
      { label: 'Unlock queued message 3', pressed: 'true', glyph: null, fontSize: null, svg: '12' },
      { label: 'Lock queued message 4', pressed: 'false', glyph: null, fontSize: null, svg: '12' },
      { label: 'Lock queued message 5', pressed: 'false', glyph: null, fontSize: null, svg: '12' },
    ],
  },
];

async function assertPortAvailable() {
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  });
}

await assertPortAvailable();
fs.mkdirSync(shotDir, { recursive: true });
const server = await createServer({
  configFile: false,
  root: path.join(cwd, 'src'),
  cacheDir: path.join(shotDir, 'vite-cache'),
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
      if (response.ok) { ready = true; break; }
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
  // The harness projects queue state through the stores; the queue GET is
  // fulfilled with a benign error so the panel keeps the fixture snapshot
  // without any console noise.
  await page.route('**/api/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true, error: { code: 'harness_static', message: 'static harness fixture' } }),
    });
  });
  await page.goto(baseURL, { waitUntil: 'networkidle' });
  await page.waitForFunction(
    () => document.querySelectorAll('.queue-row-in button[aria-pressed]').length === 5,
    null,
    { timeout: 15000 },
  );

  for (const width of viewports) {
    for (const scenario of scenarios) {
    await page.setViewportSize({ width, height: 844 });
    await page.evaluate((paused) => window.__setQueuePaused(paused), scenario.queuePaused);
    const geometry = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('.queue-row-in')];
      return {
        viewport: { width: innerWidth, documentWidth: document.documentElement.scrollWidth },
        rows: rows.map((row) => {
          const button = row.querySelector('button[aria-pressed]');
          const rect = button.getBoundingClientRect();
          const glyphOuter = button.querySelector(':scope > span[aria-hidden="true"]');
          const glyph = glyphOuter?.querySelector('span') ?? glyphOuter;
          const glyphRect = glyph?.getBoundingClientRect();
          const svg = button.querySelector('svg');
          return {
            label: button.getAttribute('aria-label'),
            pressed: button.getAttribute('aria-pressed'),
            title: button.title,
            rect: { x: rect.x, width: rect.width, height: rect.height, right: rect.right, bottom: rect.bottom },
            glyphText: glyph?.textContent ?? null,
            glyphFontSize: glyph ? parseFloat(getComputedStyle(glyph).fontSize) : null,
            glyphWidth: glyphRect ? glyphRect.width : null,
            svgWidth: svg?.getAttribute('width') ?? null,
          };
        }),
      };
    });

    assert.equal(geometry.rows.length, 5, `Expected 5 queue rows, got ${geometry.rows.length}`);
    assert.equal(geometry.viewport.documentWidth, width, `Horizontal overflow at ${width}px: ${JSON.stringify(geometry)}`);
    geometry.rows.forEach((row, index) => {
      const expected = scenario.rows[index];
      const at = `${width}px ${scenario.name} row ${index + 1}`;
      assert.equal(row.label, expected.label, `aria-label mismatch at ${at}`);
      assert.equal(row.pressed, expected.pressed, `aria-pressed mismatch at ${at}: ${JSON.stringify(row)}`);
      assert.equal(row.glyphText, expected.glyph, `glyph mismatch at ${at}: ${JSON.stringify(row)}`);
      assert.equal(row.glyphFontSize, expected.fontSize, `glyph font-size mismatch at ${at}: ${JSON.stringify(row)}`);
      assert.equal(row.svgWidth, expected.svg, `overlay lock svg mismatch at ${at}: ${JSON.stringify(row)}`);
      // The button stays a real tap target and never leaves the viewport.
      assert.ok(row.rect.width >= 20 && row.rect.height >= 20, `Button below 20px at ${at}: ${JSON.stringify(row)}`);
      assert.ok(row.rect.width <= 36, `Button too wide for narrow rows at ${at}: ${JSON.stringify(row)}`);
      assert.ok(row.rect.x >= 0 && row.rect.right <= width, `Button outside viewport at ${at}: ${JSON.stringify(row)}`);
      if (expected.glyph && expected.glyph.length > 1) {
        // Multi-letter glyphs (Re) must not be clipped on narrow rows; a lone
        // italic "i" is naturally ~3.5px wide and is the pre-existing Illegal
        // treatment, so only font size gates its readability.
        assert.ok(row.glyphWidth >= 9, `Glyph "${expected.glyph}" too small to read at ${at}: ${JSON.stringify(row)}`);
      }
    });
    // Lock semantics stay untouched: the Illegal row keeps its explanation
    // title, and every unlocked row (P glyph or not) keeps the Lock affordance.
    assert.ok(
      geometry.rows[0].title.includes('Worker entered Illegal status'),
      `Illegal row lost its explanation title: ${JSON.stringify(geometry.rows[0])}`,
    );
    assert.ok(
      geometry.rows.slice(3).every((row) => row.title === 'Lock'),
      `Unlocked rows must still offer Lock: ${JSON.stringify(geometry.rows.slice(3))}`,
    );

    await page.screenshot({ path: path.join(shotDir, `${width}-${scenario.name}.png`) });
    console.log(`${width}px ${scenario.name}: ${JSON.stringify(geometry.rows.map((row) => ({
      label: row.label, glyph: row.glyphText, fontSize: row.glyphFontSize, svg: row.svgWidth,
      width: Math.round(row.rect.width * 10) / 10, height: Math.round(row.rect.height * 10) / 10,
    })))}`);
    }
  }

  assert.deepEqual(pageErrors, [], `Page errors: ${pageErrors.join('; ')}`);
  assert.deepEqual(consoleErrors, [], `Console errors: ${consoleErrors.join('; ')}`);
  assert.deepEqual(failedRequests, [], `Failed requests: ${failedRequests.join('; ')}`);
  assert.deepEqual(protectedRequests, [], `Requests reached protected ports: ${protectedRequests.join('; ')}`);
  console.log(`queue-row-lock-icon harness OK; screenshots in ${shotDir}`);
} finally {
  await browser?.close();
  await server.close();
}
