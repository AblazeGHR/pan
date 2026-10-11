/* global console:readonly, document:readonly, KeyboardEvent:readonly */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { chromium } from '@playwright/test';

const web = path.resolve(import.meta.dirname, '..');
const evidenceDir = path.join(web, 'test-results/search-editor-unread');
await fs.mkdir(evidenceDir, { recursive: true });
const requestedPort = await new Promise((resolve, reject) => {
  const probe = net.createServer();
  probe.once('error', reject);
  probe.listen(0, '127.0.0.1', () => {
    const port = probe.address().port;
    probe.close(() => resolve(port));
  });
});
const server = await createServer({
  configFile: false,
  root: web,
  cacheDir: path.join(evidenceDir, 'vite-cache'),
  resolve: { alias: { '@': path.join(web, 'src') } },
  server: { host: '127.0.0.1', port: requestedPort, strictPort: true, proxy: {} },
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'isolated-fixture',
      configureServer(vite) {
        vite.middlewares.use('/__fixture', async (_req, res) => {
          res.setHeader('Content-Type', 'text/html');
          res.end(
            await vite.transformIndexHtml(
              '/__fixture',
              '<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/e2e/searchEditorUnreadFixture.tsx"></script></body></html>',
            ),
          );
        });
      },
    },
  ],
});
await server.listen();
const port = server.httpServer.address().port;
assert.ok(port !== 8767 && port !== 8768);
const browser = await chromium.launch({ headless: true });
const evidence = { port, tests: [], requests: [], errors: [] };
let activePage;
try {
  for (const mobile of [false, true]) {
    const context = await browser.newContext({
      viewport: mobile ? { width: 390, height: 700 } : { width: 1280, height: 800 },
      isMobile: mobile,
      hasTouch: mobile,
    });
    await context.tracing.start({ screenshots: true, snapshots: true });
    const page = await context.newPage();
    activePage = page;
    page.on('pageerror', (error) => evidence.errors.push(String(error)));
    page.on('request', (request) => evidence.requests.push(request.url()));
    await page.route('**/api/**', (route) => route.abort());
    await page.goto(`http://127.0.0.1:${port}/__fixture${mobile ? '?mobile' : ''}`);
    await page.getByTestId('workspace-unread-w1').waitFor({ state: 'visible' });
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.getByTestId('workspace-unread-w2').count(), 0);
    assert.equal(await page.getByTestId('workspace-count-w1').innerText(), '2');
    assert.equal(
      await page.getByTestId('workspace-count-w1').getAttribute('data-worker-status'),
      'running',
    );
    const before = await page.getByTestId('workspace-count-w1').boundingBox();
    const dot = await page.getByTestId('workspace-unread-w1').boundingBox();
    assert.ok(dot.x + dot.width <= before.x, 'dot does not overlap count');
    if (mobile) assert.equal(await page.evaluate(() => document.documentElement.clientWidth), 390);
    await page.screenshot({
      path: path.join(evidenceDir, mobile ? 'mobile-unread.png' : 'desktop-unread.png'),
    });
    await page.getByRole('button', { name: 'Mark all read' }).click();
    await page.getByTestId('workspace-unread-w1').waitFor({ state: 'detached' });
    assert.deepEqual(await page.getByTestId('workspace-count-w1').boundingBox(), before);
    if (!mobile) {
      const row = page.getByText('draft.ts', { exact: true });
      await row.hover();
      const deletion = page.getByRole('button', { name: 'Delete', exact: true });
      await deletion.locator('svg.lucide-trash-2').waitFor({ state: 'attached' });
      assert.equal(await deletion.locator('svg.lucide-trash-2').count(), 1);
      assert.equal(await deletion.getAttribute('title'), 'Delete');
      await deletion.click();
      await page.getByRole('dialog').waitFor({ state: 'visible' });
      await page.getByRole('button', { name: '取消', exact: true }).click();
      assert.equal(await row.isVisible(), true);
      assert.equal(
        evidence.requests.some((url) => url.includes('/fs/delete')),
        false,
      );
    }
    await page.screenshot({ path: path.join(evidenceDir, mobile ? 'mobile.png' : 'desktop.png') });
    evidence.tests.push(
      `${mobile ? 'mobile touch' : 'desktop'}: inherited unread, separate count/status, read clears without displacement${mobile ? '' : ', trash opens confirmation and cancel preserves file'}`,
    );
    await context.tracing.stop({
      path: path.join(evidenceDir, mobile ? 'mobile-trace.zip' : 'desktop-trace.zip'),
    });
    await context.close();
  }
  const result = {
    hits: [0, 1, 2].map((index) => ({
      sessionId: 'root',
      messageId: `hit${index}`,
      messageIndex: index,
      role: 'assistant',
      snippet: 'needle',
      historyTotal: 3,
      historyEpoch: 'fixture',
      historyRevision: 1,
      matchStart: index,
      matchCount: 1,
    })),
    totalMatches: 3,
    totalMessages: 3,
    hasMore: false,
    nextCursor: null,
    matchingSessionIds: ['root'],
    limit: 100,
  };
  for (const mobile of [false, true]) {
    const context = await browser.newContext({
      viewport: mobile ? { width: 390, height: 700 } : { width: 1280, height: 800 },
      isMobile: mobile,
      hasTouch: mobile,
    });
    await context.tracing.start({ screenshots: true, snapshots: true });
    const page = await context.newPage();
    activePage = page;
    page.on('pageerror', (error) => evidence.errors.push(String(error)));
    page.on('request', (request) => evidence.requests.push(request.url()));
    await page.route('**/api/**', async (route) => {
      if (route.request().url().includes('/history/search/prepare'))
        return route.fulfill({
          contentType: 'application/x-ndjson',
          body:
            JSON.stringify({ result, done: true, completed: 1, total: 1, failedSessions: [] }) +
            '\n',
        });
      if (route.request().url().includes('/history/search')) return route.fulfill({ json: result });
      await route.abort();
    });
    await page.goto(`http://127.0.0.1:${port}/__fixture?search${mobile ? '&mobile' : ''}`);
    for (const global of [false, true]) {
      const prefix = global ? 'global' : 'session';
      await page.getByTestId(`${prefix}-history-search-toggle`).click();
      const input = page.getByTestId(`${prefix}-history-search-input`);
      assert.equal(await input.evaluate((element) => element === document.activeElement), true);
      await input.fill('needle');
      await page.waitForLoadState('networkidle');
      const count = page
        .getByTestId(global ? 'global-current-session-count' : 'session-history-search-count')
        .locator('input');
      await count.waitFor({ state: 'visible' });
      await page.waitForFunction(
        (testId) => document.querySelector(`[data-testid="${testId}"] input`)?.value === '3',
        global ? 'global-current-session-count' : 'session-history-search-count',
      );
      await page
        .getByRole('button', { name: 'Next result', exact: true })
        .waitFor({ state: 'visible' });
      await page.waitForFunction(
        () => !document.querySelector('button[aria-label="Next result"]')?.disabled,
      );
      for (const key of ['Enter', 'Escape'])
        await input.evaluate(
          (element, key) =>
            element.dispatchEvent(
              new KeyboardEvent('keydown', { key, isComposing: true, bubbles: true }),
            ),
          key,
        );
      assert.equal(await count.inputValue(), '3');
      assert.equal(await input.isVisible(), true);
      await input.press('Enter');
      await page.waitForFunction(
        (testId) => document.querySelector(`[data-testid="${testId}"] input`)?.value === '1',
        global ? 'global-current-session-count' : 'session-history-search-count',
      );
      await page.waitForFunction(
        () => !document.querySelector('button[aria-label="Next result"]')?.disabled,
      );
      await input.press('Shift+Enter');
      await page.waitForFunction(
        (testId) => document.querySelector(`[data-testid="${testId}"] input`)?.value === '3',
        global ? 'global-current-session-count' : 'session-history-search-count',
      );
      assert.equal(await count.inputValue(), '3');
      if (global) {
        assert.equal(await input.getAttribute('type'), 'text');
        await page.getByRole('button', { name: 'Clear global search' }).click();
        assert.equal(await input.inputValue(), '');
        assert.equal(await input.evaluate((element) => element === document.activeElement), true);
      }
      await page.getByRole('checkbox', { name: 'Search user messages' }).focus();
      await page.keyboard.press('Escape');
      await input.waitFor({ state: 'detached' });
    }
    evidence.tests.push(
      `${mobile ? 'mobile touch' : 'desktop'} real Session/global search components: input focus, Enter/Shift+Enter wrap, IME ignored, Esc from role checkbox closes, clear focuses input`,
    );
    await context.tracing.stop({
      path: path.join(evidenceDir, mobile ? 'search-mobile-trace.zip' : 'search-desktop-trace.zip'),
    });
    await context.close();
  }
  assert.deepEqual(evidence.errors, []);
  assert.equal(
    evidence.requests.some((url) => /:(8767|8768)(\/|$)/.test(url)),
    false,
  );
  console.log(JSON.stringify(evidence.tests));
} catch (error) {
  evidence.failure = String(error.stack || error);
  evidence.body = await activePage?.locator('body').innerText();
  evidence.inputs = await activePage
    ?.locator('input')
    .evaluateAll((inputs) =>
      inputs.map((input) => ({ label: input.getAttribute('aria-label'), value: input.value })),
    );
  await activePage?.screenshot({ path: path.join(evidenceDir, 'failure.png') });
  throw error;
} finally {
  await fs.writeFile(path.join(evidenceDir, 'evidence.json'), JSON.stringify(evidence, null, 2));
  await browser.close();
  await server.close();
}
