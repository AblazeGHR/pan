/* global URL, document, innerWidth, console */
import assert from 'node:assert/strict';
import { build, preview } from 'vite';
import { existsSync, unlinkSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const harnessName = '.settings-search-tab-harness';
const htmlPath = new URL(`../src/${harnessName}.html`, import.meta.url);
const tsxPath = new URL(`../src/${harnessName}.tsx`, import.meta.url);
let baseURL;
const mobileWidths = [320, 360, 390];
const desktopWidth = 1600;
const labels = ['Lifecycle', 'Behavior', 'Remote', 'Appearance', 'Notifications', 'Shortcuts', 'Adapters', 'Plugins', 'Data', 'Authorization', 'Search'];
const evidence = new URL('../.tmp/settings-search-tab/', import.meta.url);
mkdirSync(evidence, {recursive: true});
const measurements = [];

writeFileSync(htmlPath, `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1.0" /></head><body><div id="root"></div><script type="module" src="/${harnessName}.tsx"></script></body></html>`);
writeFileSync(tsxPath, `import { createRoot } from 'react-dom/client';\nimport { AppSettingsModal } from './components/layout/AppSettingsModal';\nimport './index.css';\ncreateRoot(document.getElementById('root')!).render(<AppSettingsModal open onClose={() => {}} />);`);

await build({
  configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
  base: '/',
  build: {outDir: fileURLToPath(new URL('../.tmp/settings-search-build', import.meta.url)), emptyOutDir: true,
    rollupOptions: {input: fileURLToPath(htmlPath)}},
});
const server = await preview({configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)), base: '/',
  build: {outDir: fileURLToPath(new URL('../.tmp/settings-search-build', import.meta.url))},
  preview: {host: '127.0.0.1', port: 0, strictPort: true}});
const address = server.httpServer.address();
assert.ok(typeof address === 'object' && ![8767,8768].includes(address.port));
baseURL = `http://127.0.0.1:${address.port}/${harnessName}.html`;
let browser;

try {
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
    const retentionPolicies = {
      sessions: { enabled: false, days: null },
      attachments: { enabled: false, days: null },
      qq_history: { enabled: false, days: null },
      qq_media: { enabled: false, days: null },
      pan_logs: { enabled: false, days: null },
    };
    const lastScans = Object.fromEntries(Object.keys(retentionPolicies).map((key) => [key, {
      scanned: 0, deleted: 0, skipped: 0, skipReasons: {}, lastScanAt: null,
    }]));
    const jobRules = {
      completed: { enabled: false, days: null },
      failed: { enabled: false, days: null },
      timed_out: { enabled: false, days: null },
      cancelled: { enabled: false, days: null },
      logs: { enabled: false, days: null },
    };
    let body;
    if (path.endsWith('/models')) body = { models: [], default: '' };
    else if (path.endsWith('/remote/status')) body = { available: false, enabled: false, running: false };
    else if (path.endsWith('/settings/session-lifecycle')) {
      body = { exitStrategy: 'ask', startupPreference: 'ask' };
    } else if (path.endsWith('/data/catalog')) {
      body = {
        categories: [],
        notice: '',
        jobsRetention: { slot: 'jobs-retention-control', status: 'reserved', message: '' },
      };
    } else if (path.endsWith('/settings/data-retention')) {
      body = { policies: retentionPolicies, configKey: 'data_retention', lastScans };
    } else if (path.endsWith('/jobs/settings/completed-retention')) {
      body = {
        settings: jobRules.completed,
        rules: jobRules,
        configValid: true,
        configValidity: Object.fromEntries(Object.keys(jobRules).map((key) => [key, true])),
        lastRun: null,
        lastRuns: Object.fromEntries(Object.keys(jobRules).map((key) => [key, null])),
      };
    } else body = { available: false, pending: false, platform: 'nt', stage: 'idle' };
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto(baseURL, { waitUntil: 'networkidle' });
  await page.getByRole('tab', { name: 'Lifecycle' }).waitFor({ state: 'visible' });

  async function measure(width) {
    await page.setViewportSize({ width, height: 844 });
    await page.getByRole('tab', { name: 'Lifecycle' }).waitFor({ state: 'visible' });
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
    // Lifecycle is intentionally short; measure the independent panel
    // vertical scroll on Appearance, then return to Lifecycle for the tab
    // scroll assertions below.
    await page.getByRole('tab', { name: 'Appearance' }).click();
    const before = await measure(width);
    assert.ok(before.tabList.scrollWidth > before.tabList.clientWidth, `${width}px tablist should overflow horizontally: ${JSON.stringify(before)}`);
    assert.ok(before.documentWidth <= width, `${width}px document overflowed: ${JSON.stringify(before)}`);
    assert.ok(before.card.width <= width, `${width}px card overflowed: ${JSON.stringify(before)}`);
    assert.ok(before.panel.scrollHeight > before.panel.clientHeight, `${width}px settings panel should scroll vertically on its own: ${JSON.stringify(before.panel)}`);
    await page.getByRole('tab', { name: 'Lifecycle' }).click();
    await page.evaluate(() => { document.querySelector('[role="tablist"]').scrollLeft = 0; });

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
    assert.equal(await page.getByRole('tab', { name: 'Lifecycle' }).getAttribute('aria-selected'), 'true', 'horizontal scrolling must not activate a tab');
    assert.equal(afterGesture.panelScrollLeft, 0, 'content panel must not move horizontally with the tablist');

    await page.evaluate(() => {
      document.querySelector('[role="tablist"]').scrollLeft = 0;
      document.getElementById('app-settings-tab-lifecycle').focus();
    });
    await page.keyboard.press('End');
    await page.waitForTimeout(50);
    const endState = await page.evaluate(() => {
      const list = document.querySelector('[role="tablist"]');
      const tab = document.getElementById('app-settings-tab-search');
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
    assert.ok(endState.scrollLeft > 0, `${width}px End did not scroll to Search: ${JSON.stringify(endState)}`);
    assert.ok(endState.fullyVisible, `${width}px Search is clipped after End: ${JSON.stringify(endState)}`);
    assert.equal(endState.panelScrollLeft, 0);
    await page.keyboard.press('Home');
    await page.keyboard.press('ArrowLeft');
    await page.waitForTimeout(50);
    const wrappedLeft = await page.evaluate(() => {
      const list = document.querySelector('[role="tablist"]');
      const tab = document.getElementById('app-settings-tab-search');
      const bounds = list.getBoundingClientRect();
      const tabBounds = tab.getBoundingClientRect();
      return { selected: tab.getAttribute('aria-selected'), fullyVisible: tabBounds.left >= bounds.left && tabBounds.right <= bounds.right };
    });
    assert.equal(wrappedLeft.selected, 'true', `${width}px ArrowLeft did not wrap to Search`);
    assert.ok(wrappedLeft.fullyVisible, `${width}px ArrowLeft left Search clipped`);
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.getByRole('tab', { name: 'Lifecycle' }).getAttribute('aria-selected'), 'true');
    await page.evaluate(() => { document.querySelector('[role="tablist"]').scrollLeft = 0; });
    await page.getByRole('tab', { name: 'Data' }).click();
    assert.equal(await page.getByRole('tab', { name: 'Data' }).getAttribute('aria-selected'), 'true');
    console.log(JSON.stringify({ width, before, touch: afterGesture, afterEnd: await measure(width) }));
  }

  await page.setViewportSize({ width: desktopWidth, height: 900 });
  const desktop = await measure(desktopWidth);
  assert.equal(desktop.tabList.scrollWidth, desktop.tabList.clientWidth, `desktop tablist should fit without scrolling: ${JSON.stringify(desktop)}`);
  assert.equal(desktop.tabList.scrollLeft, 0);
  assert.ok(desktop.documentWidth <= desktopWidth, `desktop document overflowed: ${JSON.stringify(desktop)}`);
  console.log(JSON.stringify({ width: desktopWidth, desktop }));

  for (const width of [320,390,1280]) {
    await page.setViewportSize({width, height:900});
    await page.getByRole('tab', {name:'Lifecycle', exact:true}).click();
    assert.equal(await page.getByRole('searchbox', {name:'Search settings',exact:true}).count(), 0);
    const layout = await page.locator('[role="tablist"]').evaluate(el => ({
      scrollbarWidth: getComputedStyle(el).scrollbarWidth,
      webkitScrollbar: getComputedStyle(el,'::-webkit-scrollbar').display,
      siblingInputs: el.parentElement.querySelectorAll('input').length,
    }));
    assert.equal(layout.scrollbarWidth, 'none');
    assert.equal(layout.webkitScrollbar, 'none');
    assert.equal(layout.siblingInputs, 0);
    await page.screenshot({path:fileURLToPath(new URL(`tabs-${width}.png`,evidence))});
    await page.getByRole('tab', {name:'Search',exact:true}).click();
    await page.getByRole('searchbox',{name:'Search settings',exact:true}).fill('sidebar');
    await page.locator('[data-testid="settings-search-hit-sidebar"]').waitFor();
    await page.screenshot({path:fileURLToPath(new URL(`search-${width}.png`,evidence))});
    await page.locator('[data-testid="settings-search-hit-sidebar"]').click();
    assert.equal(await page.getByRole('tab',{name:'Appearance',exact:true}).getAttribute('aria-selected'),'true');
    assert.equal(await page.getByRole('searchbox',{name:'Search settings',exact:true}).count(),0);
    assert.ok((await measure(width)).documentWidth <= width);
    measurements.push({width,...layout,searchJump:true});
  }
  writeFileSync(new URL('evidence.json',evidence),JSON.stringify({status:'passed',measurements,pageErrors,protectedRequests},null,2));
  assert.deepEqual(protectedRequests, [], `protected service ports were requested: ${protectedRequests.join(', ')}`);
  assert.deepEqual(pageErrors, [], `page errors: ${pageErrors.join('; ')}`);
  assert.deepEqual(consoleErrors, [], `console errors: ${consoleErrors.join('; ')}`);
} finally {
  await browser?.close();
  await new Promise(resolve => server.httpServer.close(resolve));
  for (const path of [htmlPath, tsxPath]) if (existsSync(path)) unlinkSync(path);
}
