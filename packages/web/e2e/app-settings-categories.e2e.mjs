/* global URL, fetch, document, innerWidth, console, process */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, unlinkSync, writeFileSync } from 'node:fs';
import net from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

// Isolated fixture only: Vite on 5214, API requests are fulfilled in-process.
// This script never contacts Pan's practical services (8767/8768).
const port = 5214;
const harnessName = '.app-settings-categories-harness';
const htmlPath = new URL(`../src/${harnessName}.html`, import.meta.url);
const tsxPath = new URL(`../src/${harnessName}.tsx`, import.meta.url);
const baseURL = `http://127.0.0.1:${port}/${harnessName}.html`;
const cwd = new URL('..', import.meta.url).pathname.replace(/^\/(\w:)/, '$1').replaceAll('/', '\\');
const labels = [
  'Lifecycle',
  'Behavior',
  'Remote',
  'Appearance',
  'Notifications',
  'Adapters',
  'Plugins',
  'Data',
];

await new Promise((resolve, reject) => {
  const server = net.createServer();
  server.once('error', reject);
  server.listen(port, '127.0.0.1', () => server.close(resolve));
});

writeFileSync(
  htmlPath,
  `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1.0" /></head><body><div id="root"></div><script type="module" src="/${harnessName}.tsx"></script></body></html>`,
);
writeFileSync(
  tsxPath,
  `import { useState } from 'react';\nimport { createRoot } from 'react-dom/client';\nimport { AppSettingsModal } from './components/layout/AppSettingsModal';\nimport { useAppSettingsStore, DEFAULT_SETTINGS } from './stores/appSettingsStore';\nimport './index.css';\nuseAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });\nfunction Harness() {\n  const [open, setOpen] = useState(true);\n  return <><button id="reopen-settings" type="button" onClick={() => setOpen(true)}>Open settings</button><AppSettingsModal open={open} onClose={() => setOpen(false)} /></>;\n}\ncreateRoot(document.getElementById('root')!).render(<Harness />);`,
);

const server = spawn(
  process.execPath,
  [
    fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url)),
    '--host',
    '127.0.0.1',
    '--port',
    String(port),
    '--strictPort',
  ],
  { cwd, windowsHide: true, stdio: 'ignore' },
);

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
    viewport: { width: 1280, height: 900 },
    deviceScaleFactor: 1,
    isMobile: false,
    hasTouch: true,
  });
  const pageErrors = [];
  const consoleErrors = [];
  const protectedRequests = [];
  const writeRequests = [];
  const lifecycleWrites = [];
  const restartRequests = [];
  const exitRequests = [];

  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('request', (request) => {
    const url = request.url();
    if (/:(8767|8768)(\/|$)/.test(url)) protectedRequests.push(url);
    const path = new URL(url).pathname;
    if (request.method() !== 'GET') writeRequests.push(`${request.method()} ${path}`);
    if (path === '/api/main/restart') restartRequests.push(url);
    if (path === '/api/main/exit') exitRequests.push(url);
    if (path === '/api/settings/session-lifecycle' && request.method() === 'PUT') {
      lifecycleWrites.push(request.postData() ?? '');
    }
  });

  await page.route('**/api/**', (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/settings/login-startup') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ enabled: false, registered: false, available: true, conflict: false, blocked: false, reason: '', targetRoot: 'fixture', dataDirectory: 'fixture', entry: 'fixture', registration: null, revision: 'fixture' }) });
    }
    if (path === '/api/settings/session-lifecycle') {
      const body = request.method() === 'PUT'
        ? JSON.parse(request.postData() ?? '{}')
        : { exitStrategy: 'ask', startupPreference: 'ask' };
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      });
    }
    if (path === '/api/main/restart/status') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ available: true, pending: false, platform: 'nt' }),
      });
    }
    if (path === '/api/main/exit/status') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ available: true, pending: false, platform: 'nt', stage: 'idle' }),
      });
    }
    if (path === '/api/remote/status') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ available: false, enabled: false, running: false }),
      });
    }
    if (path === '/api/codex/models') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ models: [], default: '' }),
      });
    }
    if (path === '/api/remote/mcp') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          config: {
            enabled: false,
            port: 8769,
            public_hostname: '',
            access_issuer: '',
            access_audience: '',
            config_path: '',
            binary_path: '',
          },
          publicUrl: '',
          gateway: { listening: false },
          tunnel: { running: false },
        }),
      });
    }
    if (path === '/api/remote/tailscale') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          config: { startupEnabled: false, protocol: 'http', port: 18080 },
          running: false,
          url: '',
          target: '',
          error: '',
          pendingCleanup: false,
        }),
      });
    }
    if (path === '/api/remote/lan') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          config: { startupEnabled: false, host: '', port: 8780 },
          activeConfig: null,
          running: false,
          addresses: [],
          urls: [],
          error: '',
        }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '{}',
    });
  });

  async function waitForSettings() {
    await page.getByRole('tab', { name: 'Lifecycle' }).waitFor({ state: 'visible' });
    await page.waitForFunction(() => {
      const button = Array.from(document.querySelectorAll('button')).find((candidate) =>
        candidate.textContent?.includes('Restart Pan main service'),
      );
      return Boolean(button && !button.disabled);
    });
  }

  async function assertFocused(id) {
    await page.waitForFunction(
      (expected) => document.activeElement?.id === expected,
      id,
      { timeout: 5000 },
    );
  }

  async function assertSettingVisible(id) {
    const visible = await page.evaluate((expected) => {
      const panel = document.querySelector('[role="tabpanel"]');
      const element = document.getElementById(expected);
      if (!panel || !element) return false;
      const panelRect = panel.getBoundingClientRect();
      const elementRect = element.getBoundingClientRect();
      return elementRect.top >= panelRect.top && elementRect.bottom <= panelRect.bottom;
    }, id);
    assert.ok(visible, `${id} is not inside the visible settings panel`);
  }

  async function assertNoWrites(context) {
    assert.deepEqual(writeRequests, [], `${context}: unexpected write requests ${writeRequests}`);
    assert.deepEqual(lifecycleWrites, [], `${context}: lifecycle policy was written`);
    assert.deepEqual(restartRequests, [], `${context}: restart was requested`);
    assert.deepEqual(exitRequests, [], `${context}: exit was requested`);
  }

  await page.goto(baseURL, { waitUntil: 'networkidle' });
  await waitForSettings();

  assert.equal(
    await page.getByRole('tab', { name: 'Lifecycle' }).getAttribute('aria-selected'),
    'true',
    'the dialog must open on Lifecycle',
  );
  assert.deepEqual(
    await page.getByRole('tab').evaluateAll((tabs) => tabs.map((tab) => tab.textContent.trim())),
    labels,
    'category order/name contract',
  );
  assert.equal(await page.locator('#app-settings-tab-shortcuts').count(), 0);
  assert.equal(await page.locator('#app-settings-tab-general').count(), 0);
  assert.equal(await page.locator('#app-settings-tab-preferences').count(), 0);

  // Desktop jump 1: exit/restart policy.
  await page.getByTestId('open-exit-session-policy').click();
  assert.equal(
    await page.getByRole('tab', { name: 'Behavior' }).getAttribute('aria-selected'),
    'true',
  );
  await assertFocused('session-exit-strategy');
  await assertSettingVisible('session-exit-strategy');
  assert.equal(await page.locator('#session-exit-strategy').inputValue(), 'ask');

  // Desktop jump 2: startup recovery policy.
  await page.getByRole('tab', { name: 'Lifecycle' }).click();
  await page.getByTestId('open-startup-session-policy').click();
  await assertFocused('session-startup-preference');
  await assertSettingVisible('session-startup-preference');
  assert.equal(await page.locator('#session-startup-preference').inputValue(), 'ask');

  // Keyboard reaches both the tab strip and the shortcuts.
  await page.getByRole('tab', { name: 'Lifecycle' }).click();
  await page.getByRole('tab', { name: 'Lifecycle' }).focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(
    await page.getByRole('tab', { name: 'Behavior' }).getAttribute('aria-selected'),
    'true',
  );
  await page.keyboard.press('Home');
  assert.equal(
    await page.getByRole('tab', { name: 'Lifecycle' }).getAttribute('aria-selected'),
    'true',
  );
  await page.getByTestId('open-exit-session-policy').focus();
  await page.keyboard.press('Enter');
  await assertFocused('session-exit-strategy');

  // Reopening resets to Lifecycle and preserves the saved policy values.
  await page.getByRole('button', { name: 'Close' }).click();
  await page.locator('#reopen-settings').click();
  await waitForSettings();
  assert.equal(
    await page.getByRole('tab', { name: 'Lifecycle' }).getAttribute('aria-selected'),
    'true',
    'reopening must default to Lifecycle',
  );
  assert.equal(await page.locator('#app-settings-tabpanel').getAttribute('aria-labelledby'), 'app-settings-tab-lifecycle');

  await assertNoWrites('desktop navigation');

  // Mobile 390: both jumps stay usable and do not move the page horizontally.
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForSettings();
  const mobileOverflow = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: innerWidth,
  }));
  assert.ok(
    mobileOverflow.documentWidth <= mobileOverflow.viewportWidth,
    `390px document overflowed: ${JSON.stringify(mobileOverflow)}`,
  );

  await page.getByRole('tab', { name: 'Lifecycle' }).click();
  await page.getByTestId('open-exit-session-policy').click();
  await assertFocused('session-exit-strategy');
  await assertSettingVisible('session-exit-strategy');
  await page.getByRole('tab', { name: 'Lifecycle' }).click();
  await page.getByTestId('open-startup-session-policy').click();
  await assertFocused('session-startup-preference');
  await assertSettingVisible('session-startup-preference');

  await assertNoWrites('mobile navigation');
  assert.deepEqual(protectedRequests, [], `protected services were contacted: ${protectedRequests}`);
  assert.deepEqual(pageErrors, [], `page errors: ${pageErrors.join('; ')}`);
  assert.deepEqual(consoleErrors, [], `console errors: ${consoleErrors.join('; ')}`);

  console.log(JSON.stringify({
    desktop: {
      labels,
      focus: ['session-exit-strategy', 'session-startup-preference'],
      writes: writeRequests,
    },
    mobile: { width: 390, overflow: mobileOverflow, writes: writeRequests },
    protectedRequests,
    pageErrors,
    consoleErrors,
  }));
} finally {
  await browser?.close();
  server.kill();
  for (const path of [htmlPath, tsxPath]) if (existsSync(path)) unlinkSync(path);
}
