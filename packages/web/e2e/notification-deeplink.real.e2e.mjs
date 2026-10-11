/* global window, document, URL, fetch, process, console, MouseEvent, Event */
/**
 * Real-backend E2E for the notification → session deep link (phase 1).
 *
 * Real React production bundle + isolated real Pan backend (dynamic port,
 * disposable runtime root, deterministic fake CLI provider, system
 * notification channel disabled in every seeded session). The browser
 * `Notification` display layer is stubbed: instances are captured and the
 * app-attached `onclick` handler is invoked exactly as a delivered click
 * would invoke it. OS-level notification display/focus remains a browser/OS
 * boundary and is explicitly NOT asserted here.
 *
 * Covered: real worker.result → notification creation → onclick → router →
 * selected session (from another view), deleted-before-click, list-load
 * failure + recovery for ?session, user take-over during a slow lookup,
 * rapid double click, dual tab isolation, background tab handler, and the
 * no-external-link / no-body-in-URL constraints.
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
const runtime = process.env.PAN_NOTIFICATION_E2E_RUNTIME
  || path.join(webRoot, 'test-results', `notify-${Date.now()}`);
const outDir = process.env.PAN_NOTIFICATION_E2E_OUT
  || path.join(repoRoot, 'audit', 'notification-click');
await fs.mkdir(runtime, { recursive: true });
await fs.mkdir(outDir, { recursive: true });

const MISSING_TOAST = '通知对应的会话不存在或已被删除，无法跳转';
const LIST_UNAVAILABLE_TOAST = '会话列表暂时无法加载，未能打开该会话';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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
  process.env.PAN_NOTIFICATION_E2E_PYTHON,
  process.env.PAN_E2E_PYTHON,
  'D:/project/Pan-main/.venv/Scripts/python.exe',
  'D:/project/Pan/.venv/Scripts/python.exe',
]);
assert.ok(python, 'no Pan python interpreter found (set PAN_NOTIFICATION_E2E_PYTHON)');

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

async function apiPost(route, body) {
  return api(route, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * Deterministically remove a session whose worker is still alive.
 * The single DELETE schedules worker cleanup asynchronously, and a late
 * worker-side save can re-persist the session (observed here; out of phase-1
 * scope). Kill first, then retry delete until the server list drops it —
 * this only establishes the "really deleted" precondition the case needs.
 */
async function deleteSessionForGood(sessionId) {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    try {
      await apiPost(`/api/sessions/${sessionId}/worker/kill`, {});
    } catch {
      // no live worker / already gone
    }
    try {
      await api(`/api/sessions/${sessionId}`, { method: 'DELETE' });
    } catch {
      // already gone is fine
    }
    await sleep(400);
    const payload = await api('/api/sessions?summary=1');
    if (!(payload.sessions || []).some((entry) => entry.id === sessionId)) return true;
  }
  return false;
}

async function poll(read, test, label, timeout = 20000) {
  let last;
  for (const end = Date.now() + timeout; Date.now() < end; await sleep(80)) {
    last = await read();
    if (test(last)) return last;
  }
  throw new Error(`${label}: last=${JSON.stringify(last)?.slice(0, 800)}`);
}

// --- evidence ---------------------------------------------------------------

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
const sessionNetLog = [];
const FONT_HOSTS = /^https?:\/\/fonts\.(googleapis|gstatic)\.com\//;
function watchPage(page) {
  pageErrors.set(page, []);
  page.on('pageerror', (error) => pageErrors.get(page)?.push(error.message));
  page.on('request', (request) => {
    let parsed;
    try { parsed = new URL(request.url()); } catch { return; }
    if (parsed.pathname.startsWith('/api/sessions')) {
      sessionNetLog.push({ t: Date.now(), phase: 'req', method: request.method(), path: parsed.pathname + parsed.search });
    }
    if (!parsed.protocol.startsWith('http')) return;
    if (FONT_HOSTS.test(request.url())) {
      blockedFontRequests.push(request.url());
      return;
    }
    if (parsed.hostname === '127.0.0.1' && parsed.port === String(port)) return;
    foreignRequests.push(`${request.method()} ${request.url()}`);
  });
  page.on('response', (response) => {
    let parsed;
    try { parsed = new URL(response.url()); } catch { return; }
    if (parsed.pathname.startsWith('/api/sessions')) {
      sessionNetLog.push({ t: Date.now(), phase: 'res', status: response.status(), path: parsed.pathname + parsed.search });
    }
  });
}

// --- browser helpers --------------------------------------------------------

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
    const store = window.__panSessionStore;
    if (!store) return null;
    const s = store.getState();
    return {
      id: s.currentSessionId,
      list: s.sessions.map((session) => session.id),
      loaded: !s.sessionsLoading,
    };
  });
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

async function notificationSummary(page) {
  return page.evaluate(() => (window.__panNotifications || []).map((notification) => ({
    title: notification.title,
    body: String(notification.options?.body ?? ''),
    hasOnclick: typeof notification.onclick === 'function',
  })));
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

async function doubleClickNotification(page, titlePart) {
  const dispatched = await page.evaluate((needle) => {
    const list = window.__panNotifications || [];
    const target = [...list].reverse()
      .find((notification) => String(notification.title || '').includes(needle));
    if (!target || typeof target.onclick !== 'function') return false;
    target.onclick(new MouseEvent('click'));
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
  [path.join(here, 'notification-deeplink.server.py')],
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
  runtime,
  results,
};

try {
  // The production bundle must be newer than the deep-link source.
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
    async () => {
      try { return await api('/api/sessions?summary=1'); } catch { return null; }
    },
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
  for (const name of ['Notify Target', 'Other Target', 'Delete Target']) {
    assert.ok(nameToId.get(name), `seeded session '${name}' is present`);
  }
  const notifyId = nameToId.get('Notify Target');
  const otherId = nameToId.get('Other Target');
  const deleteId = nameToId.get('Delete Target');

  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript(NOTIFICATION_STUB);
  // Seal the run: the app's static Google-font links are pre-existing page
  // resources, blocked here so "no external request" proves the feature never
  // reaches outside the isolated origin.
  await context.route(FONT_HOSTS, (route) => route.abort());

  const page = await context.newPage();
  watchPage(page);
  await page.goto(`${chatUrl}?panE2E=1`, { waitUntil: 'domcontentloaded' });
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  await poll(() => state(page), (s) => s && s.loaded && s.list.length >= 3, 'sessions loaded');

  await check('real worker.result creates the notification for the completed session', async () => {
    await selectSessionByName(page, 'Notify Target');
    await poll(() => state(page), (s) => s?.id === notifyId, 'Notify Target selected');
    await sendFromComposer(page, 'notify-e2e-one');
    await waitForNotification(page, 'Notify Target');
    const notifications = await notificationSummary(page);
    const target = notifications.find((n) => n.title.includes('Notify Target'));
    assert.ok(target, 'a notification titled after Notify Target exists');
    assert.ok(target.body.includes('answer:notify-e2e-one'), `body carries the result: ${target.body}`);
    assert.equal(target.hasOnclick, true);
    // The session keeps system:false, so no OS notification channel ran; the
    // stub is the only Notification consumer in this browser context.
    return { title: target.title, body: target.body.slice(0, 60) };
  });

  await check('onclick from another view returns to chat and selects the session', async () => {
    await page.getByRole('link', { name: 'Jobs' }).click();
    await poll(() => pathOf(page), (p) => p.endsWith('/jobs'), 'navigated to jobs');
    await clickNotification(page, 'Notify Target');
    await poll(() => state(page), (s) => s?.id === notifyId, 'selection restored to Notify Target');
    const pathname = await pathOf(page);
    assert.ok(/\/react\/?$/.test(pathname), `back on the chat route: ${pathname}`);
    const search = await searchOf(page);
    assert.ok(!search.includes('session='), `no session param leaked into the URL: ${search}`);
    assert.ok(!/answer%3A|answer:/.test(search), `notification body never enters the URL: ${search}`);
    return { pathname, search };
  });

  await check('a session with browser notifications off completes silently', async () => {
    const before = await notificationSummary(page);
    await selectSessionByName(page, 'Other Target');
    await poll(() => state(page), (s) => s?.id === otherId, 'Other Target selected');
    await sendFromComposer(page, 'notify-e2e-off');
    // Completion without a notification still lands in canonical history.
    await poll(
      () => api(`/api/sessions/${otherId}/history?before=0&limit=50`),
      (payload) => (payload.history || [])
        .some((row) => String(row.content || '').includes('answer:notify-e2e-off')),
      'Other Target completion persisted',
    );
    await sleep(800); // allow any straggler event to create a notification
    const after = await notificationSummary(page);
    assert.equal(after.length, before.length, 'no new browser notification');
    assert.ok(!after.some((n) => n.title.includes('Other Target')),
      `browser=off produced no notification: ${JSON.stringify(after.map((n) => n.title))}`);
    return { notificationsBefore: before.length, notificationsAfter: after.length };
  });

  await check('deleted-before-click degrades to the deletion toast, no navigation', async () => {
    await selectSessionByName(page, 'Delete Target');
    await poll(() => state(page), (s) => s?.id === deleteId, 'Delete Target selected');
    await sendFromComposer(page, 'notify-e2e-del');
    await waitForNotification(page, 'Delete Target');
    await page.evaluate(() => {
      window.__panListLog = [];
      window.setInterval(() => {
        const s = window.__panSessionStore?.getState();
        if (s) {
          window.__panListLog.push({
            t: Date.now(),
            id: s.currentSessionId,
            list: s.sessions.map((x) => x.id),
          });
        }
      }, 100);
    });
    assert.ok(await deleteSessionForGood(deleteId), 'server-side deletion is durable');
    // Nudge the client's authoritative refresh; the store then converges.
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    const deletionApplied = await poll(
      () => state(page),
      (s) => s && !s.list.includes(deleteId),
      'deletion applied to the store',
    ).catch(async (error) => {
      const timeline = await page.evaluate(() => (window.__panListLog || []).slice(-60));
      const net = sessionNetLog.slice(-40);
      let serverList = [];
      try {
        const payload = await api('/api/sessions?summary=1');
        serverList = (payload.sessions || []).map((entry) => `${entry.id}:${entry.name}`);
      } catch (serverError) {
        serverList = [`<server list failed: ${serverError?.message}>`];
      }
      throw new Error(
        `${error.message}; deleteId=${deleteId} timeline=${JSON.stringify(timeline)}`
        + ` net=${JSON.stringify(net)} server=${JSON.stringify(serverList)}`,
      );
    });
    void deletionApplied;
    await clickNotification(page, 'Delete Target');
    const toastTexts = await poll(
      () => page.locator('.toast-enter').allTextContents(),
      (texts) => texts.some((t) => t.includes(MISSING_TOAST) || t.includes(LIST_UNAVAILABLE_TOAST)),
      'toast after clicking the deleted notification',
      15000,
    ).catch(async (error) => {
      const notifications = await page.evaluate(
        () => (window.__panNotifications || []).map((n) => n.title),
      );
      const unhandled = await page.evaluate(() => window.__panUnhandled || []);
      const snapshot = await state(page);
      const timeline = await page.evaluate(() => (window.__panListLog || []).slice(-40));
      let serverList = [];
      try {
        const payload = await api('/api/sessions?summary=1');
        serverList = (payload.sessions || []).map((entry) => `${entry.id}:${entry.name}`);
      } catch (serverError) {
        serverList = [`<server list failed: ${serverError?.message}>`];
      }
      throw new Error(
        `${error.message}; deleteId=${deleteId} notifications=${JSON.stringify(notifications)}`
        + ` unhandled=${JSON.stringify(unhandled)} state=${JSON.stringify(snapshot)}`
        + ` server=${JSON.stringify(serverList)} timeline=${JSON.stringify(timeline)}`,
      );
    });
    assert.ok(toastTexts.some((t) => t.includes(MISSING_TOAST)),
      `expected the deletion toast, got ${JSON.stringify(toastTexts)}`);
    const after = await state(page);
    assert.ok(after?.id !== deleteId, 'deleted session is not selected');
    assert.ok(!(await pathOf(page)).includes('/manage'), 'no dead navigation');
    await sleep(3200); // let the 3s toast dismiss before the next case
    return { selection: after?.id ?? null };
  });

  await check('?session survives a failed list load, then recovers with other query params intact', async () => {
    const recoveryPage = await context.newPage();
    watchPage(recoveryPage);
    const listPredicate = (url) => new URL(url).pathname === '/api/sessions';
    const listHandler = (route) => {
      if (new URL(route.request().url()).pathname === '/api/sessions') return route.abort();
      return route.continue();
    };
    await recoveryPage.route(listPredicate, listHandler);
    await recoveryPage.goto(`${chatUrl}?panE2E=1&session=${notifyId}&keep=1`, {
      waitUntil: 'domcontentloaded',
    });
    await recoveryPage.getByText(LIST_UNAVAILABLE_TOAST).first()
      .waitFor({ state: 'visible', timeout: 20000 });
    assert.equal(await recoveryPage.getByText(MISSING_TOAST).count(), 0,
      'a failed load is never reported as deletion');
    await sleep(1500);
    assert.ok((await searchOf(recoveryPage)).includes(`session=${notifyId}`),
      'deep link is kept while the list is unavailable');

    await recoveryPage.unroute(listPredicate, listHandler);
    // A later successful refresh (here: the app focus-recovery path) resolves it.
    await recoveryPage.evaluate(() => window.dispatchEvent(new Event('focus')));
    await poll(() => state(recoveryPage), (s) => s?.id === notifyId, 'recovered selection', 25000);
    await poll(() => searchOf(recoveryPage), (q) => !q.includes('session='), 'session param consumed');
    const search = await searchOf(recoveryPage);
    assert.ok(search.includes('panE2E=1') && search.includes('keep=1'),
      `unrelated query params survive: ${search}`);
    assert.equal(await recoveryPage.getByText(MISSING_TOAST).count(), 0);
    await recoveryPage.close();
    return { search };
  });

  await check('a slow lookup never overrides a later user selection', async () => {
    const preemptPage = await context.newPage();
    watchPage(preemptPage);
    let routeMode = 'strip';
    await preemptPage.route(
      (url) => new URL(url).pathname === '/api/sessions',
      async (route) => {
        if (routeMode === 'strip') {
          const response = await route.fetch();
          const json = await response.json();
          json.sessions = (json.sessions || []).filter((entry) => entry.id !== notifyId);
          return route.fulfill({ response, json });
        }
        if (routeMode === 'delay') {
          await sleep(1200);
          return route.continue();
        }
        return route.continue();
      },
    );
    await preemptPage.goto(`${chatUrl}?panE2E=1`, { waitUntil: 'domcontentloaded' });
    await poll(() => state(preemptPage), (s) => s && s.loaded && !s.list.includes(notifyId),
      'list without the addressed session');

    // Real completion for a session this page cannot see yet.
    await apiPost(`/api/sessions/${notifyId}/queue`, {
      text: 'notify-e2e-absent',
      clientMessageId: `e2e-absent-${Date.now()}`,
    });
    await waitForNotification(preemptPage, 'Notify Target');
    assert.ok(!(await state(preemptPage)).list.includes(notifyId),
      'notification exists while the session card is still absent (forces the async lookup)');

    routeMode = 'delay';
    await clickNotification(preemptPage, 'Notify Target');
    await sleep(250);
    await selectSessionByName(preemptPage, 'Other Target');
    await poll(() => state(preemptPage), (s) => s?.id === otherId, 'user picked Other Target');
    await sleep(1800); // delayed lookup settles now

    const after = await state(preemptPage);
    assert.equal(after?.id, otherId, 'user selection wins over the late lookup');
    assert.equal(await preemptPage.getByText(MISSING_TOAST).count(), 0);
    assert.equal(await preemptPage.getByText(LIST_UNAVAILABLE_TOAST).count(), 0);
    assert.ok((await pathOf(preemptPage)).includes('/react'), 'still on the chat route');
    await preemptPage.close();
    return { selection: after?.id };
  });

  await check('two rapid clicks on one notification apply only once', async () => {
    await selectSessionByName(page, 'Notify Target');
    await poll(() => state(page), (s) => s?.id === notifyId, 'Notify Target selected again');
    await sendFromComposer(page, 'notify-e2e-two');
    await waitForNotification(page, 'Notify Target');
    await doubleClickNotification(page, 'Notify Target');
    await poll(() => state(page), (s) => s?.id === notifyId, 'selection converged');
    await sleep(500);
    assert.equal(await page.getByText(MISSING_TOAST).count(), 0);
    assert.equal(await page.getByText(LIST_UNAVAILABLE_TOAST).count(), 0);
    assert.ok((await pathOf(page)).includes('/react'), 'still on the chat route');
    return { selection: (await state(page))?.id };
  });

  await check('dual tabs stay isolated: clicking one tab does not select in the other', async () => {
    const tabB = await context.newPage();
    watchPage(tabB);
    await tabB.goto(`${chatUrl}?panE2E=1`, { waitUntil: 'domcontentloaded' });
    await poll(() => state(tabB), (s) => s && s.loaded, 'tab B sessions loaded');
    const beforeB = await state(tabB);

    await sendFromComposer(page, 'notify-e2e-three');
    await waitForNotification(page, 'Notify Target');
    await waitForNotification(tabB, 'Notify Target'); // both tabs received their own
    await clickNotification(page, 'Notify Target');
    await poll(() => state(page), (s) => s?.id === notifyId, 'tab A selected Notify Target');
    await sleep(800);
    const afterB = await state(tabB);
    assert.equal(afterB?.id, beforeB?.id ?? null, `tab B selection unchanged: ${afterB?.id}`);
    assert.equal(await tabB.getByText(MISSING_TOAST).count(), 0);
    await tabB.close();
    return { tabA: (await state(page))?.id, tabB: afterB?.id ?? null };
  });

  await check('background-tab click handler still applies the jump', async () => {
    const tabB = await context.newPage();
    watchPage(tabB);
    await tabB.goto(`${chatUrl}?panE2E=1`, { waitUntil: 'domcontentloaded' });
    await poll(() => state(tabB), (s) => s && s.loaded, 'tab B sessions loaded');

    await sendFromComposer(page, 'notify-e2e-four');
    await waitForNotification(page, 'Notify Target');
    await waitForNotification(tabB, 'Notify Target');

    await tabB.bringToFront();
    const visibility = await page.evaluate(() => document.visibilityState);
    await clickNotification(page, 'Notify Target');
    await poll(() => state(page), (s) => s?.id === notifyId, 'background tab handler applied');
    await tabB.close();
    // Headless Chromium may report 'visible' for both pages; record the value
    // instead of claiming a hidden-tab OS scenario.
    return { visibilityStateObserved: visibility, selection: (await state(page))?.id };
  });

  await check('no external requests and no page errors across the run', async () => {
    const errors = [...pageErrors.values()].flat();
    assert.deepEqual(foreignRequests, []);
    assert.deepEqual(errors, []);
    // No page URL carries a session id or notification body at the end.
    for (const [watched] of pageErrors) {
      const href = await watched.evaluate(() => window.location.href).catch(() => '');
      assert.ok(!href.includes('session='), `no session param left in ${href}`);
      assert.ok(!/answer%3A|answer:/.test(href), `no body leaked into ${href}`);
    }
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
    path.join(outDir, 'e2e-notification-deeplink-real.json'),
    `${JSON.stringify(summary, null, 2)}\n`,
    'utf8',
  );
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exitCode = summary.ok ? 0 : 1;
}
