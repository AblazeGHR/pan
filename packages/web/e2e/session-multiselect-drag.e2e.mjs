/* global document, localStorage, URL, process, console */
// Isolated production HTTP/Chromium acceptance for select-mode multi-select:
//   - card-body click toggles selection (never opens the Session),
//   - the dedicated drag gutter never selects,
//   - press-and-move sweeps every crossed Session into the selection,
//   - dragging a selected Session drags the whole selection through the
//     existing manage / order persistence,
//   - a descendant-target drop is rejected,
//   - bulk Manage / msgBridge read the representative, write only after
//     confirmation, skip the manages-session list, and roll back a partial
//     failure.
//
// Serves the built production bundle on a loopback-only random port and stubs
// every API in the browser. It never connects to a Pan service, never uses
// Pan data/profile, and refuses the shared 8767/8768 ports.
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { chromium } from '@playwright/test';

const DIST = path.resolve(process.env.PAN_SELECT_DRAG_DIST || 'dist');
const OUT = path.join(os.tmpdir(), 'pan-session-multiselect-drag');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.woff2': 'font/woff2',
};

assert.ok(fs.existsSync(path.join(DIST, 'index.html')), `missing build at ${DIST}; run pnpm build first`);
fs.mkdirSync(OUT, { recursive: true });

const report = { viewports: {}, ok: false, requests: [] };
const fixture = () => ({
  sessions: [
    {
      id: 'a',
      name: 'Alpha',
      updatedAt: '2026-01-06T00:00:00.000Z',
      managedBy: 'm',
      panAccess: { restrictToManaged: true },
      mcpServers: ['pan'],
      pluginBindings: [{ plugin_id: 'pan', enabled: true, mode: 'standalone' }],
      pluginSelectionRevision: 1,
      msgBridgeEnabled: true,
      notificationSettings: { system: true, browser: false },
      readonlySession: false,
      reportsToManager: true,
    },
    { id: 'b', name: 'Bravo', updatedAt: '2026-01-05T00:00:00.000Z', panAccess: {}, mcpServers: [], msgBridgeEnabled: true, notificationSettings: { system: false, browser: false } },
    { id: 'd', name: 'Delta', updatedAt: '2026-01-04T00:00:00.000Z', panAccess: {}, mcpServers: [], msgBridgeEnabled: true },
    { id: 'p', name: 'Parent', updatedAt: '2026-01-03T00:00:00.000Z', panAccess: {}, mcpServers: [] },
    { id: 'c', name: 'Child', updatedAt: '2026-01-02T00:00:00.000Z', managedBy: 'p', panAccess: {}, mcpServers: [] },
    { id: 'm', name: 'Manager', updatedAt: '2026-01-01T00:00:00.000Z', panAccess: {}, mcpServers: [] },
  ],
  failPatchFor: null,
});

let state = fixture();
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (url.pathname.startsWith('/api/')) {
    res.writeHead(404, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ error: 'not found' }));
  }
  const rel = url.pathname.startsWith('/react/') ? url.pathname.slice('/react/'.length) : url.pathname.replace(/^\/+/, '');
  const candidate = path.join(DIST, rel);
  const file = path.extname(candidate) ? candidate : path.join(DIST, 'index.html');
  if (!file.startsWith(DIST) || !fs.existsSync(file)) {
    res.writeHead(404);
    return res.end('not found');
  }
  res.writeHead(200, {
    'Content-Type': MIME[path.extname(file)] || 'application/octet-stream',
    'Cache-Control': 'no-store',
  });
  fs.createReadStream(file).pipe(res);
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
assert.ok(![8767, 8768].includes(port), `refusing shared port ${port}`);
const BASE = `http://127.0.0.1:${port}`;

function findSession(id) {
  return state.sessions.find((session) => session.id === id);
}

async function installApiStubs(page, viewportName) {
  await page.routeWebSocket('**/ws', (ws) => ws.onMessage(() => {}));
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    let body = null;
    try {
      body = request.postDataJSON();
    } catch {
      body = null;
    }
    const reply = (data, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    report.requests.push({ viewport: viewportName, method, path: url.pathname, body });

    if (url.pathname === '/api/sessions' && method === 'GET') return reply({ sessions: state.sessions });
    if (url.pathname === '/api/mcp/servers') {
      return reply({ loaded: true, servers: [{ name: 'pan' }, { name: 'qq' }] });
    }
    if (url.pathname === '/api/main/startup-recovery') {
      return reply({ generation: 'fixture', state: 'no_candidates', candidateSnapshot: [], decision: null, attempts: 0, results: [] });
    }
    if (url.pathname === '/api/list') return reply({ workers: [] });
    if (url.pathname === '/api/workspaces') return reply({ workspaces: [] });
    if (url.pathname === '/api/settings/ui') return reply({ showGroupBy: true, defaultGroupBy: 'none' });
    if (url.pathname === '/api/claim' || url.pathname === '/api/unclaim') {
      const target = findSession(body?.sessionId);
      if (target) target.managedBy = url.pathname === '/api/claim' ? body.managerId : undefined;
      return reply({ ok: true });
    }
    if (url.pathname === '/api/sessions/order') {
      const byId = new Map(state.sessions.map((session) => [session.id, session]));
      const rest = state.sessions.filter((session) => !body.sessionIds.includes(session.id));
      state.sessions = [...body.sessionIds, ...rest.map((session) => session.id)]
        .map((id) => byId.get(id))
        .filter(Boolean);
      return reply({ ok: true, order: state.sessions.map((session) => session.id) });
    }
    const detail = url.pathname.match(/^\/api\/sessions\/([^/]+)$/);
    if (detail && method === 'GET') {
      const session = findSession(detail[1]);
      return session ? reply(session) : reply({ error: 'not found' }, 404);
    }
    if (detail && method === 'PATCH') {
      const session = findSession(detail[1]);
      if (!session) return reply({ error: 'not found' }, 404);
      if (state.failPatchFor === detail[1]) return reply({ error: { message: 'Server rejected settings' } }, 409);
      Object.assign(session, body);
      return reply(session);
    }
    const bridge = url.pathname.match(/^\/api\/sessions\/([^/]+)\/msg-bridge$/);
    if (bridge && method === 'PUT') {
      const session = findSession(bridge[1]);
      if (!session) return reply({ error: 'not found' }, 404);
      session.msgBridgeEnabled = body.enabled;
      return reply({ ok: true, msgBridgeEnabled: body.enabled });
    }
    if (url.pathname === '/api/readonly') return reply({ ok: true });
    if (/^\/api\/sessions\/[^/]+\/report-to-manager$/.test(url.pathname)) {
      return reply({ ok: true, reportsToManager: body.enabled });
    }
    if (/^\/api\/sessions\/[^/]+\/history$/.test(url.pathname)) {
      return reply({ history: [], total: 0, hasMore: false, start: 0 });
    }
    // Unknown endpoints fail closed exactly like the other isolated production
    // fixtures; returning `{}` makes list stores crash on iteration.
    return reply({ error: 'not found' }, 404);
  });
}

function card(page, id) {
  return page.locator(`[data-session-card-id="${id}"]`);
}
function gutter(page, id) {
  return page.locator(`[data-session-card-id="${id}"] [data-testid="drag-handle"]`);
}
function selectionCount(page) {
  return page.locator('.sidebar-selection-bar').getByText(/\d+ selected/).textContent();
}
function requestsOf(method, pathname) {
  return report.requests.filter((entry) => entry.method === method && entry.path === pathname);
}
async function enterSelectMode(page, id = 'a') {
  await card(page, id).hover();
  await card(page, id).locator('[data-testid="session-actions-menu"]').click();
  await page.getByRole('button', { name: 'Select', exact: true }).click();
  await page.locator('.sidebar-selection-bar').waitFor({ state: 'visible' });
}
async function openActions(page) {
  await page.locator('.sidebar-selection-bar').getByRole('button', { name: /Actions/ }).click();
}
async function clearSelection(page) {
  // One binary button: select all when anything is missing, then deselect all.
  const selectAll = page.getByRole('button', { name: 'Select all visible sessions' });
  if (await selectAll.count()) await selectAll.click();
  const deselect = page.getByRole('button', { name: 'Deselect all visible sessions' });
  if (await deselect.count()) await deselect.click();
  await page.waitForFunction(() =>
    document.querySelector('.sidebar-selection-bar')?.textContent?.includes('0 selected'));
  // Let the post-drag/post-sweep click guard (600ms) expire so the next
  // card-body click is a real toggle instead of a swallowed drag-release click.
  await page.waitForTimeout(700);
}

const browser = await chromium.launch({ headless: true });
try {
  for (const viewport of [
    { name: 'desktop', width: 1440, height: 900 },
    { name: 'narrow', width: 390, height: 844 },
  ]) {
    state = fixture();
    report.requests.length = 0;
    const context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      isMobile: viewport.width < 768,
      hasTouch: viewport.width < 768,
    });
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    await installApiStubs(page, viewport.name);
    await page.goto(`${BASE}/react/`, { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => {
      localStorage.clear();
      localStorage.setItem('pan:dragEnabled', 'true');
    });
    await page.reload({ waitUntil: 'domcontentloaded' });
    if (viewport.width < 768) {
      await page.getByRole('button', { name: '打开侧边栏' }).click();
    }
    try {
      await card(page, 'a').waitFor({ state: 'visible', timeout: 15000 });
    } catch (error) {
      console.log('BODY', (await page.locator('body').innerText()).slice(0, 1200));
      await page.screenshot({ path: path.join(OUT, `failure-${viewport.name}.png`), fullPage: false });
      throw error;
    }

    const result = {};

    // 1) Enter select mode from the per-card menu (Alpha is selected).
    await enterSelectMode(page, 'a');
    assert.match(await selectionCount(page), /^1 selected/);

    // 2) Clicking any card body toggles selection and never opens the session.
    const urlBeforeCardClick = page.url();
    await card(page, 'b').click();
    assert.match(await selectionCount(page), /^2 selected/);
    assert.equal(await card(page, 'b').locator('input[type="checkbox"]').isChecked(), true);
    assert.equal(page.url(), urlBeforeCardClick, 'card-body click must not open the Session');

    // 3) The drag gutter stays a pure drag zone (no selection change on click).
    await gutter(page, 'd').click();
    assert.match(await selectionCount(page), /^2 selected/);

    // 4) Sweep: press on Delta and move across Parent/Child; all are added and
    //    the gesture itself never starts a sort drag.
    const d = await card(page, 'd').boundingBox();
    const child = await card(page, 'c').boundingBox();
    await page.mouse.move(d.x + d.width * 0.5, d.y + d.height / 2);
    await page.mouse.down();
    await page.mouse.move(d.x + d.width * 0.5, child.y + child.height / 2, { steps: 12 });
    await page.mouse.up();
    assert.match(await selectionCount(page), /^5 selected/);
    assert.equal(await page.locator('[data-drag-ghost]').count(), 0, 'sweep must not start a drag');
    assert.equal(requestsOf('POST', '/api/sessions/order').length, 0, 'sweep must not reorder');
    result.sweep = '5 selected';
    await clearSelection(page);
    assert.match(await selectionCount(page), /^0 selected/);

    // 5) Bulk Manage: representative, no write before confirm, all members.
    await card(page, 'a').click();
    await card(page, 'b').click();
    await openActions(page);
    await page.getByRole('menuitem', { name: 'Manage', exact: true }).click();
    await page.locator('[data-testid="bulk-manage-modal"]').waitFor({ state: 'visible' });
    await page.getByText('Representative: Alpha').waitFor({ timeout: 5000 });
    assert.ok((await page.locator('[data-testid="bulk-manage-modal"]').textContent()).includes('Representative: Alpha'));
    assert.equal(await page.locator('[data-testid="bulk-managed-sessions"]').count(), 0);
    assert.equal(requestsOf('PATCH', '/api/sessions/a').length, 0, 'no PATCH before confirmation');
    assert.equal(requestsOf('PATCH', '/api/sessions/b').length, 0, 'no PATCH before confirmation');
    await page.getByLabel('Can claim unmanaged', { exact: true }).check();
    await page.getByTestId('bulk-apply').click();
    await page.getByText('Updated 2 selected Sessions').waitFor({ timeout: 5000 });
    const patchA = requestsOf('PATCH', '/api/sessions/a').find((entry) => entry.body.panAccess);
    const patchB = requestsOf('PATCH', '/api/sessions/b').find((entry) => entry.body.panAccess);
    assert.deepEqual(patchA.body, { panAccess: { canClaimUnmanaged: true } });
    assert.deepEqual(patchB.body, { panAccess: { restrictToManaged: true, canClaimUnmanaged: true } });
    assert.ok(!Object.prototype.hasOwnProperty.call(patchA.body, 'managed'));
    result.bulkManage = { patchA: patchA.body, patchB: patchB.body };

    // 6) Bulk msgBridge: representative Alpha (on) → off for both.
    await openActions(page);
    await page.getByRole('menuitem', { name: 'MsgBridge', exact: true }).click();
    await page.locator('[data-testid="bulk-msgbridge-modal"]').waitFor({ state: 'visible' });
    await page.getByText('Representative: Alpha').waitFor({ timeout: 5000 });
    assert.equal(await page.getByLabel('msgBridge enabled', { exact: true }).isChecked(), true);
    await page.getByLabel('msgBridge enabled', { exact: true }).uncheck();
    await page.getByTestId('bulk-apply').click();
    await page.getByText('Updated msgBridge for 2 selected Sessions').waitFor({ timeout: 5000 });
    const bridgeCalls = report.requests.filter((entry) => entry.path.endsWith('/msg-bridge'));
    assert.ok(bridgeCalls.some((entry) => entry.path === '/api/sessions/a/msg-bridge' && entry.body.enabled === false));
    assert.ok(bridgeCalls.some((entry) => entry.path === '/api/sessions/b/msg-bridge' && entry.body.enabled === false));
    result.bulkMsgBridge = bridgeCalls.map((entry) => ({ path: entry.path, enabled: entry.body.enabled }));

    // 7) Partial failure rolls the first member back.
    state.failPatchFor = 'b';
    await openActions(page);
    await page.getByRole('menuitem', { name: 'Manage', exact: true }).click();
    await page.locator('[data-testid="bulk-manage-modal"]').waitFor({ state: 'visible' });
    // Can claim unmanaged is now on for both; turn it off -> Alpha must restore it.
    await page.getByLabel('Can claim unmanaged', { exact: true }).uncheck();
    await page.getByTestId('bulk-apply').click();
    await page.getByText(/rolled back/).first().waitFor({ timeout: 5000 });
    const patchesA = requestsOf('PATCH', '/api/sessions/a');
    assert.deepEqual(patchesA.at(-1).body, { panAccess: { canClaimUnmanaged: true } });
    result.partialRollback = patchesA.map((entry) => entry.body);
    state.failPatchFor = null;
    await page.getByRole('button', { name: 'Cancel', exact: true }).last().click();

    // 8) Selection drag to an edge: the whole selection lands contiguously.
    await clearSelection(page);
    await card(page, 'a').click();
    await card(page, 'd').click();
    assert.match(await selectionCount(page), /^2 selected/);
    const p = await card(page, 'p').boundingBox();
    const gutterA = await gutter(page, 'a').boundingBox();
    await page.mouse.move(gutterA.x + gutterA.width / 2, gutterA.y + gutterA.height / 2);
    await page.mouse.down();
    await page.mouse.move(p.x + p.width / 2, p.y + p.height - 3, { steps: 10 });
    const ghostCount = await page.locator('[data-drag-ghost]').getAttribute('data-drag-count');
    assert.equal(ghostCount, '2');
    await page.mouse.up();
    await page.waitForTimeout(250);
    const orderReq = requestsOf('POST', '/api/sessions/order').at(-1);
    assert.ok(orderReq, 'expected POST /api/sessions/order');
    const order = orderReq.body.sessionIds;
    const ai = order.indexOf('a');
    const di = order.indexOf('d');
    assert.ok(ai >= 0 && di >= 0 && Math.abs(ai - di) === 1, `selection not contiguous: ${order.join(',')}`);
    assert.ok(ai > order.indexOf('p') && di > order.indexOf('p'), `selection not after Parent: ${order.join(',')}`);
    result.selectionEdgeOrder = order;

    // 9) Selection drag to a center: every member is claimed by the target.
    await clearSelection(page);
    await card(page, 'a').click();
    await card(page, 'd').click();
    const bBox = await card(page, 'b').boundingBox();
    const gutterA2 = await gutter(page, 'a').boundingBox();
    await page.mouse.move(gutterA2.x + gutterA2.width / 2, gutterA2.y + gutterA2.height / 2);
    await page.mouse.down();
    await page.mouse.move(bBox.x + bBox.width / 2, bBox.y + bBox.height / 2, { steps: 10 });
    await page.mouse.up();
    await page.waitForTimeout(250);
    const claims = requestsOf('POST', '/api/claim').map((entry) => entry.body);
    assert.ok(claims.some((body) => body.managerId === 'b' && body.sessionId === 'a'), 'Alpha not claimed');
    assert.ok(claims.some((body) => body.managerId === 'b' && body.sessionId === 'd'), 'Delta not claimed');
    result.selectionCenterClaims = claims;

    // 10) A descendant target is rejected for the whole selection.
    await clearSelection(page);
    await card(page, 'p').click();
    await card(page, 'd').click();
    const childBox = await card(page, 'c').boundingBox();
    const gutterP = await gutter(page, 'p').boundingBox();
    const claimsBefore = requestsOf('POST', '/api/claim').length;
    await page.mouse.move(gutterP.x + gutterP.width / 2, gutterP.y + gutterP.height / 2);
    await page.mouse.down();
    await page.mouse.move(childBox.x + childBox.width / 2, childBox.y + childBox.height / 2, { steps: 10 });
    await page.mouse.up();
    await page.getByText(/禁止：不能把/).waitFor({ timeout: 3000 });
    assert.equal(requestsOf('POST', '/api/claim').length, claimsBefore);
    result.descendantRejected = true;

    // 11) Narrow-screen reachability of the bulk Manage surface.
    if (viewport.width < 768) {
      await openActions(page);
      await page.getByRole('menuitem', { name: 'Manage', exact: true }).click();
      await page.locator('[data-testid="bulk-manage-modal"]').waitFor({ state: 'visible' });
      await page.getByText('Representative: Parent').waitFor({ timeout: 5000 });
      assert.equal(await page.getByTestId('bulk-apply').isVisible(), true);
      result.narrowBulkVisible = true;
      await page.getByRole('button', { name: 'Cancel', exact: true }).last().click();
    }

    assert.deepEqual(pageErrors, [], `${viewport.name}: page errors: ${pageErrors.join('; ')}`);
    await page.screenshot({ path: path.join(OUT, `select-multiselect-drag-${viewport.name}.png`), fullPage: false });
    result.pageErrors = pageErrors;
    report.viewports[viewport.name] = result;
    await context.close();
  }
  report.ok = true;
} catch (error) {
  report.error = error instanceof Error ? error.stack || error.message : String(error);
  throw error;
} finally {
  fs.writeFileSync(path.join(OUT, 'session-multiselect-drag.json'), `${JSON.stringify(report, null, 2)}\n`);
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
console.log(JSON.stringify(report, null, 2));
