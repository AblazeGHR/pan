/* global process, URL, window, document, getComputedStyle, console */
// Isolated Chromium check for the merged NonBodyGroup child virtualizer.
// It serves one Vite development middleware page on a loopback port and uses
// synthetic messages only; no Pan service, API, WebSocket, or history is used.
import assert from 'node:assert/strict';
import http from 'node:http';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = path.resolve(HERE, '..');
const HARNESS = path.resolve(HERE, 'nonbody-nested-virtualization-harness.tsx');
const PORT = Number(process.env.PAN_NONBODY_VIRTUALIZATION_PORT || 8799);
const BASE = `http://127.0.0.1:${PORT}`;

assert.ok(![8767, 8768].includes(PORT), 'refusing to use a protected Pan port');

const vite = await createServer({
  configFile: path.join(WEB_ROOT, 'vite.config.ts'),
  server: {
    middlewareMode: true,
    hmr: false,
    fs: { allow: [WEB_ROOT] },
  },
});

const harnessUrl = `/@fs/${HARNESS.replaceAll('\\', '/')}`;
const server = http.createServer((request, response) => {
  const url = new URL(request.url || '/', BASE);
  if (url.pathname === '/__nonbody_virtualization__') {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end(`<!doctype html>
      <html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
      <body style="margin:0;padding:24px;background:#101010;color:#eee">
        <div id="root"></div><script type="module" src="${harnessUrl}"></script>
      </body></html>`);
    return;
  }
  vite.middlewares(request, response, () => {
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('not found');
  });
});

await new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(PORT, '127.0.0.1', resolve);
});

const consoleErrors = [];
const protectedRequests = [];
let browser;

try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on('pageerror', (error) => consoleErrors.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(`console: ${message.text()}`);
  });
  page.on('request', (request) => {
    if (/:(8767|8768)\b/.test(request.url())) protectedRequests.push(request.url());
  });

  await page.goto(`${BASE}/__nonbody_virtualization__`, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.__nonBodyVirtualization?.childGroupCount === 240);

  const outer = page.locator('.non-body-group > button');
  const childRows = page.locator('[data-child-group]');
  assert.equal(await outer.getAttribute('aria-expanded'), 'false');
  assert.equal(await childRows.count(), 0, 'folded outer group mounted child rows');

  await outer.click();
  const viewport = page.getByTestId('non-body-group-window');
  await viewport.waitFor({ state: 'visible' });
  await page.waitForFunction(() => document.querySelectorAll('[data-child-group]').length > 0);
  const waitForStableScrollHeight = async (element) => {
    let previous = -1;
    let stableSamples = 0;
    const started = Date.now();
    while (Date.now() - started < 5000) {
      const current = await element.evaluate((node) => node.scrollHeight);
      if (current === previous) stableSamples += 1;
      else stableSamples = 0;
      if (stableSamples >= 3) return current;
      previous = current;
      await page.waitForTimeout(50);
    }
    throw new Error('nested virtualizer scroll height did not settle');
  };
  await waitForStableScrollHeight(page.getByTestId('non-body-group-window'));

  const totalGroupCount = Number(await viewport.getAttribute('data-group-count'));
  const initialGeometry = await viewport.evaluate((element) => ({
    width: element.clientWidth,
    height: element.clientHeight,
    maxHeight: getComputedStyle(element).maxHeight,
    scrollHeight: element.scrollHeight,
    scrollTop: element.scrollTop,
    mounted: element.querySelectorAll('[data-child-group]').length,
    firstIndex: Number(element.querySelector('[data-child-group]')?.getAttribute('data-index')),
    firstRowHeight: element.querySelector('[data-child-group]')?.getBoundingClientRect().height,
    firstRowOffset: element.querySelector('[data-index="0"]')?.getBoundingClientRect().top - element.getBoundingClientRect().top,
    secondRowOffset: element.querySelector('[data-index="1"]')?.getBoundingClientRect().top - element.getBoundingClientRect().top,
  }));
  assert.equal(totalGroupCount, 240);
  assert.equal(initialGeometry.maxHeight, '320px');
  assert.ok(initialGeometry.scrollHeight > initialGeometry.height, 'child viewport has no scroll range');
  assert.ok(initialGeometry.mounted > 0 && initialGeometry.mounted < totalGroupCount);
  assert.ok(initialGeometry.mounted <= 24, `too many child rows mounted: ${initialGeometry.mounted}`);

  const maxScroll = await viewport.evaluate((element) => element.scrollHeight - element.clientHeight);
  const box = await viewport.boundingBox();
  assert.ok(box, 'child viewport has no browser box');
  await page.mouse.move(box.x + Math.min(box.width - 8, 100), box.y + Math.min(box.height - 8, 100));
  await page.mouse.wheel(0, 1200);
  await page.waitForFunction(() => document.querySelector('[data-testid="non-body-group-window"]').scrollTop > 0);
  const downGeometry = await viewport.evaluate((element) => ({
    scrollTop: element.scrollTop,
    scrollHeight: element.scrollHeight,
    clientHeight: element.clientHeight,
    mounted: element.querySelectorAll('[data-child-group]').length,
    firstIndex: Number(element.querySelector('[data-child-group]')?.getAttribute('data-index')),
  }));
  assert.ok(downGeometry.scrollTop > 0, 'wheel down did not scroll the nested viewport');
  assert.ok(downGeometry.firstIndex > initialGeometry.firstIndex, 'down-scroll did not advance the mounted range');
  assert.ok(downGeometry.mounted <= 24);

  // Jump the native viewport to its current lower bound to inspect the tail
  // range; wheel direction and native scroll behavior were exercised above.
  await viewport.evaluate((element) => element.scrollTo({ top: element.scrollHeight, behavior: 'instant' }));
  await page.waitForFunction(() => {
    const element = document.querySelector('[data-testid="non-body-group-window"]');
    return element.scrollTop >= element.scrollHeight - element.clientHeight - 2;
  });
  const bottomIndex = Number(await viewport.locator('[data-child-group]').last().getAttribute('data-index'));
  assert.ok(bottomIndex > 220, `bottom range did not reach the tail: ${bottomIndex}`);

  const bottomScrollTop = await viewport.evaluate((element) => element.scrollTop);
  const viewportBox = await viewport.boundingBox();
  assert.ok(viewportBox);
  await page.mouse.move(viewportBox.x + Math.min(viewportBox.width - 8, 100), viewportBox.y + Math.min(viewportBox.height - 8, 100));
  await page.mouse.wheel(0, -1200);
  await page.waitForFunction((previousTop) => {
    return document.querySelector('[data-testid="non-body-group-window"]').scrollTop < previousTop;
  }, bottomScrollTop);
  const afterWheelUp = await viewport.evaluate((element) => element.scrollTop);
  await viewport.evaluate((element) => element.scrollTo({ top: 0, behavior: 'instant' }));
  await page.waitForFunction(() => document.querySelector('[data-testid="non-body-group-window"]').scrollTop === 0);
  const topThinking = viewport.locator('[data-index="0"] .thinking > button');
  await topThinking.click();
  await page.waitForFunction(() => document.querySelector('[data-index="0"] .thinking > button')?.getAttribute('aria-expanded') === 'true');
  await page.waitForTimeout(200);
  await page.waitForFunction(() => document.querySelector('[data-index="0"]')?.getBoundingClientRect().height > 48);
  const expandedChildHeight = await viewport.locator('[data-index="0"]').evaluate((element) => element.getBoundingClientRect().height);
  assert.ok(expandedChildHeight > 48, `child disclosure did not expand its measured row: ${expandedChildHeight}`);
  const expandedScrollHeight = await waitForStableScrollHeight(viewport);
  await page.waitForFunction((foldedOffset) => {
    const element = document.querySelector('[data-testid="non-body-group-window"]');
    const row = element.querySelector('[data-index="1"]');
    return row.getBoundingClientRect().top - element.getBoundingClientRect().top > foldedOffset + 20;
  }, initialGeometry.secondRowOffset);
  const expandedSecondRowOffset = await viewport.evaluate((element) =>
    element.querySelector('[data-index="1"]').getBoundingClientRect().top - element.getBoundingClientRect().top,
  );
  assert.ok(
    expandedSecondRowOffset > initialGeometry.secondRowOffset + 20,
    `next child row did not follow the expanded measurement: ${initialGeometry.secondRowOffset} -> ${expandedSecondRowOffset}`,
  );
  const expandedFirstRowBottom = await viewport.locator('[data-index="0"]').evaluate((element) =>
    element.getBoundingClientRect().bottom - element.parentElement.parentElement.getBoundingClientRect().top,
  );
  assert.ok(expandedSecondRowOffset >= expandedFirstRowBottom + 7, 'expanded rows overlap or lose the virtual gap');

  await viewport.evaluate((element) => element.scrollTo({ top: 1600, behavior: 'instant' }));
  await page.waitForFunction(() => !document.querySelector('[data-index="0"]'));
  await viewport.evaluate((element) => element.scrollTo({ top: 0, behavior: 'instant' }));
  await page.waitForFunction(() => {
    const row = document.querySelector('[data-index="0"]');
    const header = row?.querySelector('.thinking > button');
    return row && header?.getAttribute('aria-expanded') === 'false' && row.getBoundingClientRect().height < 48;
  });
  const remountedFoldedScrollHeight = await waitForStableScrollHeight(viewport);
  const remountedFoldedHeight = await viewport.locator('[data-index="0"]').evaluate((element) => element.getBoundingClientRect().height);
  const remountedSecondRowOffset = await viewport.evaluate((element) =>
    element.querySelector('[data-index="1"]').getBoundingClientRect().top - element.getBoundingClientRect().top,
  );
  assert.ok(remountedSecondRowOffset >= remountedFoldedHeight + 7, 'remounted rows retained an expanded spacer or overlapped');
  await viewport.locator('[data-index="0"] .thinking > button').click();
  await page.waitForFunction(() => document.querySelector('[data-index="0"]')?.getBoundingClientRect().height > 48);
  await page.waitForTimeout(200);
  const remeasuredExpandedScrollHeight = await waitForStableScrollHeight(viewport);
  await page.waitForFunction((foldedOffset) => {
    const element = document.querySelector('[data-testid="non-body-group-window"]');
    const row = element.querySelector('[data-index="1"]');
    return row.getBoundingClientRect().top - element.getBoundingClientRect().top > foldedOffset + 20;
  }, remountedSecondRowOffset);
  const remeasuredSecondRowOffset = await viewport.evaluate((element) =>
    element.querySelector('[data-index="1"]').getBoundingClientRect().top - element.getBoundingClientRect().top,
  );
  assert.ok(remeasuredSecondRowOffset > remountedSecondRowOffset + 20, 'reopened child disclosure did not update the next row position');
  await viewport.locator('[data-index="0"] .thinking > button').click();
  await page.waitForFunction(() => document.querySelector('[data-index="0"]')?.getBoundingClientRect().height < 48);
  await page.waitForTimeout(200);
  await waitForStableScrollHeight(viewport);
  await page.waitForFunction((expandedOffset) => {
    const element = document.querySelector('[data-testid="non-body-group-window"]');
    const row = element.querySelector('[data-index="1"]');
    return row.getBoundingClientRect().top - element.getBoundingClientRect().top < expandedOffset - 20;
  }, remeasuredSecondRowOffset);
  const collapsedSecondRowOffset = await viewport.evaluate((element) =>
    element.querySelector('[data-index="1"]').getBoundingClientRect().top - element.getBoundingClientRect().top,
  );
  assert.ok(collapsedSecondRowOffset < remeasuredSecondRowOffset - 20, 'folded child retained its expanded row position');

  await outer.click();
  assert.equal(await childRows.count(), 0, 'outer collapse retained child rows');
  assert.equal(await viewport.count(), 0, 'outer collapse retained the child viewport');
  await outer.click();
  await page.waitForFunction(() => document.querySelectorAll('[data-child-group]').length > 0);
  assert.equal(await outer.getAttribute('aria-expanded'), 'true');
  assert.equal(await viewport.evaluate((element) => element.scrollTop), 0, 'reopen did not reset the remounted viewport');
  assert.equal(await viewport.locator('[data-index="0"] .thinking > button').getAttribute('aria-expanded'), 'false');
  await waitForStableScrollHeight(viewport);

  const finalGeometry = await viewport.evaluate((element) => ({
    width: element.clientWidth,
    height: element.clientHeight,
    scrollHeight: element.scrollHeight,
    scrollTop: element.scrollTop,
    mounted: element.querySelectorAll('[data-child-group]').length,
  }));
  const result = {
    viewport: { width: initialGeometry.width, height: initialGeometry.height, maxHeight: initialGeometry.maxHeight },
    overflow: {
      scrollHeight: initialGeometry.scrollHeight,
      clientHeight: initialGeometry.height,
      hasVerticalRange: initialGeometry.scrollHeight > initialGeometry.height,
      maxScrollAtInitialMeasurement: maxScroll,
    },
    totalGroupCount,
    mountedGroupCount: {
      folded: 0,
      expandedAtTop: initialGeometry.mounted,
      afterWheelDown: downGeometry.mounted,
      reopenedAtTop: finalGeometry.mounted,
    },
    scroll: {
      initial: initialGeometry.scrollTop,
      afterWheelDown: downGeometry.scrollTop,
      maxScroll,
      bottomIndex,
      afterWheelUp,
      reopened: finalGeometry.scrollTop,
    },
    nestedMeasurement: {
      foldedScrollHeight: initialGeometry.scrollHeight,
      expandedScrollHeight,
      remountedFoldedScrollHeight,
      remountedFirstChildHeight: remountedFoldedHeight,
      remeasuredExpandedScrollHeight,
      nextRowOffsets: {
        folded: initialGeometry.secondRowOffset,
        expanded: expandedSecondRowOffset,
        remountedFolded: remountedSecondRowOffset,
        remeasuredExpanded: remeasuredSecondRowOffset,
        collapsed: collapsedSecondRowOffset,
      },
    },
    expandedChildHeight,
    finalScrollHeight: finalGeometry.scrollHeight,
    consoleErrors,
    protectedRequests,
  };

  assert.deepEqual(consoleErrors, []);
  assert.deepEqual(protectedRequests, []);
  console.log(JSON.stringify({ ok: true, ...result }, null, 2));
} finally {
  if (browser) await browser.close();
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  await vite.close();

  const portIsClosed = await new Promise((resolve) => {
    const probe = net.createConnection({ host: '127.0.0.1', port: PORT });
    probe.once('connect', () => {
      probe.destroy();
      resolve(false);
    });
    probe.once('error', () => resolve(true));
  });
  assert.ok(portIsClosed, `isolated browser port ${PORT} is still listening`);
  console.log(JSON.stringify({ cleanup: { port: PORT, listenerClosed: portIsClosed } }));
}
