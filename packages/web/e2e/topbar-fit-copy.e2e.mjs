/* global URL, fetch, console, document, window, innerWidth, getComputedStyle, requestAnimationFrame, navigator */
import assert from 'node:assert/strict';
import path from 'node:path';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const cwd = fileURLToPath(new URL('..', import.meta.url));
const LONG_NAME =
  'A very long session name with an unbreakable identifier abcdefghijklmnopqrstuvwxyz0123456789';

async function freePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

const port = await freePort();
const baseURL = `http://127.0.0.1:${port}/topbar-fit-copy-harness.html`;

// Programmatic server with an isolated cache dir; every /api call is mocked by
// Playwright, so no Pan instance or provider is contacted.
const server = await createServer({
  configFile: false,
  root: path.join(cwd, 'src'),
  cacheDir: path.join(cwd, 'test-results/topbar-fit-copy/vite-cache'),
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
    } catch { /* still starting */ }
    await delay(250);
  }
  assert.ok(ready, `Vite did not become ready at ${baseURL}`);

  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    permissions: ['clipboard-read', 'clipboard-write'],
  });
  const page = await context.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  const protectedRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('request', (request) => {
    if (/:(8767|8768)(\/|$)/.test(request.url())) protectedRequests.push(request.url());
  });

  // Persisted ui object owned by this mock server (survives page.reload()).
  let ui = {};
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const json = (body) => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(body),
    });
    if (url.pathname === '/api/settings/ui') {
      if (request.method() === 'PUT') {
        try { ui = { ...ui, ...JSON.parse(request.postData() || '{}') }; } catch { /* ignore */ }
      }
      await json(ui);
      return;
    }
    if (/\/api\/sessions\/[^/]+\/usage$/.test(url.pathname)) {
      await json({
        ok: true,
        codexQuota: {
          ok: true, cacheMode: 'persisted', profileKey: 'profile-fit',
          windows: {
            first: { kind: 'five_hour', usage: { usedPercent: 20 } },
            secondary: { kind: 'weekly', usage: { usedPercent: 45 } },
          },
        },
      });
      return;
    }
    if (url.pathname.includes('/native-control')) {
      await json({ ok: true, result: { workerId: 'worker-fit', mode: 'pan', generation: 1, error: null, enabled: false } });
      return;
    }
    if (url.pathname === '/api/sessions/lifecycle-preferences') {
      await json({ exitStrategy: 'ask', startupPreference: 'ask' });
      return;
    }
    if (url.pathname === '/api/data/catalog') {
      await json({ catalog: [] });
      return;
    }
    if (url.pathname === '/api/settings/data-retention') {
      await json({ policies: [], lastSeen: null, lastScans: [] });
      return;
    }
    if (url.pathname === '/api/codex/models') {
      await json({ models: [], defaultModel: '' });
      return;
    }
    if (url.pathname === '/api/health') {
      await json({ ok: true, status: 'ok' });
      return;
    }
    await json({});
  });

  await page.goto(baseURL, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => typeof window.__openSettings === 'function', null, { timeout: 10000 });
  await page.waitForSelector('[data-testid="topbar-quota-usage"]');

  const settle = () => page.evaluate(() => new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));

  const snapshot = () => page.evaluate(() => {
    const bar = document.querySelector('[data-testid="topbar"]');
    const actions = document.querySelector('[data-testid="topbar-actions"]');
    const name = document.querySelector('[data-testid="topbar-session-name"]');
    const context = document.querySelector('[data-testid="topbar-context-usage"]');
    const quota = document.querySelector('[data-testid="topbar-quota-usage"]');
    const rect = (element) => {
      if (!element) return null;
      const { x, y, width, height, right, bottom } = element.getBoundingClientRect();
      return { x, y, width, height, right, bottom };
    };
    const buttons = [...(bar?.querySelectorAll('button') ?? [])].map((button) => ({
      label: button.getAttribute('aria-label'), title: button.title, rect: rect(button),
    }));
    return {
      viewport: innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      fit: bar?.getAttribute('data-usage-fit'),
      bar: rect(bar),
      actions: rect(actions),
      name: name ? { text: name.textContent, tag: name.tagName, rect: rect(name) } : null,
      context: context ? {
        hidden: context.getAttribute('data-usage-hidden'),
        display: getComputedStyle(context).display,
        visibility: getComputedStyle(context).visibility,
        rect: rect(context),
      } : null,
      quota: quota ? {
        hidden: quota.getAttribute('data-usage-hidden'),
        display: getComputedStyle(quota).display,
        visibility: getComputedStyle(quota).visibility,
        rect: rect(quota),
      } : null,
      buttons,
      chatViewGroup: Boolean(document.querySelector('[role="group"][aria-label="Chat view"]')),
      terminalEntry: [...(bar?.querySelectorAll('button') ?? [])]
        .some((button) => button.getAttribute('aria-label') === 'Terminal TUI'),
    };
  });

  function assertSingleLine(geometry, label) {
    assert.equal(geometry.documentWidth, geometry.viewport, `Horizontal overflow (${label}): ${JSON.stringify(geometry)}`);
    assert.ok(geometry.bar && geometry.bar.height <= 49, `TopBar wrapped or grew (${label}): ${JSON.stringify(geometry.bar)}`);
    const center = geometry.bar.y + geometry.bar.height / 2;
    for (const control of geometry.buttons) {
      assert.ok(
        Math.abs(control.rect.y + control.rect.height / 2 - center) <= 1,
        `Control ${control.label} is not on the single TopBar row (${label})`,
      );
    }
  }

  // ── Desktop: full → context hidden → both hidden → recovery ──────────────
  const sweep = [];
  for (const width of [1440, 1300, 1200, 1100, 1050, 1000, 950, 900, 860, 820, 780]) {
    await page.setViewportSize({ width, height: 900 });
    await settle();
    const geometry = await snapshot();
    assertSingleLine(geometry, `${width}px`);
    sweep.push({ width, fit: geometry.fit, context: geometry.context?.hidden, quota: geometry.quota?.hidden });
    if (width === 1440) {
      assert.equal(geometry.fit, '0', `Wide layout must show both usage labels: ${JSON.stringify(geometry)}`);
      assert.equal(geometry.context.visibility, 'visible');
      assert.equal(geometry.quota.visibility, 'visible');
      assert.equal(geometry.name.rect.width, 200, 'Long name should use its 200px desktop cap');
      assert.equal(geometry.name.text, LONG_NAME);
    }
  }
  console.log('fit sweep:', JSON.stringify(sweep));

  const firstHiddenContext = sweep.find((entry) => entry.fit === '1');
  const firstHiddenBoth = sweep.find((entry) => entry.fit === '2');
  assert.ok(firstHiddenContext, `No width hid only the context label: ${JSON.stringify(sweep)}`);
  assert.equal(firstHiddenContext.context, 'true');
  assert.equal(firstHiddenContext.quota, 'false', 'Quota must survive while only context is dropped');
  assert.ok(firstHiddenBoth, `No width hid both usage labels: ${JSON.stringify(sweep)}`);
  assert.equal(firstHiddenBoth.context, 'true');
  assert.equal(firstHiddenBoth.quota, 'true');

  // Recovery restores in the opposite order (quota first, then context).
  await page.setViewportSize({ width: firstHiddenContext.width, height: 900 });
  await settle();
  let recovered = await snapshot();
  assert.equal(recovered.fit, '1');
  assert.equal(recovered.context.hidden, 'true');
  assert.equal(recovered.quota.hidden, 'false');
  await page.setViewportSize({ width: 1440, height: 900 });
  await settle();
  recovered = await snapshot();
  assert.equal(recovered.fit, '0');
  assert.equal(recovered.context.hidden, 'false');
  assert.equal(recovered.quota.hidden, 'false');
  assertSingleLine(recovered, 'recovered 1440px');

  // Long name matters at the same width: a short name fits without hiding.
  await page.setViewportSize({ width: firstHiddenContext.width, height: 900 });
  await settle();
  assert.equal((await snapshot()).fit, '1');
  await page.evaluate(() => window.__setSessionName('Short'));
  await settle();
  assert.equal((await snapshot()).fit, '0', 'Short name should not need any hiding');
  await page.evaluate((name) => window.__setSessionName(name), LONG_NAME);
  await settle();
  assert.equal((await snapshot()).fit, '1');

  // ── Copy: full name, mouse and keyboard, no navigation/rename ────────────
  await page.setViewportSize({ width: 1440, height: 900 });
  await settle();
  const urlBefore = page.url();
  await page.evaluate(() => navigator.clipboard.writeText('sentinel'));
  await page.click('[data-testid="topbar-session-name"]');
  await page.waitForFunction(
    (name) => document.body.textContent.includes(`Copied session name: ${name}`),
    LONG_NAME,
    { timeout: 5000 },
  );
  await page.waitForFunction(
    (expected) => navigator.clipboard.readText().then((value) => value === expected),
    LONG_NAME,
    { timeout: 5000 },
  );
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), LONG_NAME);
  assert.ok(
    await page.evaluate((name) => document.body.textContent.includes(`Copied session name: ${name}`), LONG_NAME),
    'Successful copy toast missing',
  );
  assert.equal(page.url(), urlBefore, 'Name click must not navigate');
  assert.equal(
    await page.evaluate(() => document.querySelector('[data-testid="topbar-session-name"]').textContent),
    LONG_NAME,
    'Name click must not rename',
  );

  await page.evaluate(() => navigator.clipboard.writeText('sentinel'));
  await page.focus('[data-testid="topbar-session-name"]');
  await page.keyboard.press('Enter');
  await page.waitForFunction(
    (expected) => navigator.clipboard.readText().then((value) => value === expected),
    LONG_NAME,
    { timeout: 5000 },
  );
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), LONG_NAME, 'Enter must copy');
  const focusStyle = await page.evaluate(() => {
    const button = document.querySelector('[data-testid="topbar-session-name"]');
    button.focus();
    return getComputedStyle(button).outlineStyle;
  });
  assert.notEqual(focusStyle, 'none', 'Name button needs a visible keyboard focus ring');

  // ── 390px mobile: labels stay hidden, switcher hides via Appearance ──────
  await page.setViewportSize({ width: 390, height: 844 });
  await settle();
  let mobile = await snapshot();
  assertSingleLine(mobile, '390px');
  assert.equal(mobile.fit, '0');
  assert.equal(mobile.context.display, 'none');
  assert.equal(mobile.quota.display, 'none');
  assert.ok(mobile.buttons.some((button) => /^Switch to /.test(button.label || '')),
    'Mobile cycle button should be visible by default');

  // ── Appearance: turn the switcher off, reload, turn it back on ───────────
  await page.setViewportSize({ width: 1440, height: 900 });
  await settle();
  assert.equal((await snapshot()).chatViewGroup, true);

  async function toggleSwitcher(expectedChecked) {
    await page.evaluate(() => window.__openSettings());
    await page.waitForSelector('.app-settings-card');
    await page.click('#app-settings-tab-appearance');
    const switcher = page.locator('[role="switch"]', { hasText: 'Show chat view switcher' });
    await switcher.waitFor({ state: 'visible' });
    assert.equal(await switcher.getAttribute('aria-checked'), expectedChecked);
    const put = page.waitForResponse((response) =>
      response.url().endsWith('/api/settings/ui') && response.request().method() === 'PUT');
    await switcher.click();
    await put;
    await page.keyboard.press('Escape');
    await page.waitForSelector('.app-settings-card', { state: 'detached' });
  }

  await toggleSwitcher('true');
  let afterOff = await snapshot();
  assert.equal(afterOff.chatViewGroup, false, 'Appearance off must hide the Chat view switcher');
  assert.equal(afterOff.terminalEntry, true, 'Terminal TUI / Upgrades must stay visible');
  assert.equal(
    await page.evaluate(() => [...document.querySelectorAll('button')].some(
      (button) => button.getAttribute('aria-label') === 'Terminal TUI' && button.textContent.includes('Upgrades'))),
    true,
  );

  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForSelector('[data-testid="topbar"]');
  await settle();
  let afterReload = await snapshot();
  assert.equal(afterReload.chatViewGroup, false, 'Disabled switcher must stay disabled after reload');
  assert.equal(afterReload.terminalEntry, true, 'Upgrades must survive reload with the switcher off');

  await toggleSwitcher('false');
  let afterOn = await snapshot();
  assert.equal(afterOn.chatViewGroup, true, 'Re-enabling restores the switcher');
  assert.equal(afterOn.terminalEntry, true);
  assert.equal((await page.evaluate(() => navigator.clipboard.readText())), LONG_NAME);

  // Mobile: the switcher switch also removes the compact cycle button only.
  await page.setViewportSize({ width: 390, height: 844 });
  await settle();
  await toggleSwitcher('true');
  mobile = await snapshot();
  assert.equal(mobile.chatViewGroup, false);
  assert.equal(mobile.buttons.some((button) => /^Switch to /.test(button.label || '')), false);
  assert.equal(mobile.terminalEntry, false, 'Mobile never mounts the Terminal TUI entry');
  assert.ok(mobile.buttons.some((button) => button.title === 'Restart worker'));
  assert.ok(mobile.buttons.some((button) => button.title === 'Kill worker'));
  await toggleSwitcher('false');

  // The mobile reload keeps the re-enabled compact cycle button.
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForSelector('[data-testid="topbar"]');
  await settle();
  const finalMobile = await snapshot();
  assert.equal(
    finalMobile.buttons.some((button) => /^Switch to /.test(button.label || '')),
    true,
    'Re-enabled switcher must survive reload on mobile',
  );
  assert.equal(finalMobile.chatViewGroup, false, 'Mobile renders the compact button, not the desktop group');

  assert.deepEqual(pageErrors, [], `Page errors: ${pageErrors.join('; ')}`);
  assert.deepEqual(consoleErrors, [], `Console errors: ${consoleErrors.join('; ')}`);
  assert.deepEqual(protectedRequests, [], `Protected-port requests: ${protectedRequests.join(', ')}`);
  console.log('topbar fit/copy browser acceptance passed');
} finally {
  await browser?.close();
  await server.close();
  await delay(300);
}
