/* global performance, requestAnimationFrame, process, console, fetch */
// Production-bundle cold-entry fixture. Only an owned Vite preview and demo
// data are used; no Pan service, provider, or persisted Session is accessed.
// PAN_COLD_DIST selects a baseline build. PAN_COLD_CHECK=1 enables asset gates.
import assert from 'node:assert/strict';
import net from 'node:net';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { chromium } from '@playwright/test';

const root = path.resolve(import.meta.dirname, '..');
const port = 5185;
const base = `http://127.0.0.1:${port}/react/`;
const check = process.env.PAN_COLD_CHECK === '1';
const samples = Number(process.env.PAN_COLD_SAMPLES || 5);
assert.ok(Number.isInteger(samples) && samples >= 1 && samples <= 20);
let server;
let browser;
const results = [];
try {
  await new Promise((resolve, reject) => {
    const socket = net.createServer();
    socket.once('error', reject);
    socket.listen(port, '127.0.0.1', () => socket.close(resolve));
  });
  server = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'),
    'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort',
    ...(process.env.PAN_COLD_DIST ? ['--outDir', path.resolve(process.env.PAN_COLD_DIST)] : [])],
  { cwd: root, windowsHide: true, stdio: 'ignore' });
  let ready = false;
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(`Preview exited ${server.exitCode}`);
    try { ready = (await fetch(base)).ok; } catch { /* starting */ }
    if (ready) break;
    await sleep(100);
  }
  assert.ok(ready);
  browser = await chromium.launch({ headless: true });
  for (const route of ['', 'jobs', 'editor']) {
    for (let sample = 0; sample < samples; sample++) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      // CPU throttling makes parsing contention observable, without inventing
      // a backend latency. Each sample has an empty browser cache/storage.
      const cdp = await context.newCDPSession(page);
      await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
      await page.goto(`${base}${route}?mock=1&panE2E=1`, { waitUntil: 'domcontentloaded' });
      if (route === 'editor') await page.getByRole('link', { name: 'Editor', exact: true }).waitFor();
      else await page.locator('[data-session-card-id]').first().waitFor();
      const sidebarVisibleMs = await page.evaluate(() => performance.now());
      if (route === 'jobs') await page.getByRole('heading', { name: 'Jobs', exact: true }).waitFor();
      else if (route === 'editor') await page.locator('main').getByText('Select a session from the sidebar to browse its working directory', { exact: true }).waitFor();
      else await page.locator('.chat-view-stage').waitFor();
      const routeVisibleMs = await page.evaluate(() => new Promise(resolve =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve(performance.now())))));
      // Collect late dynamic imports as well as requests before first paint.
      await page.waitForLoadState('networkidle');
      const assets = await page.evaluate(() => performance.getEntriesByType('resource')
        .filter(entry => entry.name.includes('/assets/') && /\.js$/.test(entry.name))
        .map(entry => ({ file: entry.name.split('/').pop(), bytes: entry.decodedBodySize })));
      await cdp.send('HeapProfiler.collectGarbage');
      const heap = await cdp.send('Runtime.getHeapUsage');
      if (check) {
        assert.ok(!assets.some(asset => /^DetailPanel-/.test(asset.file)), 'closed detail does not import its chunk');
        if (route === '') assert.ok(!assets.some(asset => /^EditorDirectoryRoots-/.test(asset.file)),
          'chat does not import the editor directory tree');
        if (route === 'editor') assert.ok(!assets.some(asset => /^(CodeEditor|monaco-vendor)-/.test(asset.file)),
          'empty editor does not import the code editor');
        if (route !== '') {
          assert.ok(!assets.some(asset => /^(ChatView|MarkdownRenderer|markdown-vendor)-/.test(asset.file)),
            `${route}: invisible chat and markdown graph stays unloaded`);
          // Block chat assets indefinitely, then refresh this non-chat route:
          // its visible content must still arrive without those dependencies.
          await page.route('**/assets/ChatView-*.js', () => new Promise(() => {}));
          await page.reload({ waitUntil: 'domcontentloaded' });
          if (route === 'jobs') await page.getByRole('heading', { name: 'Jobs', exact: true }).waitFor({ timeout: 5000 });
          else await page.locator('main').getByText('Select a session from the sidebar to browse its working directory', { exact: true }).waitFor({ timeout: 5000 });
          await page.unroute('**/assets/ChatView-*.js');
          await page.getByRole('link', { name: 'Chat', exact: true }).click();
          await page.locator('.chat-view-stage').waitFor();
          await page.locator('[data-session-card-id]').first().click();
          await page.locator('[contenteditable="true"]').first().waitFor();
          assert.ok(await page.locator('main').innerText(), 'chat is usable after first deferred route entry');
        }
      }
      assert.deepEqual(errors, []);
      results.push({ route: route || 'chat', sample, sidebarVisibleMs, routeVisibleMs,
        jsBytes: assets.reduce((total, asset) => total + asset.bytes, 0),
        heapBytesAfterGc: heap.usedSize, assets });
      await context.close();
    }
  }
  console.log(JSON.stringify({ cpuRate: 4, samples, results }, null, 2));
} finally {
  await browser?.close();
  if (server?.pid && server.exitCode === null) {
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    else server.kill();
  }
}
