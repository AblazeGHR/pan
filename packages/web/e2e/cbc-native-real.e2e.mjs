/* global console */
// Opt-in probe helper: native API traffic reaches the dedicated probe server.
// Other application routes are presentation fixtures, never production Pan.
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { chromium } from '@playwright/test';

const [backendUrl, sessionId] = process.argv.slice(2);
const backend = new URL(backendUrl);
assert.equal(backend.hostname, '127.0.0.1');
assert.ok(!['8767', '8768', ''].includes(backend.port));
assert.ok(sessionId.startsWith('ses-cbc-proof-'));
const cwd = fileURLToPath(new URL('..', import.meta.url));
const server = await createServer({ configFile: false, root: path.join(cwd, 'src'),
  cacheDir: path.join(cwd, 'test-results/cbc-native-real/vite-cache'),
  resolve: { alias: { '@': path.join(cwd, 'src') } }, plugins: [react(), tailwindcss()],
  server: { host: '127.0.0.1', port: 0, strictPort: true, proxy: {} } });
let browser;
try {
  await server.listen();
  const port = server.httpServer.address().port;
  assert.ok(![8767, 8768].includes(port));
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const errors = [];
  const switches = [];
  const protocolInput = [];
  let nativeRequests = 0;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.includes('/native-control')) {
      nativeRequests++;
      const actual = backendUrl + url.pathname.replace('/native-browser/', `/${sessionId}/`) + url.search;
      const response = await route.fetch({ url: actual });
      if (url.pathname.endsWith('/write')) {
        const text = request.postDataJSON().text;
        if (typeof text === 'string') protocolInput.push(text);
      }
      if (url.pathname.endsWith('/mode')) {
        assert.equal(response.status(), 200, JSON.stringify({ response: await response.text(), protocolInput }));
        switches.push(request.postDataJSON().mode);
      }
      await route.fulfill({ response });
      return;
    }
    const result = url.pathname.endsWith('/queue')
      ? { items: [], queueRevision: 0, paused: false } : {};
    await route.fulfill({ json: result });
  });
  await page.goto(`http://127.0.0.1:${port}/native-control-harness.html?adapter=cbc`);
  await page.getByRole('button', { name: 'Terminal TUI', exact: true }).click();
  await page.getByTestId('native-terminal').waitFor({ state: 'visible' });
  await page.locator('.xterm-helper-textarea').waitFor({ state: 'attached' });
  await page.waitForTimeout(800);
  const beforeRefresh = switches.length;
  await page.reload();
  await page.getByTestId('native-terminal').waitFor({ state: 'visible' });
  assert.equal(switches.length, beforeRefresh);
  await page.getByRole('button', { name: 'Switch to Bubble view' }).click();
  await page.getByTestId('native-terminal').waitFor({ state: 'detached' });
  await page.locator('[contenteditable="true"][data-placeholder]').waitFor({ state: 'visible' });
  assert.deepEqual(switches, ['native', 'pan']);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, browser: 'Chromium', native_api: 'real isolated HTTP',
    agent: 'real hy3 CBC TUI', presentation_routes: 'fixture', switches,
    refresh_keeps_owner: true, nativeRequests, page_errors: errors }));
} finally {
  if (browser) await browser.close();
  await server.close();
}
