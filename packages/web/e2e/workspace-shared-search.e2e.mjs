/* global console, localStorage, process, document, innerWidth */
// Real-browser E2E for the independent Workspace search and shared status filters (Workspace rail).
// Runs against the isolated e2e/server.py instance: real FastAPI routes, real
// /ws broadcast path, real React build, disposable session fixtures. Workspace
// membership, durable unread cursors and real queue rows are seeded through
// the product routes; worker-state changes are injected through the test-only
// /__e2e/broadcast route exactly like replayed worker events. Ports 8767/8768
// are refused; the runner owns an isolated profile/cache and a dynamic port.

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

const baseURL = process.env.PAN_E2E_BASE_URL || 'http://127.0.0.1:8765';
const runtime = process.env.PAN_E2E_RUNTIME || path.resolve('test-results/pan-e2e-workspace-shared-search');
const artifacts = path.join(runtime, 'artifacts');
await fs.mkdir(artifacts, { recursive: true });

assert.ok(!/:8767|:8768/.test(baseURL), `refusing protected service ports: ${baseURL}`);

const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };

const browser = await chromium.launch({ headless: true });
const results = [];

async function openPage(viewport) {
  const context = await browser.newContext({
    viewport,
    deviceScaleFactor: 1,
    hasTouch: viewport.width <= 480,
  });
  const page = await context.newPage();
  const pageErrors = [];
  const protectedRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => {
    if (request.url().includes(':8767') || request.url().includes(':8768')) {
      protectedRequests.push(`${request.method()} ${request.url()}`);
    }
  });
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  await page.goto(`${baseURL}/react/`);
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  return { context, page, pageErrors, protectedRequests };
}

async function runCase(name, viewport, body) {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  let context;
  let page;
  let pageErrors = [];
  let protectedRequests = [];
  const started = new Date().toISOString();
  try {
    ({ context, page, pageErrors, protectedRequests } = await openPage(viewport));
    await body(page);
    assert.deepEqual(pageErrors, [], `page errors: ${pageErrors.join(' | ')}`);
    assert.deepEqual(protectedRequests, [], `protected-port requests: ${protectedRequests.join(' | ')}`);
    await page.screenshot({ path: path.join(artifacts, `${slug}.png`), fullPage: true });
    await context.tracing.stop({ path: path.join(artifacts, `${slug}.zip`) });
    results.push({ name, status: 'passed', started, screenshot: `${slug}.png` });
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    if (page) await page.screenshot({ path: path.join(artifacts, `${slug}-failure.png`), fullPage: true }).catch(() => {});
    if (context) await context.tracing.stop({ path: path.join(artifacts, `${slug}-failure.zip`) }).catch(() => {});
    results.push({ name, status: 'failed', started, error: message, screenshot: `${slug}-failure.png` });
  } finally {
    await context?.close();
  }
}

// ── API helpers (product routes on the isolated server) ──

async function api(page, route, options = {}) {
  const response = await page.request[options.method ?? 'get'](`${baseURL}${route}`, {
    data: options.body,
  });
  assert.equal(response.ok(), true, `${options.method ?? 'GET'} ${route} failed: ${response.status()}`);
  return response.json();
}

async function sessionIdOf(page, name) {
  const { sessions } = await api(page, '/api/sessions?summary=1');
  const row = sessions.find((item) => item.name === name);
  assert.ok(row, `session ${name} not found`);
  return row.id;
}

async function createWorkspace(page, name) {
  const created = await api(page, '/api/workspaces', { method: 'post', body: { name } });
  assert.ok(created.workspace?.id, `create workspace failed: ${JSON.stringify(created)}`);
  return created.workspace.id;
}

async function moveToWorkspace(page, sessionId, workspaceId) {
  const result = await api(page, `/api/sessions/${sessionId}/workspaces`, {
    method: 'put',
    body: { workspaceIds: [workspaceId] },
  });
  assert.equal(result.ok, true, `move ${sessionId} failed: ${JSON.stringify(result)}`);
}

async function injectSessionPatch(page, sessionId, patch) {
  await api(page, '/__e2e/broadcast', {
    method: 'post',
    body: { event: { type: 'session.updated', sessionId, session: patch } },
  });
}

/** Monotonic worker generation: the client rejects stale generations. */
let workerGeneration = 0;

/** Inject a real worker lifecycle status through the /ws broadcast path. */
async function broadcastWorkerStatus(page, sessionId, status, session) {
  workerGeneration += 1;
  await api(page, '/__e2e/broadcast', {
    method: 'post',
    body: {
      event: {
        type: 'worker.status',
        sessionId,
        workerId: 'e2e-wss-worker',
        status,
        generation: workerGeneration,
        ...(session ? { session } : {}),
      },
    },
  });
}

/**
 * Clear the worker summary a broadcast leaves on the shared isolated server
 * process, so later cases start from no active worker.
 */
async function clearWorkerStatus(page, sessionId) {
  workerGeneration += 1;
  await api(page, '/__e2e/broadcast', {
    method: 'post',
    body: {
      event: {
        type: 'worker.status',
        sessionId,
        workerId: 'e2e-wss-worker',
        status: null,
        generation: workerGeneration,
      },
    },
  });
}

async function seedUnread(page, sessionId, advance) {
  await api(page, '/__e2e/unread', { method: 'post', body: { sessionId, advance } });
}

/** Seed the durable legal Worker state (real projection drives the card/rail). */
async function seedLegalWorkerState(page, sessionId, state) {
  await api(page, '/__e2e/worker-legal-state', { method: 'post', body: { sessionId, state } });
}

async function setUnreadPolicyAll(page) {
  const response = await page.request.put(`${baseURL}/api/settings/ui`, {
    data: { notifications: { unreadRecording: ['all'] } },
  });
  assert.equal(response.ok(), true, `PUT settings failed: ${response.status()}`);
}

/**
 * Pause a Session's whole queue. The real enqueue wake-up can spawn a worker
 * that drains the pending row inside the assertion window; pausing keeps the
 * durable row pending (queuePendingCount stays > 0) without faking anything.
 */
async function setQueuePaused(page, sessionId, paused) {
  const response = await page.request.patch(`${baseURL}/api/sessions/${sessionId}/queue/paused`, {
    data: { paused },
  });
  assert.equal(response.ok(), true, `queue pause failed: ${response.status()}`);
  return response.json();
}

// ── UI helpers ──

function workspaceRow(page, id) {
  return page.locator(`[data-testid="workspace-tab-${id}"]`);
}

function allRow(page) {
  return page.locator('[data-workspace-tab-id="all"]');
}

function ungroupedRow(page) {
  return page.locator('[data-testid="workspace-tab-ungrouped"]');
}

function searchInput(page) {
  return page.getByRole('textbox', { name: 'Search workspaces' });
}

async function expandRail(page) {
  const handle = page.locator('button[title*="点击展开工作区"]').first();
  await handle.waitFor({ state: 'visible' });
  await handle.click();
  await page.locator('button[title="收起"]').first().waitFor({ state: 'visible' });
}

async function openFilterMenu(page) {
  const trigger = page.locator('button[title="Special filters"]').first();
  if ((await trigger.getAttribute('aria-expanded')) !== 'true') await trigger.click();
  await page.locator('[role="menu"]').first().waitFor({ state: 'visible' });
}

async function closeFilterMenu(page) {
  // The menu's click-away backdrop covers the trigger; click the backdrop
  // itself (top-left corner, clear of the menu panel) to dismiss.
  await page.locator('div.fixed.inset-0.z-20').first().click({ position: { x: 3, y: 3 } });
  await page.locator('[role="menu"]').first().waitFor({ state: 'detached' });
}

async function toggleSpecialFilter(page, label) {
  await openFilterMenu(page);
  const item = page.locator('[role="menuitemcheckbox"]').filter({ hasText: label }).first();
  await item.waitFor({ state: 'visible' });
  await item.click();
  await closeFilterMenu(page);
}

async function clearSpecialFilters(page) {
  await openFilterMenu(page);
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await closeFilterMenu(page);
}

// ── Fixture: two workspaces + one ungrouped session (durable, per runtime) ──

let wsAlpha;
let wsBeta;
let alphaId;
let bravoId;
let charlieId;
let dragId;

async function seedWorkspaceFixtures(page) {
  wsAlpha ??= await createWorkspace(page, 'Alpha WS');
  wsBeta ??= await createWorkspace(page, 'Beta WS');
  alphaId ??= await sessionIdOf(page, 'Alpha Session');
  bravoId ??= await sessionIdOf(page, 'Bravo Session');
  charlieId ??= await sessionIdOf(page, 'Charlie Session');
  dragId ??= await sessionIdOf(page, 'Drag 01');
  await moveToWorkspace(page, alphaId, wsAlpha);
  await moveToWorkspace(page, bravoId, wsBeta);
  // Charlie Session deliberately stays ungrouped.
  await page.waitForTimeout(400);
}

// Desktop and mobile exercise actual typing, independent clearing, focus and layout.
async function verifyIndependentSearch(page, mobile) {
  const sessionSearch = page.getByRole('textbox', { name: 'Search sessions' });
  const workspaceSearch = searchInput(page);
  await sessionSearch.fill('bravo session');
  await workspaceRow(page, wsAlpha).waitFor({ state: 'visible' });
  await workspaceRow(page, wsBeta).waitFor({ state: 'visible' });
  await workspaceSearch.pressSequentially('alpha ws');
  await workspaceRow(page, wsAlpha).waitFor({ state: 'visible' });
  await expectHidden(workspaceRow(page, wsBeta));
  assert.equal(await sessionSearch.inputValue(), 'bravo session');
  await page.locator(`[data-session-card-id="${bravoId}"]`).waitFor({ state: 'visible' });
  await expectHidden(page.locator(`[data-session-card-id="${alphaId}"]`));
  await allRow(page).waitFor({ state: 'visible' });
  await ungroupedRow(page).waitFor({ state: 'visible' });
  for (const key of ['Enter', 'ArrowDown', 'ArrowUp', 'Control+k', 'Control+1', 'Control+2', 'Control+b', 'Escape']) {
    await workspaceSearch.press(key);
    assert.equal(await workspaceSearch.evaluate((input) => input === document.activeElement), true);
  }
  const inputBox = await workspaceSearch.boundingBox();
  const allBox = await allRow(page).boundingBox();
  assert.ok(inputBox && allBox && inputBox.y + inputBox.height <= allBox.y);
  assert.ok(inputBox.x >= 0 && inputBox.x + inputBox.width <= (mobile ? MOBILE.width : DESKTOP.width));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.getByRole('button', { name: 'Clear session search', exact: true }).click();
  assert.equal(await workspaceSearch.inputValue(), 'alpha ws');
  await page.locator(`[data-session-card-id="${alphaId}"]`).waitFor({ state: 'visible' });
  await sessionSearch.fill('bravo session');
  if (mobile) await page.getByRole('button', { name: '收起工作区面板', exact: true }).click();
  else await page.locator('button[title="收起"]').click();
  assert.equal(await workspaceSearch.count(), 0);
  if (mobile) await page.getByRole('button', { name: '展开工作区', exact: true }).click();
  else await expandRail(page);
  assert.equal(await workspaceSearch.inputValue(), 'alpha ws');
  await expectHidden(workspaceRow(page, wsBeta));
  await page.getByRole('button', { name: 'Clear workspace search', exact: true }).click();
  assert.equal(await sessionSearch.inputValue(), 'bravo session');
  await workspaceRow(page, wsBeta).waitFor({ state: 'visible' });
  await expectHidden(page.locator(`[data-session-card-id="${alphaId}"]`));
  await workspaceSearch.fill('alpha ws'); // screenshot records the new input/layout
}

await runCase('desktop independent queries clear collapse focus and layout', DESKTOP, async (page) => {
  await seedWorkspaceFixtures(page);
  await expandRail(page);
  await verifyIndependentSearch(page, false);
});

// ── Case 3: running filter + live Session state changes ──

await runCase('running filter applies to workspaces and follows live session updates', DESKTOP, async (page) => {
  await seedWorkspaceFixtures(page);
  await expandRail(page);
  await toggleSpecialFilter(page, 'Running state');

  // Nothing is running yet: no workspace row, no virtual scope row.
  await expectHidden(workspaceRow(page, wsAlpha));
  await expectHidden(workspaceRow(page, wsBeta));
  await expectHidden(ungroupedRow(page));

  // A Session starts running → its Workspace row appears immediately.
  await broadcastWorkerStatus(page, alphaId, 'running', { lastLegalWorkerState: 'running' });
  await workspaceRow(page, wsAlpha).waitFor({ state: 'visible' });
  await expectHidden(workspaceRow(page, wsBeta));
  await allRow(page).waitFor({ state: 'visible' });
  await expectHidden(ungroupedRow(page));

  // A second Session starts running → the second Workspace row appears.
  await broadcastWorkerStatus(page, bravoId, 'running', { lastLegalWorkerState: 'running' });
  await workspaceRow(page, wsBeta).waitFor({ state: 'visible' });

  // Clearing the filters restores every row.
  await clearSpecialFilters(page);
  await workspaceRow(page, wsAlpha).waitFor({ state: 'visible' });
  await workspaceRow(page, wsBeta).waitFor({ state: 'visible' });
  await ungroupedRow(page).waitFor({ state: 'visible' });

  // Leave no active worker on the shared server for later cases.
  await clearWorkerStatus(page, alphaId);
  await clearWorkerStatus(page, bravoId);
});

// ── Case 4: unread filter with the authoritative unread policy ──

await runCase('unread filter uses the authoritative unread projection', DESKTOP, async (page) => {
  await setUnreadPolicyAll(page);
  await seedWorkspaceFixtures(page);
  await expandRail(page);
  await toggleSpecialFilter(page, 'Has unread');

  await expectHidden(workspaceRow(page, wsAlpha));
  await expectHidden(workspaceRow(page, wsBeta));
  await expectHidden(ungroupedRow(page));

  // Durable unread on the ungrouped session (real cursor projection).
  await seedUnread(page, charlieId, 1);
  await ungroupedRow(page).waitFor({ state: 'visible' });
  await expectHidden(workspaceRow(page, wsAlpha));

  // Durable unread inside Alpha WS → its row appears.
  await seedUnread(page, alphaId, 2);
  await workspaceRow(page, wsAlpha).waitFor({ state: 'visible' });
  await expectHidden(workspaceRow(page, wsBeta));

  await clearSpecialFilters(page);
  await workspaceRow(page, wsBeta).waitFor({ state: 'visible' });
});

// ── Case 5: queued-messages filter through the real durable queue ──

await runCase('queued messages filter reflects the real queue projection', DESKTOP, async (page) => {
  await seedWorkspaceFixtures(page);
  await expandRail(page);
  await toggleSpecialFilter(page, 'Has queued messages');

  await expectHidden(workspaceRow(page, wsAlpha));
  await expectHidden(workspaceRow(page, wsBeta));
  await expectHidden(ungroupedRow(page));

  // Real durable queue row. Enqueued on the UNGROUPED session: the product
  // wake-up path spawns a worker attempt for the target, which would otherwise
  // leak a running worker state onto a workspace member for later cases. The
  // queue is paused so the durable row cannot be drained mid-assertion.
  await setQueuePaused(page, dragId, true);
  const enqueued = await api(page, `/api/sessions/${dragId}/queue`, {
    method: 'post',
    body: { text: 'workspace shared search queue fixture', clientMessageId: `e2e-wss-${Date.now()}` },
  });
  assert.equal(enqueued.ok, true, `enqueue failed: ${JSON.stringify(enqueued)}`);
  // Queue events carry no session summary; nudge the authoritative session
  // refresh (the same broadcast shape /__e2e/unread uses) so the real
  // queuePendingCount projection reaches the rail.
  await injectSessionPatch(page, dragId, {});
  await ungroupedRow(page).waitFor({ state: 'visible', timeout: 10000 });
  await expectHidden(workspaceRow(page, wsAlpha));
  await expectHidden(workspaceRow(page, wsBeta));

  await clearSpecialFilters(page);
  await workspaceRow(page, wsAlpha).waitFor({ state: 'visible' });
  // Keep fixture queues paused until the isolated server is stopped.
});

// ── Case 6: illegal filter (legal running, actual not running) ──

await runCase('illegal filter matches only workspaces with an illegal member', DESKTOP, async (page) => {
  await seedWorkspaceFixtures(page);
  await expandRail(page);
  await toggleSpecialFilter(page, 'Illegal state');

  await expectHidden(workspaceRow(page, wsAlpha));
  await expectHidden(workspaceRow(page, wsBeta));
  await expectHidden(ungroupedRow(page));

  // Charlie: durable legal state running while the actual status is not
  // running → illegal.
  await seedLegalWorkerState(page, charlieId, 'running');
  await ungroupedRow(page).waitFor({ state: 'visible' });
  await expectHidden(workspaceRow(page, wsAlpha));
  await expectHidden(workspaceRow(page, wsBeta));

  // Alpha: legal running AND actually running → legal, must not match.
  await seedLegalWorkerState(page, alphaId, 'running');
  await broadcastWorkerStatus(page, alphaId, 'running');
  await page.waitForTimeout(500);
  await expectHidden(workspaceRow(page, wsAlpha));

  await clearSpecialFilters(page);
  await workspaceRow(page, wsAlpha).waitFor({ state: 'visible' });

  // Reset the durable legal state and worker summary for later cases.
  await seedLegalWorkerState(page, charlieId, null);
  await seedLegalWorkerState(page, alphaId, null);
  await clearWorkerStatus(page, alphaId);
});

// ── Case 7: keyword and status keep the original combination semantics ──

await runCase('keyword and status filters combine with the original semantics', DESKTOP, async (page) => {
  await seedWorkspaceFixtures(page);
  await expandRail(page);
  await toggleSpecialFilter(page, 'Running state');
  await broadcastWorkerStatus(page, alphaId, 'running', { lastLegalWorkerState: 'running' });
  await workspaceRow(page, wsAlpha).waitFor({ state: 'visible' });

  // Preconditions: the Running filter is really active, Alpha actually runs
  // and Bravo does not (a leaked worker state here would corrupt the case).
  const preRows = (await api(page, '/api/sessions?summary=1')).sessions;
  const bravoWorkerStatus = preRows.find((row) => row.id === bravoId)?.workerStatus ?? null;
  assert.equal(bravoWorkerStatus, null, 'Bravo unexpectedly reports a running worker');
  await page.locator('[data-session-card-id]').filter({ hasText: 'Alpha Session' }).first()
    .waitFor({ state: 'visible' });
  await expectHidden(page.locator('[data-session-card-id]').filter({ hasText: 'Bravo Session' }).first());

  // Name matches AND a member runs → visible.
  await searchInput(page).fill('alpha');
  await workspaceRow(page, wsAlpha).waitFor({ state: 'visible' });
  await expectHidden(workspaceRow(page, wsBeta));

  // Name matches but no member runs → hidden (Bravo is not running).
  await searchInput(page).fill('beta');
  await expectHidden(workspaceRow(page, wsBeta));
  await expectHidden(workspaceRow(page, wsAlpha));

  // A member runs but the name does not match → hidden.
  await searchInput(page).fill('zzz-no-match');
  await expectHidden(workspaceRow(page, wsAlpha));

  // Clearing the keyword keeps the status filter active.
  await searchInput(page).fill('');
  await workspaceRow(page, wsAlpha).waitFor({ state: 'visible' });
  await expectHidden(workspaceRow(page, wsBeta));

  await clearSpecialFilters(page);
  await workspaceRow(page, wsBeta).waitFor({ state: 'visible' });

  // Leave no active worker on the shared server for later cases.
  await clearWorkerStatus(page, alphaId);
});

// ── Case 8: the mobile drawer uses the same independent queries ──

await runCase('mobile drawer independent queries clear collapse focus and layout', MOBILE, async (page) => {
  await seedWorkspaceFixtures(page);
  await page.getByRole('button', { name: '打开侧边栏', exact: true }).click();
  await page.getByRole('button', { name: '展开工作区', exact: true }).click();
  await page.getByTestId('mobile-workspace-rail-expanded').waitFor({ state: 'visible' });
  await verifyIndependentSearch(page, true);
});

// ── Case 9: different members satisfy different status filters (AND at the Workspace level) ──

await runCase('different members satisfy different status filters', DESKTOP, async (page) => {
  await seedWorkspaceFixtures(page);
  await expandRail(page);

  // Dedicated workspaces so the durable moves cannot skew the cases above.
  // Gamma: one member per filter, no member carrying two of them.
  const gammaId = await createWorkspace(page, 'Gamma WS');
  const deltaId = await createWorkspace(page, 'Delta WS');
  const runningId = await sessionIdOf(page, 'Drag 02');
  const unreadId = await sessionIdOf(page, 'Drag 03');
  const illegalId = await sessionIdOf(page, 'Drag 05');
  const controlId = await sessionIdOf(page, 'Drag 06');
  await moveToWorkspace(page, runningId, gammaId);
  await moveToWorkspace(page, unreadId, gammaId);
  await moveToWorkspace(page, illegalId, gammaId);
  await moveToWorkspace(page, controlId, deltaId);
  await page.waitForTimeout(400);

  // Gamma: running (Drag 02) + unread (Drag 03) + illegal (Drag 05).
  await broadcastWorkerStatus(page, runningId, 'running', { lastLegalWorkerState: 'running' });
  await seedUnread(page, unreadId, 1);
  await seedLegalWorkerState(page, illegalId, 'running');
  // Delta keeps only a running member — no unread, no queue, no illegal.
  await broadcastWorkerStatus(page, controlId, 'running', { lastLegalWorkerState: 'running' });
  await page.waitForTimeout(400);

  // Precondition: with Running alone both workspaces match.
  await toggleSpecialFilter(page, 'Running state');
  await workspaceRow(page, gammaId).waitFor({ state: 'visible' });
  await workspaceRow(page, deltaId).waitFor({ state: 'visible' });

  // Running + unread: Gamma is satisfied by two DIFFERENT members, Delta is not
  // (the per-filter any() results are ANDed at the Workspace level).
  await toggleSpecialFilter(page, 'Has unread');
  await workspaceRow(page, gammaId).waitFor({ state: 'visible' });
  await expectHidden(workspaceRow(page, deltaId));

  await clearSpecialFilters(page);

  // The real durable queue satisfies the queue filter for yet another member.
  const queuedId = await sessionIdOf(page, 'Drag 04');
  await moveToWorkspace(page, queuedId, gammaId);
  await setQueuePaused(page, queuedId, true);
  const enqueued = await api(page, `/api/sessions/${queuedId}/queue`, {
    method: 'post',
    body: { text: 'workspace combination queue fixture', clientMessageId: `e2e-wss9-${Date.now()}` },
  });
  assert.equal(enqueued.ok, true, `enqueue failed: ${JSON.stringify(enqueued)}`);
  await injectSessionPatch(page, queuedId, {});

  // Queued + illegal: Gamma holds the queued member and the illegal member,
  // Delta holds neither.
  await toggleSpecialFilter(page, 'Has queued messages');
  await toggleSpecialFilter(page, 'Illegal state');
  await workspaceRow(page, gammaId).waitFor({ state: 'visible', timeout: 10000 });
  await expectHidden(workspaceRow(page, deltaId));

  await clearSpecialFilters(page);
  // Keep fixture queues paused until the isolated server is stopped.
  await clearWorkerStatus(page, runningId);
  await clearWorkerStatus(page, controlId);
});

// ── helpers used above (declared after use is fine: function hoisting) ──

async function expectHidden(locator) {
  await locator.waitFor({ state: 'hidden', timeout: 5000 });
}

await browser.close();
await fs.writeFile(
  path.join(runtime, 'browser-results.json'),
  JSON.stringify({ browser: browser.version(), baseURL, results }, null, 2),
);
for (const result of results) {
  console.log(`${result.status.toUpperCase()} ${result.name}`);
  if (result.error) console.error(result.error);
}
if (results.some((result) => result.status === 'failed')) process.exit(1);
