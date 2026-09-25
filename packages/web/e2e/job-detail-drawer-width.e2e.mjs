/* global URL, process, fetch, document, innerWidth, innerHeight, console */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { spawn } from 'node:child_process';
import net from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';

const port = 5189;
const baseURL = `http://127.0.0.1:${port}/job-detail-drawer-width.html`;
const cwd = new URL('..', import.meta.url).pathname.replace(/^\/(\w:)/, '$1').replaceAll('/', '\\');

async function assertPortAvailable() {
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  });
}

await assertPortAvailable();
const server = spawn('pnpm', ['exec', 'vite', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
  cwd,
  shell: process.platform === 'win32',
  stdio: 'ignore',
});
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
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const pageErrors = [];
  const protectedRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => {
    if (/:(8767|8768)(\/|$)/.test(request.url())) protectedRequests.push(request.url());
  });
  await page.route('**/api/**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
  await page.goto(baseURL, { waitUntil: 'networkidle' });

  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    const dialog = page.getByRole('dialog', { name: 'Job detail' });
    const close = page.getByRole('button', { name: 'Close' });
    await dialog.waitFor({ state: 'visible' });
    const geometry = await page.evaluate(() => {
      const dialogEl = document.querySelector('[role="dialog"]');
      const closeEl = document.querySelector('button[aria-label="Close"]');
      if (!dialogEl || !closeEl) throw new Error('Drawer or Close control missing');
      const rect = (el) => {
        const { x, y, width, height, right, bottom } = el.getBoundingClientRect();
        return { x, y, width, height, right, bottom };
      };
      return { dialog: rect(dialogEl), close: rect(closeEl), viewport: { width: innerWidth, height: innerHeight } };
    });

    if (viewport.width === 1440) {
      assert.ok(Math.abs(geometry.dialog.width - 448) <= 1, `desktop drawer width=${geometry.dialog.width}, expected 448`);
    } else {
      assert.ok(Math.abs(geometry.dialog.width - viewport.width) <= 0.5, `mobile drawer width=${geometry.dialog.width}, expected ${viewport.width}`);
    }
    assert.ok(geometry.close.x >= 0 && geometry.close.right <= viewport.width, `Close button outside viewport: ${JSON.stringify(geometry.close)}`);
    assert.ok(geometry.close.y >= 0 && geometry.close.bottom <= viewport.height, `Close button outside viewport vertically: ${JSON.stringify(geometry.close)}`);
    console.log(`${viewport.width}x${viewport.height}: ${JSON.stringify(geometry)}`);
    assert.equal(await close.count(), 1);
  }

  assert.deepEqual(pageErrors, [], `Browser errors: ${pageErrors.join('; ')}`);
  assert.deepEqual(protectedRequests, [], `Unexpected protected-port requests: ${protectedRequests.join(', ')}`);
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
