/* global window, document, URL, fetch, process, console, MouseEvent */
/**
 * Real-backend E2E for the notification -> workspace jump.
 *
 * Real React production bundle + isolated real Pan backend (dynamic port,
 * disposable runtime root, deterministic fake CLI provider, system
 * notification channel disabled in every seeded session). The browser
 * `Notification` display layer is stubbed: instances are captured and the
 * app-attached `onclick` handler is invoked exactly as a delivered click would
 * invoke it. OS-level notification display/focus remains a browser/OS boundary
 * and is explicitly NOT asserted here.
 *
 * Covered (desktop + mobile viewports): a notification click and a cold
 * `?session=` link select the target Session AND its effective workspace at the
 * same time; a managed child inherits its root's workspace; an ungrouped
 * session selects the virtual 'no workspace' scope (no workspace selected); a
 * user workspace switch (A -> B -> A) during a slow lookup wins; unrelated
 * query params survive; no external requests / no page errors.
 */
import assert from 'node:assert/strict';
import { spawn, spawnSync, execSync } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import net from 'node:net';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout } from 'node:timers';
import { chromium } from '@playwright/test';

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, '..');
const repoRoot = path.resolve(here, '../../..');
const runtime = process.env.PAN_WORKSPACE_E2E_RUNTIME
  || path.join(webRoot, 'test-results', `notify-ws-${Date.now()}`);
const outDir = process.env.PAN_WORKSPACE_E2E_OUT
  || path.join(repoRoot, 'audit', 'notification-workspace-jump');
await fs.mkdir(runtime, { recursive: true });
await fs.mkdir(outDir, { recursive: true });

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const MISSING_TOAST = '通知对应的会话不存在或已被删除，无法跳转';
const LIST_UNAVAILABLE_TOAST = '会话列表暂时无法加载，未能打开该会话';
const WORKSPACE_UNAVAILABLE_TOAST = '工作区列表暂时无法加载，未能确定会话的工作区';

async function firstExisting(paths) {
  for (const candidate of paths) {
    if (!candidate) continue;
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      // try next
    }
  }
  return null;
}

const python = await firstExisting([
  process.env.PAN_WORKSPACE_E2E_PYTHON,
  process.env.PAN_E2E_PYTHON,
  'D:/project/Pan-main/.venv/Scripts/python.exe',
  'D:/project/Pan/.venv/Scripts/python.exe',
]);
assert.ok(python, 'no Pan python interpreter found (set PAN_WORKSPACE_E2E_PYTHON)');

async function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

const port = await freePort();
assert.ok(![8767, 8768].includes(port), `owned dynamic port must not be protected: ${port}`);
const base = `http://127.0.0.1:${port}`;
const chatUrl = `${base}/react/`;

async function api(route, init) {
  const res = await fetch(base + route, init);
  assert.equal(res.status, 200, `${route} -> ${res.status}`);
  const data = await res.json();
  if (data && typeof data === 'object' && 'error' in data && data.error) {
    throw new Error(`${route}: ${JSON.stringify(data.error)}`);
  }
  return data;
}

async function poll(read, test, label, timeout = 20000) {
  let last;
  for (const end = Date.now() + timeout; Date.now() < end; await sleep(80)) {
    last = await read();
    if (test(last)) return last;
  }
  throw new Error(`${label}: last=${JSON.stringify(last)?.slice(0, 800)}`);
}

/** Kill any worker first, then retry DELETE until the server really drops it. */
async function deleteSessionForGood(sessionId) {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    try { await fetch(`${base}/api/sessions/${sessionId}/worker/kill`, { method: 'POST' }); } catch { /* none */ }
    try { await fetch(`${base}/api/sessions/${sessionId}`, { method: 'DELETE' }); } catch { /* gone */ }
    await sleep(300);
    const payload = await api('/api/sessions?summary=1');
    if (!(payload.sessions || []).some((entry) => entry.id === sessionId)) return true;
  }
  return false;
}

/** Wait for the client to drop a session (the delete broadcast refreshes it). */
async function dropFromClient(page, sessionId) {
  await poll(() => state(page), (s) => s && !(s.list || []).includes(sessionId),
    `client dropped ${sessionId}`, 15000);
}

/**
 * Start watching for a jump's feedback toast (deleted / session-list or
 * workspace-list unavailable). The client's delete broadcast triggers a refresh
 * that can supersede the jump's own authoritative fetch, so the existing
 * contract legitimately reports any of these — all honest feedback. The
 * invariant that always holds is that an unapplied target is never selected.
 */
function watchDeletionFeedback(page) {
  return page.waitForFunction(
    (texts) => [...document.querySelectorAll('.toast-enter')]
      .some((node) => texts.some((text) => (node.textContent || '').includes(text))),
    [MISSING_TOAST, LIST_UNAVAILABLE_TOAST, WORKSPACE_UNAVAILABLE_TOAST],
    { timeout: 30000 },
  ).then(() => true).catch(() => false);
}

const results = [];
async function check(name, fn) {
  try {
    const detail = await fn();
    results.push({ name, ok: true, ...(detail ? { detail } : {}) });
    console.log(`[PASS] ${name}${detail ? ` ${JSON.stringify(detail)}` : ''}`);
  } catch (error) {
    results.push({ name, ok: false, error: String(error?.message || error) });
    console.log(`[FAIL] ${name}: ${error?.message || error}`);
  }
}

const pageErrors = new Map();
const foreignRequests = [];
const blockedFontRequests = [];
const FONT_HOSTS = /^https?:\/\/fonts\.(googleapis|gstatic)\.com\//;
function watchPage(page) {
  pageErrors.set(page, []);
  page.on('pageerror', (error) => pageErrors.get(page)?.push(error.message));
  page.on('request', (request) => {
    let parsed;
    try { parsed = new URL(request.url()); } catch { return; }
    if (!parsed.protocol.startsWith('http')) return;
    if (FONT_HOSTS.test(request.url())) {
      blockedFontRequests.push(request.url());
      return;
    }
    if (parsed.hostname === '127.0.0.1' && parsed.port === String(port)) return;
    foreignRequests.push(`${request.method()} ${request.url()}`);
  });
}

const NOTIFICATION_STUB = () => {
  window.__panNotifications = [];
  window.__panUnhandled = [];
  window.addEventListener('unhandledrejection', (event) => {
    window.__panUnhandled.push(String(event.reason?.stack || event.reason));
  });
  class PanE2ENotification {
    static permission = 'granted';
    static requestPermission = async () => 'granted';
    constructor(title, options) {
      this.title = title;
      this.options = options || {};
      this.onclick = null;
      this.closed = false;
      window.__panNotifications.push(this);
    }
    close() { this.closed = true; }
  }
  Object.defineProperty(window, 'Notification', {
    value: PanE2ENotification, configurable: true, writable: true,
  });
};

async function state(page) {
  return page.evaluate(() => {
    const session = window.__panSessionStore?.getState?.();
    const ui = window.__panUIStore?.getState?.();
    return {
      id: session?.currentSessionId ?? null,
      list: (session?.sessions ?? []).map((entry) => entry.id),
      loaded: session ? !session.sessionsLoading : false,
      workspace: ui?.activeWorkspaceId ?? null,
    };
  });
}

async function cardCount(page, name) {
  return page.locator('[data-session-card-id]').filter({ hasText: name }).count();
}

async function selectSessionByName(page, name) {
  await page.locator('[data-session-card-id]').filter({ hasText: name }).first().click();
}

async function sendFromComposer(page, text) {
  const input = page.locator('[contenteditable="true"]').first();
  await input.fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}

async function waitForNotification(page, titlePart) {
  await page.waitForFunction(
    (needle) => (window.__panNotifications || [])
      .some((notification) => String(notification.title || '').includes(needle)),
    titlePart,
    { timeout: 20000 },
  );
}

async function clickNotification(page, titlePart) {
  const dispatched = await page.evaluate((needle) => {
    const list = window.__panNotifications || [];
    const target = [...list].reverse()
      .find((notification) => String(notification.title || '').includes(needle));
    if (!target || typeof target.onclick !== 'function') return false;
    target.onclick(new MouseEvent('click'));
    return true;
  }, titlePart);
  assert.ok(dispatched, `notification with '${titlePart}' had an onclick to dispatch`);
}

function searchOf(page) {
  return page.evaluate(() => window.location.search);
}

function pathOf(page) {
  return page.evaluate(() => window.location.pathname);
}

// --- server -----------------------------------------------------------------

const server = spawn(
  python,
  [path.join(here, 'notification-workspace-jump.server.py')],
  {
    cwd: repoRoot,
    windowsHide: true,
    env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime },
    stdio: ['ignore', 'pipe', 'pipe'],
  },
);
const serverLog = createWriteStream(path.join(runtime, 'server.log'));
server.stdout.pipe(serverLog);
server.stderr.pipe(serverLog);
server.on('exit', () => serverLog.end());

async function stopServer() {
  if (server.exitCode !== null) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true });
  } else {
    server.kill('SIGTERM');
  }
  await poll(() => Promise.resolve(server.exitCode), (code) => code !== null, 'server exit', 15000)
    .catch(() => {});
}

let browser;
let summary = {
  ok: false,
  ranAt: new Date().toISOString(),
  worktreeHead: execSync('git rev-parse HEAD', { cwd: repoRoot }).toString().trim(),
  python,
  port,
  runtime,
  results,
};

try {
  const distIndex = path.join(webRoot, 'dist', 'index.html');
  const sourceFile = path.join(webRoot, 'src', 'utils', 'session-deeplink.ts');
  const [distStat, sourceStat] = await Promise.all([fs.stat(distIndex), fs.stat(sourceFile)])
    .catch(() => [null, null]);
  assert.ok(distStat, `missing ${distIndex}; run "vite build" before this E2E`);
  assert.ok(
    distStat.mtimeMs >= sourceStat.mtimeMs,
    'dist is older than session-deeplink.ts; rebuild before this E2E',
  );

  await poll(
    async () => { try { return await api('/api/sessions?summary=1'); } catch { return null; } },
    (data) => data !== null,
    'server ready',
    30000,
  );
  const identity = JSON.parse(await fs.readFile(path.join(runtime, 'server-identity.json'), 'utf8'));
  assert.equal(path.resolve(identity.checkout), repoRoot);
  assert.equal(identity.port, port);
  console.log('server', JSON.stringify(identity));

  const sessionsPayload = await api('/api/sessions?summary=1');
  const nameToId = new Map((sessionsPayload.sessions || []).map((s) => [s.name, s.id]));
  const workspacesPayload = await api('/api/workspaces');
  const wsNameToId = new Map((workspacesPayload.workspaces || []).map((w) => [w.name, w.id]));
  const alphaId = wsNameToId.get('Alpha Lab');
  const betaId = wsNameToId.get('Beta Lab');
  for (const name of ['Alpha One', 'Alpha Two', 'Beta Root', 'Beta Child', 'Loose One', 'Doomed Cold', 'Hot Target']) {
    assert.ok(nameToId.get(name), `seeded session '${name}' is present`);
  }
  assert.ok(alphaId && betaId, 'seeded workspaces Alpha Lab / Beta Lab are present');
  const alphaOne = nameToId.get('Alpha One');
  const alphaTwo = nameToId.get('Alpha Two');
  const betaChild = nameToId.get('Beta Child');
  const looseOne = nameToId.get('Loose One');
  const doomedCold = nameToId.get('Doomed Cold');
  const hotTarget = nameToId.get('Hot Target');

  browser = await chromium.launch({ headless: true });
  // Each check gets its OWN context: localStorage (the persisted active
  // workspace) must not bleed from one scenario into the next.
  async function newIsolatedContext(overrides = {}) {
    const isolated = await browser.newContext({ viewport: { width: 1440, height: 900 }, ...overrides });
    await isolated.addInitScript(NOTIFICATION_STUB);
    await isolated.route(FONT_HOSTS, (route) => route.abort());
    return isolated;
  }

  // --- desktop -----------------------------------------------------------------

  await check('cold ?session selects a workspace member AND its workspace', async () => {
    const context = await newIsolatedContext();
    const page = await context.newPage();
    watchPage(page);
    await page.goto(`${chatUrl}?panE2E=1&session=${alphaOne}`, { waitUntil: 'domcontentloaded' });
    await poll(() => state(page), (s) => s?.id === alphaOne, 'Alpha One selected');
    const snapshot = await state(page);
    assert.equal(snapshot.workspace, alphaId, `active workspace is Alpha Lab, got ${snapshot.workspace}`);
    // The list is scoped to Alpha Lab: Alpha cards present, others gone.
    await poll(() => cardCount(page, 'Alpha One'), (n) => n === 1, 'Alpha One card visible');
    assert.equal(await cardCount(page, 'Alpha Two'), 1, 'Alpha Two card visible');
    assert.equal(await cardCount(page, 'Beta Root'), 0, 'Beta Root hidden');
    assert.equal(await cardCount(page, 'Loose One'), 0, 'Loose One hidden');
    assert.ok(!(await searchOf(page)).includes('session='), 'session param consumed');
    await context.close();
    return { workspace: snapshot.workspace };
  });

  await check('managed child inherits its root workspace (not its own empty id)', async () => {
    const context = await newIsolatedContext();
    const page = await context.newPage();
    watchPage(page);
    await page.goto(`${chatUrl}?panE2E=1&session=${betaChild}`, { waitUntil: 'domcontentloaded' });
    await poll(() => state(page), (s) => s?.id === betaChild, 'Beta Child selected');
    const snapshot = await state(page);
    assert.equal(snapshot.workspace, betaId, `inherited Beta Lab, got ${snapshot.workspace}`);
    assert.equal(await cardCount(page, 'Beta Root'), 1, 'Beta Root card visible');
    assert.equal(await cardCount(page, 'Alpha One'), 0, 'Alpha One hidden');
    assert.equal(await cardCount(page, 'Loose One'), 0, 'Loose One hidden');
    await context.close();
    return { workspace: snapshot.workspace, inherited: true };
  });

  await check('ungrouped session selects the no-workspace scope, no workspace selected', async () => {
    const context = await newIsolatedContext();
    const page = await context.newPage();
    watchPage(page);
    await page.goto(`${chatUrl}?panE2E=1&session=${looseOne}`, { waitUntil: 'domcontentloaded' });
    await poll(() => state(page), (s) => s?.id === looseOne, 'Loose One selected');
    const snapshot = await state(page);
    assert.equal(snapshot.workspace, 'ungrouped', `no-workspace scope, got ${snapshot.workspace}`);
    assert.equal(await cardCount(page, 'Loose One'), 1, 'Loose One card visible');
    assert.equal(await cardCount(page, 'Alpha One'), 0, 'Alpha One hidden');
    assert.equal(await cardCount(page, 'Beta Root'), 0, 'Beta Root hidden');
    await context.close();
    return { workspace: snapshot.workspace };
  });

  await check('notification onclick selects the session AND its workspace from another view', async () => {
    const context = await newIsolatedContext();
    const page = await context.newPage();
    watchPage(page);
    await page.goto(`${chatUrl}?panE2E=1`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
    await poll(() => state(page), (s) => s && s.loaded && s.list.length >= 5, 'sessions loaded');

    await selectSessionByName(page, 'Alpha One');
    await poll(() => state(page), (s) => s?.id === alphaOne, 'Alpha One selected');
    await sendFromComposer(page, 'ws-e2e-hot');
    await waitForNotification(page, 'Alpha One');

    await page.getByRole('link', { name: 'Jobs' }).click();
    await poll(() => pathOf(page), (p) => p.endsWith('/jobs'), 'navigated to jobs');
    await clickNotification(page, 'Alpha One');
    await poll(() => state(page), (s) => s?.id === alphaOne, 'selection restored to Alpha One');

    const snapshot = await state(page);
    assert.equal(snapshot.workspace, alphaId, `workspace restored to Alpha Lab, got ${snapshot.workspace}`);
    assert.ok(/\/react\/?$/.test(await pathOf(page)), 'back on the chat route');
    assert.ok(!(await searchOf(page)).includes('session='), 'no session param leaked');
    assert.equal(await cardCount(page, 'Alpha Two'), 1, 'Alpha Two card visible');
    assert.equal(await cardCount(page, 'Beta Root'), 0, 'Beta Root hidden (scoped to Alpha Lab)');
    await context.close();
    return { workspace: snapshot.workspace, selection: snapshot.id };
  });

  await check('a user workspace switch (A -> B -> A) during a slow lookup wins', async () => {
    const context = await newIsolatedContext();
    const page = await context.newPage();
    watchPage(page);
    let delayed = true;
    const listPredicate = (url) => new URL(url).pathname === '/api/sessions';
    await page.route(listPredicate, async (route) => {
      if (delayed) await sleep(1500);
      return route.continue();
    });
    await page.goto(`${chatUrl}?panE2E=1&session=${alphaOne}`, { waitUntil: 'domcontentloaded' });
    // Wait for the workspace rows (loaded from /api/workspaces, not delayed) and
    // expand the rail so a real user can switch scopes.
    const handle = page.locator('button[title*="点击展开工作区"]');
    await handle.waitFor({ state: 'visible', timeout: 20000 });
    await handle.click();
    await page.locator('button[title*="收起工作区面板"]').waitFor({ state: 'visible', timeout: 5000 });
    const alphaRow = page.locator(`[data-testid="workspace-tab-${alphaId}"]`);
    const betaRow = page.locator(`[data-testid="workspace-tab-${betaId}"]`);
    await alphaRow.click();
    await betaRow.click();
    await alphaRow.click();
    await poll(() => state(page), (s) => s?.workspace === alphaId, 'user landed on Alpha again');
    await sleep(2200); // the delayed lookup settles now

    const snapshot = await state(page);
    // The A -> B -> A roam looks unchanged by value, so only the workspace
    // generation guard can reject the late notification jump.
    assert.equal(snapshot.workspace, alphaId, `user workspace preserved, got ${snapshot.workspace}`);
    assert.notEqual(snapshot.id, alphaOne, 'late jump did not force-select the session');
    await context.close();
    return { workspace: snapshot.workspace, selection: snapshot.id };
  });

  await check('cold ?session with an unavailable workspace list keeps the link, then recovers', async () => {
    const context = await newIsolatedContext();
    const page = await context.newPage();
    watchPage(page);
    let failing = true;
    const wsPredicate = (url) => new URL(url).pathname === '/api/workspaces';
    await page.route(wsPredicate, (route) => (failing ? route.abort() : route.continue()));

    await page.goto(`${chatUrl}?panE2E=1&session=${alphaOne}`, { waitUntil: 'domcontentloaded' });
    await page.getByText(WORKSPACE_UNAVAILABLE_TOAST).first().waitFor({ state: 'visible', timeout: 20000 });

    const held = await state(page);
    assert.equal(held.id, null, 'nothing selected while the workspace is unprovable');
    assert.equal(held.workspace, 'all', 'no workspace scope claimed');
    assert.ok((await searchOf(page)).includes(`session=${alphaOne}`), 'deep link preserved');
    assert.equal(await page.getByText(MISSING_TOAST).count(), 0, 'not reported as deleted');

    // The list is back: the preserved link resolves from a fresh load.
    failing = false;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await poll(() => state(page), (s) => s?.id === alphaOne, 'recovered selection');
    const recovered = await state(page);
    assert.equal(recovered.workspace, alphaId, 'recovered workspace');
    await poll(() => searchOf(page), (q) => !q.includes('session='), 'session param consumed');
    await context.close();
    return { workspace: recovered.workspace };
  });

  await check('cold ?session whose target is deleted during the workspace wait is not applied', async () => {
    const context = await newIsolatedContext();
    const page = await context.newPage();
    watchPage(page);
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const wsPredicate = (url) => new URL(url).pathname === '/api/workspaces';
    await page.route(wsPredicate, async (route) => { await gate; return route.continue(); });

    await page.goto(`${chatUrl}?panE2E=1&session=${doomedCold}`, { waitUntil: 'domcontentloaded' });
    await page.waitForRequest((request) => new URL(request.url()).pathname === '/api/workspaces', { timeout: 20000 });

    // Delete the target while the workspace list is still held.
    assert.ok(await deleteSessionForGood(doomedCold), 'Doomed Cold deleted durably');
    const feedback = watchDeletionFeedback(page);
    await dropFromClient(page, doomedCold);
    release();

    assert.ok(await feedback, 'the deletion was surfaced (deleted or list unavailable)');
    const after = await state(page);
    assert.equal(after.id, null, 'deleted target is not selected');
    assert.equal(after.workspace, 'all', 'no workspace applied');
    await context.close();
    return { selection: after.id };
  });

  await check('hot click with an unavailable workspace list applies nothing and reports honestly', async () => {
    const context = await newIsolatedContext();
    const page = await context.newPage();
    watchPage(page);
    const wsPredicate = (url) => new URL(url).pathname === '/api/workspaces';
    await page.route(wsPredicate, (route) => route.abort());

    await page.goto(`${chatUrl}?panE2E=1`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
    await poll(() => state(page), (s) => s && s.loaded, 'sessions loaded');

    await selectSessionByName(page, 'Hot Target');
    await poll(() => state(page), (s) => s?.id === hotTarget, 'Hot Target selected');
    await sendFromComposer(page, 'ws-e2e-unavailable');
    await waitForNotification(page, 'Hot Target');
    // Make another session current: the jump must NOT force-select the target
    // when its workspace cannot be proven.
    await selectSessionByName(page, 'Alpha Two');
    await poll(() => state(page), (s) => s?.id === alphaTwo, 'Alpha Two is current');

    const feedback = watchDeletionFeedback(page);
    await clickNotification(page, 'Hot Target');

    assert.ok(await feedback, 'the unprovable workspace was surfaced honestly (not as 无工作区)');
    const after = await state(page);
    assert.equal(after.id, alphaTwo, 'the target was not force-selected');
    assert.equal(after.workspace, 'all', 'no workspace was applied');
    await context.close();
    return { selection: after.id, workspace: after.workspace };
  });

  // --- mobile ------------------------------------------------------------------

  await check('mobile viewport: cold ?session selects session AND workspace', async () => {
    const mobileContext = await newIsolatedContext({
      viewport: { width: 390, height: 844 },
      isMobile: true,
      hasTouch: true,
    });
    const page = await mobileContext.newPage();
    watchPage(page);
    await page.goto(`${chatUrl}?panE2E=1&session=${alphaTwo}`, { waitUntil: 'domcontentloaded' });
    await poll(() => state(page), (s) => s?.id === alphaTwo, 'Alpha Two selected on mobile');
    const snapshot = await state(page);
    assert.equal(snapshot.workspace, alphaId, `mobile active workspace Alpha Lab, got ${snapshot.workspace}`);
    assert.ok(!(await searchOf(page)).includes('session='), 'session param consumed on mobile');
    await mobileContext.close();
    return { workspace: snapshot.workspace, viewport: '390x844' };
  });

  await check('no external requests and no page errors across the run', async () => {
    const errors = [...pageErrors.values()].flat();
    assert.deepEqual(foreignRequests, []);
    assert.deepEqual(errors, []);
    return {
      foreignRequests: foreignRequests.length,
      blockedFontRequests: blockedFontRequests.length,
      pageErrors: errors.length,
    };
  });
} catch (error) {
  summary.aborted = String(error?.stack || error);
  console.error(`E2E aborted: ${error?.stack || error}`);
} finally {
  if (browser) await browser.close().catch(() => {});
  await stopServer();
  const failed = results.filter((entry) => !entry.ok);
  summary = {
    ...summary,
    ok: !summary.aborted && failed.length === 0,
    browserVersion: browser ? browser.version() : null,
    results,
    foreignRequests,
    blockedFontRequests: [...new Set(blockedFontRequests)],
    pageErrors: [...pageErrors.values()].flat(),
    runtime,
  };
  await fs.writeFile(
    path.join(outDir, 'e2e-notification-workspace-jump-real.json'),
    `${JSON.stringify(summary, null, 2)}\n`,
    'utf8',
  );
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exitCode = summary.ok ? 0 : 1;
}
