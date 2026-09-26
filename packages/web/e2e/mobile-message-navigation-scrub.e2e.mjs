/* global URL, process, fetch, console, document, window, innerWidth, innerHeight */
import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import net from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';

const port = 5198;
const baseURL = `http://127.0.0.1:${port}/mobile-message-navigation-harness.html`;
const cwd = new URL('..', import.meta.url).pathname.replace(/^\/(\w:)/, '$1').replaceAll('/', '\\');
const fixtureMessages = Array.from({ length: 48 }, (_, index) => ({
  role: 'user',
  content: `Chromium scrub preview ${String(index + 1).padStart(2, '0')} ${'preview detail '.repeat(4)}`,
}));

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
  const protectedRequests = [];
  const reports = [];
  for (const width of [320, 390]) {
    const context = await browser.newContext({
      viewport: { width, height: 844 },
      isMobile: true,
      hasTouch: true,
      deviceScaleFactor: 1,
    });
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('request', (request) => {
      if (/:(8767|8768)(\/|$)/.test(request.url())) protectedRequests.push(request.url());
    });
    await context.route('**/api/**', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        history: fixtureMessages,
        total: fixtureMessages.length,
        start: 0,
        hasMore: false,
      }),
    }));
    await page.goto(baseURL, { waitUntil: 'networkidle' });
    await page.locator('.message-navigation-marker').first().waitFor({ state: 'visible' });
    await page.waitForFunction(() => document.querySelector('.message-navigation-rail')?.getAttribute('data-index-status') === 'ready');
    const cdp = await context.newCDPSession(page);
    await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true, configuration: 'mobile' });
    const list = page.locator('.message-navigation-list');
    const initial = await list.evaluate((element) => ({
      scrollHeight: element.scrollHeight,
      clientHeight: element.clientHeight,
      scrollTop: element.scrollTop,
    }));
    assert.ok(initial.scrollHeight > initial.clientHeight, `${width}px rail did not overflow: ${JSON.stringify(initial)}`);

    const touch = async (type, x, y) => cdp.send('Input.dispatchTouchEvent', {
      type,
      touchPoints: type === 'touchEnd' ? [] : [{ x, y, id: 1 }],
    });
    const bounds = async (index) => page.locator('.message-navigation-marker').nth(index).boundingBox();

    // Ordinary movement starts before the long-press threshold and must scroll the marker list.
    const scrollStartMarker = await bounds(4);
    assert.ok(scrollStartMarker);
    const scrollStart = {
      x: scrollStartMarker.x + scrollStartMarker.width / 2,
      y: scrollStartMarker.y + scrollStartMarker.height / 2,
    };
    await touch('touchStart', scrollStart.x, scrollStart.y);
    await delay(70);
    await touch('touchMove', scrollStart.x, scrollStart.y - 120);
    await delay(90);
    const scrolled = await list.evaluate((element) => element.scrollTop);
    await touch('touchEnd', scrollStart.x, scrollStart.y - 120);
    assert.ok(scrolled > 24, `${width}px pre-long-press drag did not scroll: ${scrolled}`);
    await delay(240);
    await list.evaluate((element) => { element.scrollTop = 0; });

    // Hold the first marker, then move over two further markers while checking preview and scroll state.
    const first = await bounds(0);
    const second = await bounds(1);
    const third = await bounds(2);
    assert.ok(first && second && third);
    const point = (rect) => ({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 });
    const firstPoint = point(first);
    const secondPoint = point(second);
    const thirdPoint = point(third);
    const markerData = await page.locator('.message-navigation-marker').evaluateAll((elements) =>
      elements.slice(0, 3).map((element) => ({ fromEnd: element.dataset.fromEnd, title: element.title })),
    );

    await touch('touchStart', firstPoint.x, firstPoint.y);
    await delay(520);
    const tooltip = page.locator('[role="tooltip"]');
    await tooltip.waitFor({ state: 'visible' });
    assert.equal(await tooltip.getAttribute('data-preview-mode'), 'scrub');
    assert.equal(await tooltip.getAttribute('data-preview-from-end'), markerData[0].fromEnd);

    await touch('touchMove', secondPoint.x, secondPoint.y);
    await delay(50);
    assert.equal(await tooltip.getAttribute('data-preview-from-end'), markerData[1].fromEnd);
    assert.ok((await tooltip.textContent()).includes(markerData[1].title));

    await touch('touchMove', thirdPoint.x, thirdPoint.y);
    await delay(50);
    assert.equal(await tooltip.getAttribute('data-preview-from-end'), markerData[2].fromEnd);
    assert.ok((await tooltip.textContent()).includes(markerData[2].title));
    const geometry = await page.evaluate(() => {
      const tooltipElement = document.querySelector('[role="tooltip"]');
      const railMarker = document.querySelectorAll('.message-navigation-marker')[2];
      const rect = (element) => {
        const { x, y, width: w, height: h, right, bottom } = element.getBoundingClientRect();
        return { x, y, width: w, height: h, right, bottom };
      };
      return {
        viewport: { width: innerWidth, height: innerHeight, documentWidth: document.documentElement.scrollWidth },
        pageScrollY: window.scrollY,
        tooltip: rect(tooltipElement),
        marker: rect(railMarker),
        dockExpanded: document.querySelector('.message-navigation-dock')?.getAttribute('data-expanded'),
        list: {
          scrollTop: document.querySelector('.message-navigation-list').scrollTop,
          scrollHeight: document.querySelector('.message-navigation-list').scrollHeight,
          clientHeight: document.querySelector('.message-navigation-list').clientHeight,
        },
      };
    });
    assert.equal(geometry.viewport.documentWidth, width, `${width}px rail introduced horizontal page overflow`);
    assert.ok(geometry.tooltip.x >= 0 && geometry.tooltip.right < geometry.marker.x, `${width}px preview overlaps the finger side: ${JSON.stringify(geometry)}`);
    assert.ok(geometry.tooltip.y >= 0 && geometry.tooltip.bottom <= geometry.viewport.height, `${width}px preview is clipped: ${JSON.stringify(geometry)}`);
    assert.equal(geometry.pageScrollY, 0, `${width}px scrub scrolled the page`);
    assert.equal(geometry.dockExpanded, 'true', `${width}px scrub closed the rail`);
    assert.equal(geometry.list.scrollTop, 0, `${width}px scrub moved the rail: ${JSON.stringify(geometry.list)}`);

    await touch('touchEnd', thirdPoint.x, thirdPoint.y);
    await delay(120);
    assert.equal(await page.locator('[role="tooltip"]').count(), 0, `${width}px release left the preview open`);
    const actions = await page.evaluate(() => ({ jumps: window.__scrubJumpCalls, scrolls: window.__scrubScrollCalls }));
    assert.deepEqual(actions, { jumps: 0, scrolls: 0 }, `${width}px scrub triggered a message jump`);
    assert.deepEqual(pageErrors, [], `${width}px browser errors: ${pageErrors.join('; ')}`);
    reports.push({ width, initial, ordinaryScrollTop: scrolled, geometry, actions });
    await context.close();
  }

  assert.deepEqual(protectedRequests, [], `Unexpected protected-port requests: ${protectedRequests.join(', ')}`);
  console.log(JSON.stringify({ port, protectedRequests, reports }, null, 2));
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
