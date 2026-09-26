/* global URL, fetch, window, getComputedStyle, process, console, document, innerWidth */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { spawn } from 'node:child_process';
import net from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';

const port = 5197;
const baseURL = `http://127.0.0.1:${port}/topbar-compact-harness.html`;
const cwd = new URL('..', import.meta.url).pathname.replace(/^\/(\w:)/, '$1').replaceAll('/', '\\');
const viewports = [320, 360, 390];

async function assertPortAvailable() {
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  });
}

await assertPortAvailable();
const viteArgs = ['exec', 'vite', '--host', '127.0.0.1', '--port', String(port), '--strictPort'];
const server = spawn(process.platform === 'win32' ? 'cmd.exe' : 'pnpm', process.platform === 'win32'
  ? ['/d', '/s', '/c', `pnpm ${viteArgs.join(' ')}`]
  : viteArgs, {
  cwd,
  windowsHide: true,
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
    const geometry = await page.evaluate(() => {
      const rect = (element) => {
        const { x, y, width: w, height: h, right, bottom } = element.getBoundingClientRect();
        return { x, y, width: w, height: h, right, bottom };
      };
      const bar = document.querySelector('[data-testid="topbar"]');
      const actions = document.querySelector('[data-testid="topbar-actions"]');
      const title = bar?.querySelector('.font-medium.truncate');
      const status = bar?.querySelector('span[title="running"], span[title="offline"]');
      const controls = [...(bar?.querySelectorAll('button') ?? [])].map((button) => ({
        title: button.title,
        label: button.getAttribute('aria-label'),
        rect: rect(button),
      }));
      return {
        viewport: { width: innerWidth, documentWidth: document.documentElement.scrollWidth },
        topbar: bar ? rect(bar) : null,
        status: status ? rect(status) : null,
        title: title ? { text: title.textContent, fullTitle: title.title, rect: rect(title) } : null,
        actions: actions ? rect(actions) : null,
        controls,
      };
    });
    assert.ok(geometry.topbar, 'TopBar missing');
    assert.ok(geometry.topbar.height <= 48, `TopBar too tall: ${JSON.stringify(geometry)}`);
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
      const existingTitleCap = width >= 768 ? 200 : 120;
      assert.ok(geometry.title.rect.width <= existingTitleCap, `Title exceeded its existing ${existingTitleCap}px cap: ${JSON.stringify(geometry.title)}`);
      assert.ok(geometry.actions && geometry.actions.y >= geometry.topbar.y && geometry.actions.bottom <= geometry.topbar.bottom);
      for (const expected of scenario === 'worker' ? ['Restart worker', 'Interrupt', 'Kill worker'] : []) {
        assert.ok(geometry.controls.some((control) => control.title === expected), `${expected} missing in ${scenario}`);
      }
    }
    if (scenario === 'offline') {
      assert.ok(geometry.controls.some(({ rect }) => rect.width > 0));
      assert.match(await page.getByRole('button', { name: 'Start', exact: true }).textContent(), /Start/);
    }
    if (scenario !== 'nav-off') {
      const navRect = geometry.controls.find(({ label }) => label === 'Open message navigation rail')?.rect;
      assert.ok(navRect, 'Navigation toggle missing');
      assert.equal(navRect.width, 32);
      assert.equal(navRect.height, 32);
      assert.ok(Math.abs(navRect.right - (width - (scenario === 'none' ? 16 : 12))) <= 1, `Navigation toggle is not rightmost: ${JSON.stringify(geometry)}`);
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
  }

  const desktop = await measure(1440, 'nav-off');
  assert.equal(desktop.topbar.height, 41, `Desktop TopBar height changed: ${JSON.stringify(desktop.topbar)}`);
  assert.equal(desktop.controls.find(({ title }) => title === 'Restart worker')?.rect.height, 24);
  assert.equal(desktop.controls.find(({ title }) => title === 'Interrupt')?.rect.height, 24);
  assert.equal(desktop.controls.find(({ title }) => title === 'Kill worker')?.rect.height, 24);

  await setScenario('worker');
  const nav = page.getByRole('button', { name: 'Open message navigation rail' });
  await nav.click();
  assert.equal(await page.getByRole('button', { name: 'Close message navigation rail' }).getAttribute('aria-expanded'), 'true');
  await page.locator('body').click({ position: { x: 5, y: 100 } });
  let navFocusedByKeyboard = false;
  for (let attempt = 0; attempt < 8; attempt += 1) {
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
  if (server.exitCode === null) {
    if (process.platform === 'win32') {
      try {
        execFileSync('taskkill.exe', ['/PID', String(server.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
      } catch {
        // The process may have exited between the state check and taskkill.
      }
    } else {
      server.kill('SIGTERM');
    }
    await Promise.race([new Promise((resolve) => server.once('exit', resolve)), delay(3000)]);
  }
}
