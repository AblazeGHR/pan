/* global console, document */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const cwd = fileURLToPath(new URL('..', import.meta.url));

async function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer(); probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const value = probe.address().port; probe.close(() => resolve(value));
    });
  });
}

const port = await freePort();
// Isolated vite cache; no proxy — every /api request is intercepted below and
// never reaches a Pan instance, provider or PTY.
const server = await createServer({
  configFile: false,
  root: path.join(cwd, 'src'),
  cacheDir: path.join(cwd, 'test-results/settings-more/vite-cache'),
  resolve: { alias: { '@': path.join(cwd, 'src') } },
  plugins: [react(), tailwindcss()],
  server: { host: '127.0.0.1', port, strictPort: true, proxy: {} },
});
await server.listen();
let browser;

const session = {
  id: 'settings-more-browser',
  name: 'Settings More terminal entry fixture',
  adapter: 'codex',
  workerId: 'worker-1',
  workerStatus: 'running',
  nativeControl: false,
  model: 'gpt-6',
  alwaysThinkingEnabled: false,
  effort: '',
  history: [],
};

// Node-side mock native-control state; GET mirrors it so the TopBar poller
// always confirms the same picture the fixture set through __setTerminalState.
let state = { workerId: 'worker-1', mode: 'pan', generation: 1, error: null, enabled: false, pendingEnabled: null };
const configurations = [];
const modes = [];

async function waitForNode(condition, timeout = 5000) {
  const start = Date.now();
  while (!condition()) {
    if (Date.now() - start > timeout) throw new Error('node-side condition timed out');
    await delay(50);
  }
}

try {
  const baseURL = `http://127.0.0.1:${port}/settings-more-harness.html`;
  let ready = false;
  for (let n = 0; n < 80; n++) {
    try { ready = (await fetch(baseURL)).ok; } catch { /* startup only */ }
    if (ready) break;
    await delay(250);
  }
  assert.ok(ready, 'vite did not become ready');

  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const pageErrors = [];
  const consoleErrors = [];
  const protectedRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('request', (request) => {
    if (/:(8767|8768)(\/|$)/.test(request.url())) protectedRequests.push(request.url());
  });
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const pathName = new URL(request.url()).pathname;
    if (pathName.includes('/__test/set-state')) {
      state = { ...state, ...request.postDataJSON() };
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
      return;
    }
    if (pathName.includes('/native-control/configuration')) {
      const body = request.postDataJSON();
      configurations.push(body.enabled);
      state = { ...state, enabled: false, pendingEnabled: body.enabled };
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, result: { enabled: false, pendingEnabled: body.enabled } }) });
      return;
    }
    if (pathName.includes('/native-control/mode')) {
      const mode = request.postDataJSON().mode;
      modes.push(mode);
      state = { workerId: 'worker-1', mode, generation: 2, error: null, enabled: mode === 'native', pendingEnabled: null };
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, result: state }) });
      return;
    }
    if (pathName.includes('/native-control')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, result: state }) });
      return;
    }
    if (pathName.endsWith('/worker/delayed-restart')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(null) });
      return;
    }
    if (pathName.endsWith(`/sessions/${session.id}`)) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(session) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, result: {} }) });
  });

  await page.goto(baseURL, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.querySelector('[data-testid="topbar"]') !== null, null, { timeout: 10000 });

  // The harness mounts SettingsPopover open; the More disclosure starts collapsed.
  const more = page.getByRole('button', { name: 'More' });
  await more.waitFor({ state: 'visible' });
  assert.equal(await page.getByTestId('settings-terminal-tui-entry').count(), 0, 'Terminal entry must stay collapsed inside More');

  await more.click();
  const entry = page.getByRole('button', { name: 'Terminal TUI' });
  await entry.waitFor({ state: 'visible' });
  assert.equal(await entry.getAttribute('data-terminal-status'), 'upgrade', 'Entry must start in the upgrade state for a non-native session');
  assert.match(await entry.textContent(), /Upgrades/, 'Upgrade state must read as Upgrades');

  // Upgrade → confirm dialog → POST /configuration {enabled:true} → pending idle.
  page.once('dialog', (dialog) => {
    assert.match(dialog.message(), /Upgrade this Codex Session to Terminal TUI control\?/);
    void dialog.accept();
  });
  await entry.click();
  // Node-side condition (the mock records real endpoint calls), so poll here.
  await waitForNode(() => configurations.length > 0);
  assert.deepEqual(configurations, [true], 'More entry must really call the configuration endpoint');
  assert.deepEqual(modes, [], 'A pending-idle upgrade must not switch control mode immediately');

  // Store reflects the acknowledged pending upgrade: label + disabled + hint.
  await page.evaluate(() => window.__setTerminalState({ enabled: false, pendingEnabled: true }));
  await page.waitForFunction(() => {
    const button = document.querySelector('[data-testid="settings-terminal-tui-entry"] button');
    return button?.getAttribute('data-terminal-status') === 'upgrading'
      && button?.hasAttribute('disabled')
      && /pending idle/.test(button?.textContent ?? '');
  }, null, { timeout: 5000, polling: 50 });

  // Idle boundary passes (upgrade acknowledged, no pending flag): the shared
  // TopBar poller must complete the deferred native switch automatically.
  await page.evaluate(() => window.__setTerminalState({ enabled: true, pendingEnabled: null, mode: 'pan' }));
  await page.waitForFunction(() => {
    const button = document.querySelector('[data-testid="settings-terminal-tui-entry"] button');
    return button?.getAttribute('aria-pressed') === 'true'
      && button?.getAttribute('data-terminal-status') === 'enabled';
  }, null, { timeout: 8000, polling: 50 });
  assert.deepEqual(modes, ['native'], 'Idle boundary must auto-enter Terminal TUI via the shared poller');

  // Error retry: an unconfirmed ownership error keeps the entry clickable and
  // a second click really re-issues the mode switch.
  await page.evaluate(() => window.__setTerminalState({
    mode: 'switching', error: 'Control ownership unconfirmed. Retry the switch.', enabled: true,
  }));
  await page.waitForFunction(() => {
    const button = document.querySelector('[data-testid="settings-terminal-tui-entry"] button');
    return button?.getAttribute('data-terminal-status') === 'error'
      && button?.getAttribute('title') === 'Terminal TUI — control error; retry'
      && !button?.hasAttribute('disabled');
  }, null, { timeout: 5000, polling: 20 });
  await entry.click();
  await waitForNode(() => modes.length === 2);

  // Desktop regression: the More entry disappears on desktop viewports
  // (the top bar hosts the entry there instead).
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.waitForTimeout(300);
  assert.equal(await page.getByTestId('settings-terminal-tui-entry').count(), 0, 'Desktop must not host the mobile More entry');
  assert.equal(await page.getByRole('button', { name: 'Terminal TUI' }).count(), 1, 'Desktop top bar keeps the Terminal TUI entry');

  assert.deepEqual(pageErrors, [], `Browser errors: ${pageErrors.join('; ')}`);
  assert.deepEqual(protectedRequests, [], `Unexpected protected-port requests: ${protectedRequests.join(', ')}`);
  console.log('settings-terminal-entry e2e: OK', JSON.stringify({ configurations, modes }));
} finally {
  await browser?.close();
  await server.close();
  await delay(300);
}
