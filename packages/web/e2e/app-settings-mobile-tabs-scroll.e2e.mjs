/* global URL, fetch, document, innerWidth, console, process */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, unlinkSync, writeFileSync } from 'node:fs';
import net from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const port = 5213;
const harnessName = '.app-settings-mobile-tabs-scroll-harness';
const htmlPath = new URL(`../src/${harnessName}.html`, import.meta.url);
const tsxPath = new URL(`../src/${harnessName}.tsx`, import.meta.url);
const baseURL = `http://127.0.0.1:${port}/${harnessName}.html`;
const cwd = new URL('..', import.meta.url).pathname.replace(/^\/(\w:)/, '$1').replaceAll('/', '\\');
const mobileWidths = [320, 360, 390];
const desktopWidth = 1280;
const labels = ['General', 'Preferences', 'Appearance', 'Notification', 'Adapter'];

await new Promise((resolve, reject) => {
  const server = net.createServer();
  server.once('error', reject);
  server.listen(port, '127.0.0.1', () => server.close(resolve));
});

writeFileSync(htmlPath, `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1.0" /></head><body><div id="root"></div><script type="module" src="/${harnessName}.tsx"></script></body></html>`);
writeFileSync(tsxPath, `import { createRoot } from 'react-dom/client';\nimport { AppSettingsModal } from './components/layout/AppSettingsModal';\nimport './index.css';\ncreateRoot(document.getElementById('root')!).render(<AppSettingsModal open onClose={() => {}} />);`);

const server = spawn(process.execPath, [
  fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url)),
  '--host', '127.0.0.1', '--port', String(port), '--strictPort',
], {
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
  const page = await browser.newPage({
    viewport: { width: mobileWidths[0], height: 844 },
    deviceScaleFactor: 1,
    isMobile: false,
    hasTouch: true,
  });
  const pageErrors = [];
  const consoleErrors = [];
  const protectedRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('request', (request) => {
    if (/:(8767|8768)(\/|$)/.test(request.url())) protectedRequests.push(request.url());
  });
  await page.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path.endsWith('/models')
      ? { models: [], default: '' }
      : path.endsWith('/remote/status')
        ? { available: false, enabled: false, running: false }
        : { available: false, pending: false, platform: 'nt', stage: 'idle' };
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto(baseURL, { waitUntil: 'networkidle' });
  await page.getByRole('tab', { name: 'General' }).waitFor({ state: 'visible' });

  async function measure(width) {
    await page.setViewportSize({ width, height: 844 });
    await page.getByRole('tab', { name: 'General' }).waitFor({ state: 'visible' });
    return page.evaluate(() => {
      const rect = (element) => {
        const { x, width: w, left, right } = element.getBoundingClientRect();
        return { x, width: w, left, right };
      };
      const tabList = document.querySelector('[role="tablist"]');
      const card = document.querySelector('.app-settings-card');
      const panel = document.querySelector('[role="tabpanel"]');
      return {
        viewportWidth: innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        tabList: { ...rect(tabList), clientWidth: tabList.clientWidth, scrollWidth: tabList.scrollWidth, scrollLeft: tabList.scrollLeft },
        card: rect(card),
        panel: { ...rect(panel), clientWidth: panel.clientWidth, clientHeight: panel.clientHeight, scrollWidth: panel.scrollWidth, scrollHeight: panel.scrollHeight, scrollLeft: panel.scrollLeft },
      };
    });
  }

  for (const width of mobileWidths) {
    await page.reload({ waitUntil: 'networkidle' });
    await page.setViewportSize({ width, height: 844 });
    await page.evaluate(() => { document.querySelector('[role="tablist"]').scrollLeft = 0; });
    const before = await measure(width);
    assert.ok(before.tabList.scrollWidth > before.tabList.clientWidth, `${width}px tablist should overflow horizontally: ${JSON.stringify(before)}`);
    assert.ok(before.documentWidth <= width, `${width}px document overflowed: ${JSON.stringify(before)}`);
    assert.ok(before.card.width <= width, `${width}px card overflowed: ${JSON.stringify(before)}`);
    assert.ok(before.panel.scrollHeight > before.panel.clientHeight, `${width}px settings panel should scroll vertically on its own: ${JSON.stringify(before.panel)}`);

    const tabRects = await page.getByRole('tab').evaluateAll((tabs) => tabs.map((tab) => ({
      label: tab.textContent.trim(),
      width: tab.getBoundingClientRect().width,
      height: tab.getBoundingClientRect().height,
    })));
    assert.deepEqual(tabRects.map((tab) => tab.label), labels);
    assert.ok(tabRects.every((tab) => tab.width >= 70 && tab.height >= 36), `tabs collapsed or wrapped at ${width}px: ${JSON.stringify(tabRects)}`);

    const listBox = await page.locator('[role="tablist"]').boundingBox();
    const session = await page.context().newCDPSession(page);
    const touchX = listBox.x + listBox.width * 0.7;
    const touchY = listBox.y + listBox.height / 2;
    await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: touchX, y: touchY, id: 1, radiusX: 2, radiusY: 2, force: 1 }] });
    await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: touchX - 120, y: touchY, id: 1, radiusX: 2, radiusY: 2, force: 1 }] });
    await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: touchX - 240, y: touchY, id: 1, radiusX: 2, radiusY: 2, force: 1 }] });
    await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await session.detach();
    const afterGesture = await page.evaluate(() => ({
      scrollLeft: document.querySelector('[role="tablist"]').scrollLeft,
      panelScrollLeft: document.querySelector('[role="tabpanel"]').scrollLeft,
    }));
    assert.ok(afterGesture.scrollLeft > 0, `${width}px touch gesture did not move the tablist: ${JSON.stringify(afterGesture)}`);
    assert.equal(await page.getByRole('tab', { name: 'General' }).getAttribute('aria-selected'), 'true', 'horizontal scrolling must not activate a tab');
    assert.equal(afterGesture.panelScrollLeft, 0, 'content panel must not move horizontally with the tablist');

    await page.evaluate(() => {
      document.querySelector('[role="tablist"]').scrollLeft = 0;
      document.getElementById('app-settings-tab-general').focus();
    });
    await page.keyboard.press('End');
    await page.waitForTimeout(50);
    const endState = await page.evaluate(() => {
      const list = document.querySelector('[role="tablist"]');
      const tab = document.getElementById('app-settings-tab-adapter');
      const listRect = list.getBoundingClientRect();
      const tabRect = tab.getBoundingClientRect();
      return {
        selected: tab.getAttribute('aria-selected'),
        scrollLeft: list.scrollLeft,
        fullyVisible: tabRect.left >= listRect.left && tabRect.right <= listRect.right,
        panelScrollLeft: document.querySelector('[role="tabpanel"]').scrollLeft,
      };
    });
    assert.equal(endState.selected, 'true');
    assert.ok(endState.scrollLeft > 0, `${width}px End did not scroll to Adapter: ${JSON.stringify(endState)}`);
    assert.ok(endState.fullyVisible, `${width}px Adapter is clipped after End: ${JSON.stringify(endState)}`);
    assert.equal(endState.panelScrollLeft, 0);
    await page.keyboard.press('Home');
    await page.keyboard.press('ArrowLeft');
    await page.waitForTimeout(50);
    const wrappedLeft = await page.evaluate(() => {
      const list = document.querySelector('[role="tablist"]');
      const tab = document.getElementById('app-settings-tab-adapter');
      const bounds = list.getBoundingClientRect();
      const tabBounds = tab.getBoundingClientRect();
      return { selected: tab.getAttribute('aria-selected'), fullyVisible: tabBounds.left >= bounds.left && tabBounds.right <= bounds.right };
    });
    assert.equal(wrappedLeft.selected, 'true', `${width}px ArrowLeft did not wrap to Adapter`);
    assert.ok(wrappedLeft.fullyVisible, `${width}px ArrowLeft left Adapter clipped`);
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.getByRole('tab', { name: 'General' }).getAttribute('aria-selected'), 'true');
    await page.evaluate(() => { document.querySelector('[role="tablist"]').scrollLeft = 0; });
    await page.getByRole('tab', { name: 'Adapter' }).click();
    assert.equal(await page.getByRole('tab', { name: 'Adapter' }).getAttribute('aria-selected'), 'true');
    console.log(JSON.stringify({ width, before, touch: afterGesture, afterEnd: await measure(width) }));
  }

  await page.setViewportSize({ width: desktopWidth, height: 900 });
  const desktop = await measure(desktopWidth);
  assert.equal(desktop.tabList.scrollWidth, desktop.tabList.clientWidth, `desktop tablist should fit without scrolling: ${JSON.stringify(desktop)}`);
  assert.equal(desktop.tabList.scrollLeft, 0);
  assert.ok(desktop.documentWidth <= desktopWidth, `desktop document overflowed: ${JSON.stringify(desktop)}`);
  console.log(JSON.stringify({ width: desktopWidth, desktop }));

  assert.deepEqual(protectedRequests, [], `protected service ports were requested: ${protectedRequests.join(', ')}`);
  assert.deepEqual(pageErrors, [], `page errors: ${pageErrors.join('; ')}`);
  assert.deepEqual(consoleErrors, [], `console errors: ${consoleErrors.join('; ')}`);
} finally {
  await browser?.close();
  server.kill();
  for (const path of [htmlPath, tsxPath]) if (existsSync(path)) unlinkSync(path);
}
