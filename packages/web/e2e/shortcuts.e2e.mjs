/* global Buffer, ClipboardEvent, DataTransfer, KeyboardEvent, URL, clearTimeout, console, document, fetch, localStorage, process, setTimeout */

/* Real Chromium keyboard E2E for the configurable Shortcuts settings tab.
 *
 * Runs against the real FastAPI app (packages/web/e2e/server.py), a deterministic
 * local fake CLI, and an isolated runtime/port. It never uses 8767/8768 or the
 * practical data directories.
 */

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

const baseURL = process.env.PAN_E2E_BASE_URL || '';
if (!baseURL) throw new Error('PAN_E2E_BASE_URL is required');
const port = Number(new URL(baseURL).port);
if (port === 8767 || port === 8768) throw new Error(`refusing protected port ${port}`);
const runtime = process.env.PAN_E2E_RUNTIME || path.resolve('test-results/pan-e2e-shortcuts');
const artifacts = path.join(runtime, 'artifacts');
await fs.mkdir(artifacts, { recursive: true });

const results = [];
const evidence = {};

async function api(method, apiPath, body) {
  const response = await fetch(baseURL + apiPath, {
    method,
    // Terminal POSTs require the exact same-origin allowlist check; Node fetch
    // does not add Origin automatically, so send the browser-equivalent origin.
    headers: {
      Origin: baseURL,
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let json;
  try { json = text ? JSON.parse(text) : {}; } catch { json = { raw: text }; }
  if (!response.ok) {
    throw new Error(`${method} ${apiPath}: HTTP ${response.status} ${text.slice(0, 500)}`);
  }
  return json;
}

async function createSession(name) {
  const created = await api('POST', '/api/sessions', {
    name,
    adapter: 'cbc',
    model: 'deepseek-v4-flash',
    permissionMode: 'bypassPermissions',
    outputMode: 'stream',
    // The isolated E2E checkout has no real plugin manifests; an explicit
    // empty list avoids inheriting the user's default "pan" MCP plugin.
    mcpServers: [],
  });
  if (!created || typeof created.id !== 'string' || !created.id) {
    throw new Error(`Session creation failed: ${JSON.stringify(created)}`);
  }
  return created;
}

async function setShortcuts(shortcuts) {
  await api('PUT', '/api/settings/ui', { shortcuts });
}

async function resetShortcuts() {
  await setShortcuts({});
}

async function cleanupTerminals() {
  try {
    const response = await api('GET', '/api/terminals');
    for (const terminal of response.result || []) {
      const id = terminal.terminal_id;
      if (!id) continue;
      await api('POST', `/api/terminals/${encodeURIComponent(id)}/close`, {}).catch(() => {});
      await api('POST', `/api/terminals/${encodeURIComponent(id)}/remove`, {}).catch(() => {});
    }
  } catch {
    // No terminal route state to clean in this run.
  }
}

async function waitFor(read, check, timeout = 10000, label = 'condition') {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    last = await read();
    if (check(last)) return last;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`timed out waiting for ${label}: ${JSON.stringify(last)}`);
}

async function openPage(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 1,
    permissions: ['clipboard-read', 'clipboard-write'],
  });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.setDefaultNavigationTimeout(20000);
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(String(error)));
  await page.goto(`${baseURL}/react/`);
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  return { context, page, pageErrors };
}

async function reloadApp(page) {
  const settings = page.waitForResponse(
    (response) => response.url().includes('/api/settings/ui') &&
      response.request().method() === 'GET',
    { timeout: 15000 },
  );
  await page.reload();
  await settings;
  // Works in both the expanded sidebar and the collapsed rail (where Session
  // cards are intentionally not rendered).
  await page.locator('button[title="App settings"]').first().waitFor({ state: 'visible' });
  // The settings store applies the GET asynchronously; let the sanitized
  // shortcut map land before the caller presses a newly bound key.
  await page.waitForTimeout(400);
}

async function gotoChat(page) {
  await page.goto(`${baseURL}/react/`);
  await page.locator('button[title="App settings"]').first().waitFor({ state: 'visible' });
  await page.waitForTimeout(300);
}

async function selectSession(page, name) {
  const filter = page.getByPlaceholder('Filter...').first();
  const hasFilter = await filter.count() > 0;
  if (hasFilter) {
    await filter.fill(name);
    await page.waitForTimeout(100);
  }
  const card = page.locator('[data-session-card-id]').filter({ hasText: name }).first();
  await card.waitFor({ state: 'visible' });
  await card.scrollIntoViewIfNeeded();
  await card.click();
  if (hasFilter) {
    await filter.fill('');
    await filter.blur();
    await page.waitForTimeout(50);
  }
  return card;
}

async function openShortcuts(page) {
  await page.locator('button[title="App settings"]:visible').first().click();
  await page.getByRole('tab', { name: 'Shortcuts' }).click();
  await page.locator('[data-testid="shortcuts-settings"]').waitFor({ state: 'visible' });
}

async function closeSettings(page) {
  await page.keyboard.press('Escape');
  await page.locator('[data-testid="shortcuts-settings"]').waitFor({ state: 'hidden' });
}

function shortcutRow(page, actionId) {
  return page.locator(`[data-shortcut-action="${actionId}"]`);
}

async function recordShortcut(page, actionId, keys) {
  const row = shortcutRow(page, actionId);
  await row.scrollIntoViewIfNeeded();
  await row.locator(`[data-shortcut-record="${actionId}"]`).click();
  await page.keyboard.press(keys);
}

async function bindingText(page, actionId) {
  return page.locator(`[data-shortcut-binding="${actionId}"]`).innerText();
}

async function sidebarCollapsed(page) {
  return page.evaluate(() => localStorage.getItem('pan:sidebarCollapsed'));
}

function withTimeout(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`timeout: ${label}`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

async function clearComposer(page) {
  const composer = page.getByTestId('rich-text-composer');
  await composer.click();
  await page.waitForTimeout(300);
  for (const button of await page.locator('button[aria-label^="取消附件 "]').all()) {
    await button.click();
    await page.waitForTimeout(50);
  }
  await page.keyboard.press('Control+A');
  await page.keyboard.press('Backspace');
  await waitFor(() => composer.innerText(), (value) => value.trim() === '',
    4000, 'composer clear');
}

function pasteComposerText(page, text) {
  return page.evaluate((value) => {
    const element = document.querySelector('[data-testid="rich-text-composer"]');
    const dataTransfer = new DataTransfer();
    dataTransfer.setData('text/plain', value);
    const event = new ClipboardEvent('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'clipboardData', { value: dataTransfer });
    element.dispatchEvent(event);
  }, text);
}

function trackWrites(page) {
  const writes = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.method() === 'GET') return;
    if (
      url.pathname.endsWith('/queue') ||
      url.pathname.includes('/queue/') ||
      url.pathname.endsWith('/report-to-manager') ||
      url.pathname.endsWith('/msg-bridge')
    ) {
      writes.push({
        method: request.method(),
        path: url.pathname,
        body: request.postData(),
      });
    }
  });
  return writes;
}

async function writeResults() {
  await fs.writeFile(path.join(runtime, 'browser-results.json'),
    JSON.stringify({ baseURL, port, evidence, results }, null, 2), 'utf8');
}

async function runCase(browser, name, body) {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  let context;
  let page;
  let pageErrors = [];
  const started = new Date().toISOString();
  try {
    await resetShortcuts();
    ({ context, page, pageErrors } = await openPage(browser));
    await body({ context, page, pageErrors });
    assert.deepEqual(pageErrors, [], `page errors: ${pageErrors.join('\n')}`);
    await page.screenshot({ path: path.join(artifacts, `${slug}.png`), fullPage: true });
    results.push({ name, status: 'passed', started, screenshot: `${slug}.png` });
    console.log(`[shortcuts-e2e] PASS ${name}`);
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    if (page) {
      await page.screenshot({ path: path.join(artifacts, `${slug}-failure.png`), fullPage: true })
        .catch(() => {});
    }
    results.push({
      name,
      status: 'failed',
      started,
      error: message,
      pageErrors,
      screenshot: `${slug}-failure.png`,
    });
    console.log(`[shortcuts-e2e] FAIL ${name}: ${message.split('\n')[0]}`);
  } finally {
    await context?.close();
    await writeResults();
  }
}

const suffix = Date.now();
const manager = await createSession(`Shortcut-Manager-${suffix}`);
const target = await createSession(`Shortcut-Target-${suffix}`);
const other = await createSession(`Shortcut-Other-${suffix}`);
await fs.mkdir(target.workdir, { recursive: true });
await fs.writeFile(path.join(target.workdir, 'shortcut-editor.md'),
  '# Shortcut editor\n\nreal Monaco target\n', 'utf8');
await api('POST', '/api/report-subscribe', {
  managerId: manager.id,
  sessionId: target.id,
});
let targetDetail = await api('GET', `/api/sessions/${target.id}?view=metadata`);
if (!targetDetail.msgBridgeEnabled) {
  await api('PUT', `/api/sessions/${target.id}/msg-bridge`, {
    enabled: true,
    expectedEnabled: false,
  });
}
targetDetail = await api('GET', `/api/sessions/${target.id}?view=metadata`);
assert.equal(targetDetail.reportsToManager, true);
assert.equal(targetDetail.msgBridgeEnabled, true);

const browser = await chromium.launch({ headless: true });
try {
  await runCase(browser, 'record persist and execute general shortcut', async ({ page }) => {
    await openShortcuts(page);
    await recordShortcut(page, 'app.toggleSidebar', 'Control+Shift+F10');
    assert.equal(await bindingText(page, 'app.toggleSidebar'), 'Ctrl+Shift+F10');
    await closeSettings(page);
    assert.equal(await sidebarCollapsed(page), null);
    await page.keyboard.press('Control+Shift+F10');
    await waitFor(() => sidebarCollapsed(page), (value) => value === '1', 3000, 'sidebar collapse');
    await reloadApp(page);
    await openShortcuts(page);
    assert.equal(await bindingText(page, 'app.toggleSidebar'), 'Ctrl+Shift+F10');
  });

  await runCase(browser, 'reject reserved and conflicting bindings', async ({ page }) => {
    await openShortcuts(page);
    await recordShortcut(page, 'app.toggleSidebar', 'Control+k');
    await page.locator('[data-testid="shortcut-recorder-error"]').waitFor();
    assert.match(await page.locator('[data-testid="shortcut-recorder-error"]').innerText(), /Open command palette/);
    assert.equal(await bindingText(page, 'app.toggleSidebar'), 'Ctrl+B');
    await recordShortcut(page, 'app.toggleSidebar', 'Control+y');
    await page.locator('[data-testid="shortcut-recorder-error"]').waitFor();
    assert.match(await page.locator('[data-testid="shortcut-recorder-error"]').innerText(), /reserved/);
    assert.equal(await bindingText(page, 'app.toggleSidebar'), 'Ctrl+B');
  });

  await runCase(browser, 'dynamic plugin panels add remove and binding isolation', async ({ page }) => {
    let panels = [
      {
        pluginId: 'alpha', id: 'one', title: 'Alpha One',
        renderer: 'account-switcher/v1', path: '/plugins/alpha/one', actions: {},
      },
      {
        pluginId: 'beta', id: 'two', title: 'Beta Two',
        renderer: 'account-switcher/v1', path: '/plugins/beta/two', actions: {},
      },
    ];
    await page.route('**/api/plugins/panels', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ panels }) }));
    await reloadApp(page);

    await openShortcuts(page);
    await recordShortcut(page, 'panel:plugin:alpha/one', 'Control+Shift+F1');
    await recordShortcut(page, 'panel:plugin:beta/two', 'Control+Shift+F2');
    await closeSettings(page);

    await page.keyboard.press('Control+Shift+F1');
    await page.waitForURL(/\/plugins\/alpha\/one/);
    await gotoChat(page);

    panels = [panels[1]];
    await openShortcuts(page);
    await page.locator('[data-shortcut-action="panel:plugin:alpha/one"]')
      .waitFor({ state: 'detached' });
    assert.equal(await shortcutRow(page, 'panel:plugin:alpha/one').count(), 0);
    assert.equal(await bindingText(page, 'panel:plugin:beta/two'), 'Ctrl+Shift+F2');
    await closeSettings(page);

    const before = page.url();
    await page.keyboard.press('Control+Shift+F1');
    await page.waitForTimeout(400);
    assert.equal(page.url(), before);
    await page.keyboard.press('Control+Shift+F2');
    await page.waitForURL(/\/plugins\/beta\/two/);
    await gotoChat(page);

    panels = [
      panels[0],
      {
        pluginId: 'alpha', id: 'one', title: 'Alpha One',
        renderer: 'account-switcher/v1', path: '/plugins/alpha/one', actions: {},
      },
    ];
    await openShortcuts(page);
    await page.locator('[data-shortcut-action="panel:plugin:alpha/one"]')
      .waitFor({ state: 'visible' });
    assert.equal(await bindingText(page, 'panel:plugin:alpha/one'), 'Ctrl+Shift+F1');
  });

  await runCase(browser, 'avoid IME composer editor and terminal', async ({ context, page }) => {
    await selectSession(page, target.name);
    const composer = page.getByTestId('rich-text-composer');
    await composer.click();
    await page.keyboard.press('Control+b');
    assert.equal(await sidebarCollapsed(page), null);
    await page.keyboard.press('Control+k');
    assert.equal(await page.getByPlaceholder('Search commands, sessions...').count(), 0);

    const cdp = await context.newCDPSession(page);
    let realImeComposition = true;
    try {
      await cdp.send('Input.imeSetComposition', {
        text: 'に', selectionStart: 1, selectionEnd: 1,
      });
    } catch {
      realImeComposition = false;
    }
    await page.keyboard.press('Control+b');
    assert.equal(await sidebarCollapsed(page), null);
    await page.evaluate(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'b', code: 'KeyB', ctrlKey: true, isComposing: true, bubbles: true, cancelable: true,
      }));
    });
    assert.equal(await sidebarCollapsed(page), null);
    if (realImeComposition) {
      await cdp.send('Input.imeSetComposition', { text: '', selectionStart: 0, selectionEnd: 0 });
    }
    evidence.imeAvoidance = { realImeComposition, syntheticComposingGuard: true };

    await page.getByRole('link', { name: 'Editor', exact: true }).click();
    await page.waitForURL(/\/react\/editor/);
    await page.getByText('shortcut-editor.md', { exact: true }).first().waitFor({ state: 'visible' });
    await page.getByText('shortcut-editor.md', { exact: true }).first().click();
    // Markdown opens in Preview by default; switch to the source editor so the
    // shortcut target is the real Monaco textarea.
    await page.getByTitle('Edit', { exact: true }).click();
    await page.locator('.monaco-editor textarea').first().waitFor({ state: 'attached' });
    await page.locator('.monaco-editor textarea').first().focus();
    await page.keyboard.press('Control+k');
    assert.equal(await page.getByPlaceholder('Search commands, sessions...').count(), 0);
    await page.keyboard.press('Control+b');
    assert.equal(await sidebarCollapsed(page), null);

    // Terminal lives on the second nav page; navigate directly instead of
    // depending on the visual carousel page. Create an isolated terminal in
    // the E2E runtime (PAN_TERMINALS_DIR) and focus its real xterm textarea.
    let terminalId = null;
    let terminalEngineStarted = false;
    try {
      const createdTerminal = await api('POST', '/api/terminals', {
        rows: 24, cols: 80, cwd: target.workdir,
      });
      terminalId = createdTerminal.result?.terminal_id ?? null;
      terminalEngineStarted = Boolean(terminalId);
    } catch {
      // The isolated checkout has no terminal emulator sidecar, so engine
      // start fails. The failed create still leaves a real registry record;
      // select it to mount the real xterm DOM (WebSocket may be disconnected).
      const listed = await api('GET', '/api/terminals');
      terminalId = (listed.result || [])
        .find((record) => record.terminal_id)?.terminal_id ?? null;
    }
    assert.ok(terminalId, 'no terminal record available for xterm focus');
    await page.goto(`${baseURL}/react/terminals`);
    await page.waitForURL(/\/react\/terminals/);
    await page.getByLabel('选择终端').selectOption(terminalId);
    const terminalInput = page.locator('.xterm-helper-textarea').first();
    await terminalInput.waitFor({ state: 'attached' });
    await terminalInput.focus();
    await page.keyboard.press('Control+k');
    assert.equal(await page.getByPlaceholder('Search commands, sessions...').count(), 0);
    await page.keyboard.press('Control+b');
    assert.equal(await sidebarCollapsed(page), null);
    evidence.terminalAvoidance = { terminalAvailable: true, terminalId, terminalEngineStarted };
    if (terminalEngineStarted) {
      await api('POST', `/api/terminals/${encodeURIComponent(terminalId)}/close`, {});
      await api('POST', `/api/terminals/${encodeURIComponent(terminalId)}/remove`, {});
    }
  });

  await runCase(browser, 'no session disables session and queue actions', async ({ page }) => {
    await setShortcuts({
      'session.toggleStopReport': 'Mod+Shift+F8',
      'session.toggleNotification': 'Mod+Shift+F9',
      'queue.enqueueLocked': 'Mod+Shift+F7',
      'queue.lockAll': 'Mod+Shift+F4',
      'queue.pauseAll': 'Mod+Shift+F6',
    });
    await reloadApp(page);
    const writes = trackWrites(page);
    for (const keys of ['Control+Shift+F8', 'Control+Shift+F9', 'Control+Shift+F7',
      'Control+Shift+F4', 'Control+Shift+F6']) {
      await page.keyboard.press(keys);
    }
    await page.waitForTimeout(600);
    assert.deepEqual(writes, []);
  });

  await runCase(browser, 'locked enqueue failure preserves draft', async ({ page }) => {
    await setShortcuts({ 'queue.enqueueLocked': 'Mod+Shift+F7' });
    await reloadApp(page);
    await selectSession(page, target.name);
    await clearComposer(page);
    const composer = page.getByTestId('rich-text-composer');
    await composer.click();
    await page.keyboard.type('keep this draft');
    let posts = 0;
    await page.route(`**/api/sessions/${target.id}/queue`, async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      posts += 1;
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({ ok: false, error: { message: 'locked rejected' } }),
      });
    });
    await page.keyboard.press('Control+Shift+F7');
    await waitFor(() => posts, (value) => value === 1, 5000, 'locked enqueue rejection');
    await waitFor(() => composer.innerText(), (value) => value.includes('keep this draft'),
      5000, 'draft restore');
    await page.waitForTimeout(300);
    assert.equal(posts, 1, 'rejected write must not be retried');
  });

  await runCase(browser, 'locked enqueue preserves attachment and URL order', async ({ page }) => {
    await setShortcuts({ 'queue.enqueueLocked': 'Mod+Shift+F7' });
    await reloadApp(page);
    await selectSession(page, target.name);
    await clearComposer(page);
    const composer = page.getByTestId('rich-text-composer');
    await composer.click();
    await page.keyboard.type('prefix ');

    await page.getByLabel('添加附件').click();
    const chooserPromise = page.waitForEvent('filechooser');
    await page.getByText('客户端附件', { exact: true }).first().click();
    const chooser = await chooserPromise;
    await chooser.setFiles({
      name: 'locked-e2e.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('locked attachment body'),
    });
    await page.locator('[data-testid="draggable-attachment-chip"]')
      .filter({ hasText: 'locked-e2e.txt' }).waitFor({ state: 'visible' });

    await pasteComposerText(page, 'https://locked.test/a');
    await page.locator('[data-composer-url]').first().waitFor({ state: 'visible' });

    await page.keyboard.press('Control+Shift+F7');
    const queue = await waitFor(
      () => api('GET', `/api/sessions/${target.id}/queue`),
      (value) => (value.items || []).some((item) => (item.text || '').includes('locked.test/a')),
      10000,
      'locked queue item',
    );
    const item = queue.items.find((candidate) => (candidate.text || '').includes('locked.test/a'));
    assert.equal(item.meta.locked, true);
    assert.match(item.text, /prefix/);
    assert.match(item.text, /\[https:\/\/locked\.test\/a\]\(https:\/\/locked\.test\/a\)/);
    assert.match(item.text, /locked-e2e\.txt/);
    const parts = item.parts || [];
    const urlIndex = parts.findIndex((part) => part.type === 'text' && part.text.includes('locked.test/a'));
    const attachmentIndex = parts.findIndex((part) => part.type === 'attachment' &&
      part.displayName === 'locked-e2e.txt');
    assert.ok(urlIndex >= 0, `missing URL part: ${JSON.stringify(parts)}`);
    assert.ok(attachmentIndex > urlIndex,
      `attachment must follow URL node: ${JSON.stringify(parts)}`);
    assert.ok(parts[attachmentIndex].attachmentId, 'attachment registry id is required');
  });

  await runCase(browser, 'long press toggles once', async ({ context, page }) => {
    await selectSession(page, target.name);
    const cdp = await context.newCDPSession(page);
    const key = {
      modifiers: 2,
      windowsVirtualKeyCode: 66,
      nativeVirtualKeyCode: 66,
      code: 'KeyB',
      key: 'b',
    };
    await cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...key });
    await cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...key, autoRepeat: true });
    await cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...key, autoRepeat: true });
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', ...key });
    await page.waitForTimeout(300);
    assert.equal(await sidebarCollapsed(page), '1');
  });

  await runCase(browser, 'switch session race keeps target', async ({ page }) => {
    await setShortcuts({ 'queue.enqueueLocked': 'Mod+Shift+F7' });
    await reloadApp(page);
    await selectSession(page, target.name);
    await clearComposer(page);
    const composer = page.getByTestId('rich-text-composer');
    await composer.click();
    await page.keyboard.type('race draft');

    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    let started;
    const requestStarted = new Promise((resolve) => { started = resolve; });
    let postedSession = null;
    await page.route(`**/api/sessions/${target.id}/queue`, async (route) => {
      if (route.request().method() === 'POST') {
        postedSession = target.id;
        started();
        await gate;
      }
      await route.continue();
    });

    await page.keyboard.press('Control+Shift+F7');
    await withTimeout(requestStarted, 10000, 'queue request start');
    await selectSession(page, other.name);
    release();
    await page.waitForTimeout(800);
    assert.equal(postedSession, target.id);
    assert.equal((await page.getByTestId('rich-text-composer').innerText()).trim(), '');

    const queue = await waitFor(
      () => api('GET', `/api/sessions/${target.id}/queue`),
      (value) => (value.items || []).some((item) => item.text === 'race draft'),
      10000,
      'target queue item after switch',
    );
    const item = queue.items.find((candidate) => candidate.text === 'race draft');
    assert.equal(item.meta.locked, true);
  });

  await runCase(browser, 'queue expand lock all and pause semantics', async ({ page }) => {
    await setShortcuts({
      'queue.togglePanel': 'Mod+Shift+F3',
      'queue.lockAll': 'Mod+Shift+F4',
      'queue.pauseAll': 'Mod+Shift+F6',
    });
    await reloadApp(page);
    await selectSession(page, target.name);
    await page.keyboard.press('Control+Shift+F3');
    await page.getByLabel('Lock all queued messages').first().waitFor({ state: 'visible' });
    await waitFor(() => page.getByLabel('Pause everything').first().isEnabled(),
      (value) => value === true, 5000, 'queue pause control loaded');

    const writes = trackWrites(page);
    await page.keyboard.press('Control+Shift+F4');
    await waitFor(
      () => writes.filter((entry) => entry.path.endsWith(`/sessions/${target.id}/queue/locks`)),
      (value) => value.length === 1,
      5000,
      'lock-all request',
    );
    assert.deepEqual(JSON.parse(writes.find((entry) => entry.path.endsWith('/queue/locks')).body),
      { locked: true });
    assert.equal(writes.filter((entry) => entry.path.endsWith('/queue/paused')).length, 0,
      'Lock all must not pause the queue');
    let detail = await api('GET', `/api/sessions/${target.id}?view=metadata`);
    assert.equal(detail.queuePaused, false);
    assert.equal(detail.workerStatus, null, 'Lock all must not stop the Worker');
    await waitFor(() => page.getByLabel('Pause everything').first().isEnabled(),
      (value) => value === true, 5000, 'pause control enabled after lock all');

    await page.keyboard.press('Control+Shift+F6');
    await waitFor(
      () => writes.filter((entry) => entry.path.endsWith('/queue/paused')),
      (value) => value.length === 1,
      5000,
      'pause request',
    );
    assert.deepEqual(JSON.parse(writes.find((entry) => entry.path.endsWith('/queue/paused')).body),
      { paused: true });
    detail = await api('GET', `/api/sessions/${target.id}?view=metadata`);
    assert.equal(detail.queuePaused, true);
    await waitFor(() => page.getByLabel('Resume everything').first().isEnabled(),
      (value) => value === true, 5000, 'resume control enabled');
    await page.keyboard.press('Control+Shift+F6');
    await waitFor(
      () => api('GET', `/api/sessions/${target.id}?view=metadata`),
      (value) => value.queuePaused === false,
      5000,
      'resume everything',
    );
  });

  await runCase(browser, 'stop report and bell persist across reload', async ({ page }) => {
    await setShortcuts({
      'session.toggleStopReport': 'Mod+Shift+F8',
      'session.toggleNotification': 'Mod+Shift+F9',
    });
    await reloadApp(page);
    await selectSession(page, target.name);
    const card = page.locator(`[data-session-card-id="${target.id}"]`);
    const stop = card.locator('[data-quick-action="stopReport"]');
    const bell = card.locator('[data-quick-action="notification"]');
    // Card semantics: stopReport aria-pressed=true means reports are STOPPED.
    assert.equal(await stop.getAttribute('aria-pressed'), 'false');
    assert.equal(await bell.getAttribute('aria-pressed'), 'true');

    await page.keyboard.press('Control+Shift+F8');
    await waitFor(() => stop.getAttribute('aria-pressed'), (value) => value === 'true',
      5000, 'stop report on');
    await page.keyboard.press('Control+Shift+F9');
    await waitFor(() => bell.getAttribute('aria-pressed'), (value) => value === 'false',
      5000, 'bell off');

    await reloadApp(page);
    await selectSession(page, target.name);
    const reloadedCard = page.locator(`[data-session-card-id="${target.id}"]`);
    assert.equal(await reloadedCard.locator('[data-quick-action="stopReport"]').getAttribute('aria-pressed'),
      'true');
    assert.equal(await reloadedCard.locator('[data-quick-action="notification"]').getAttribute('aria-pressed'),
      'false');

    let detail = await api('GET', `/api/sessions/${target.id}?view=metadata`);
    assert.equal(detail.reportsToManager, false);
    assert.equal(detail.msgBridgeEnabled, false);

    await page.keyboard.press('Control+Shift+F8');
    await waitFor(
      () => reloadedCard.locator('[data-quick-action="stopReport"]').getAttribute('aria-pressed'),
      (value) => value === 'false', 5000, 'stop report restored');
    await page.keyboard.press('Control+Shift+F9');
    await waitFor(
      () => reloadedCard.locator('[data-quick-action="notification"]').getAttribute('aria-pressed'),
      (value) => value === 'true', 5000, 'bell restored');
    detail = await api('GET', `/api/sessions/${target.id}?view=metadata`);
    assert.equal(detail.reportsToManager, true);
    assert.equal(detail.msgBridgeEnabled, true);
  });
} finally {
  await cleanupTerminals();
  await browser.close();
  await writeResults();
}

const failed = results.filter((result) => result.status === 'failed');
console.log(JSON.stringify({
  port,
  evidence,
  passed: results.length - failed.length,
  failed: failed.length,
  results,
}, null, 2));
if (failed.length > 0) process.exitCode = 1;
