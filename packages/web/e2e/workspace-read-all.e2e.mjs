/* global console, localStorage, process */
// Real-browser E2E for the Workspace menu "Read all" action.
// Runs against the isolated e2e/server.py instance: real FastAPI routes, real
// /ws broadcast path, real React build, disposable session fixtures. Workspace
// membership, management-tree inheritance and durable unread cursors are all
// seeded through the product routes; the assertion reads the durable
// projection back from /api/sessions?summary=1. Ports 8767/8768 are refused;
// the runner owns an isolated profile/cache and a dynamic port.

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

const baseURL = process.env.PAN_E2E_BASE_URL || 'http://127.0.0.1:8765';
const runtime = process.env.PAN_E2E_RUNTIME || path.resolve('test-results/pan-e2e-workspace-read-all');
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
  const response = await page.request[options.method ?? 'get'](`${baseURL}${route}`, { data: options.body });
  assert.equal(response.ok(), true, `${options.method ?? 'GET'} ${route} failed: ${response.status()}`);
  return response.json();
}

async function sessionRow(page, name) {
  const { sessions } = await api(page, '/api/sessions?summary=1');
  const row = sessions.find((item) => item.name === name);
  assert.ok(row, `session ${name} not found`);
  return row;
}

async function sessionIdOf(page, name) {
  return (await sessionRow(page, name)).id;
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

async function claimManaged(page, managerId, sessionId) {
  const result = await api(page, '/api/claim', { method: 'post', body: { managerId, sessionId } });
  assert.equal(result.ok, true, `claim failed: ${JSON.stringify(result)}`);
}

async function seedUnread(page, sessionId, advance) {
  await api(page, '/__e2e/unread', { method: 'post', body: { sessionId, advance } });
}

async function setUnreadPolicyAll(page) {
  const response = await page.request.put(`${baseURL}/api/settings/ui`, {
    data: { notifications: { unreadRecording: ['all'] } },
  });
  assert.equal(response.ok(), true, `PUT settings failed: ${response.status()}`);
}

// ── UI helpers ──

function workspaceRow(page, id) {
  return page.locator(`[data-testid="workspace-tab-${id}"]`);
}

function unreadDot(page, id) {
  return page.locator(`[data-testid="workspace-unread-${id}"]`);
}

function searchInput(page) {
  return page.locator('input[placeholder="Filter..."]');
}

async function expandRail(page) {
  const handle = page.locator('button[title*="点击展开工作区"]').first();
  await handle.waitFor({ state: 'visible' });
  await handle.click();
  await page.locator('button[title="收起"]').first().waitFor({ state: 'visible' });
}

/** Open one workspace row's "more" menu and click the English Read all entry. */
async function readAll(page, workspaceId) {
  const row = workspaceRow(page, workspaceId);
  await row.waitFor({ state: 'visible' });
  // Desktop reveals the trigger on row hover; the mobile drawer shows it always.
  await row.hover();
  const trigger = row.locator('button[title="更多"]');
  await trigger.waitFor({ state: 'visible' });
  await trigger.click();
  const item = page.getByTestId(`workspace-read-all-${workspaceId}`);
  await item.waitFor({ state: 'visible' });
  await item.click();
}

async function waitForUnread(page, name, expected) {
  const deadline = Date.now() + 8000;
  let row = await sessionRow(page, name);
  while (row.unreadDoneCount !== expected && Date.now() < deadline) {
    await page.waitForTimeout(120);
    row = await sessionRow(page, name);
  }
  assert.equal(row.unreadDoneCount, expected, `${name} unreadDoneCount`);
}

// ── Fixture: ws-alpha (root + inherited child), ws-beta, one ungrouped ──

let wsAlpha;
let wsBeta;
let alphaId;
let bravoId;
let charlieId;
let dragId;

async function seedFixtures(page) {
  await setUnreadPolicyAll(page);
  wsAlpha ??= await createWorkspace(page, 'Alpha WS');
  wsBeta ??= await createWorkspace(page, 'Beta WS');
  alphaId ??= await sessionIdOf(page, 'Alpha Session');
  bravoId ??= await sessionIdOf(page, 'Bravo Session');
  charlieId ??= await sessionIdOf(page, 'Charlie Session');
  dragId ??= await sessionIdOf(page, 'Drag 01');
  if (!globalThis.__wssFixturesPlaced) {
    await moveToWorkspace(page, alphaId, wsAlpha);
    await moveToWorkspace(page, bravoId, wsBeta);
    // Charlie becomes a managed descendant of the ws-alpha root → inherits it.
    await claimManaged(page, alphaId, charlieId);
    globalThis.__wssFixturesPlaced = true;
  }
  await page.waitForTimeout(400);
}

// ── Case 1: Read all covers every member (incl. inherited) and nothing else ──

await runCase('read all clears workspace members including inherited children', DESKTOP, async (page) => {
  await seedFixtures(page);
  await seedUnread(page, alphaId, 2);
  await seedUnread(page, charlieId, 1);
  await seedUnread(page, bravoId, 1);
  await seedUnread(page, dragId, 1);
  assert.equal((await sessionRow(page, 'Charlie Session')).managedBy, alphaId, 'child does not inherit the manager');

  await expandRail(page);
  await unreadDot(page, wsAlpha).waitFor({ state: 'visible' });

  await readAll(page, wsAlpha);

  // Both effective members settle (the inherited child is included).
  await waitForUnread(page, 'Alpha Session', 0);
  await waitForUnread(page, 'Charlie Session', 0);
  // Other workspaces and the ungrouped session are untouched.
  assert.equal((await sessionRow(page, 'Bravo Session')).unreadDoneCount, 1);
  assert.equal((await sessionRow(page, 'Drag 01')).unreadDoneCount, 1);
  await unreadDot(page, wsAlpha).waitFor({ state: 'hidden' });
  await page.getByText('2 succeeded, 0 failed').waitFor({ state: 'visible', timeout: 4000 });
});

// ── Case 2: unread completing after the click is not cleared ──

await runCase('read all preserves unread that arrives after the click', DESKTOP, async (page) => {
  await seedFixtures(page);
  await seedUnread(page, alphaId, 1);
  await expandRail(page);
  await unreadDot(page, wsAlpha).waitFor({ state: 'visible' });

  await readAll(page, wsAlpha);
  await waitForUnread(page, 'Alpha Session', 0);

  // A new completion lands after the click: it must stay unread.
  await seedUnread(page, alphaId, 1);
  await waitForUnread(page, 'Alpha Session', 1);
  await unreadDot(page, wsAlpha).waitFor({ state: 'visible' });
});

// ── Case 3: targets are not limited to the search/filter-visible items ──

await runCase('read all reaches members hidden by the shared search box', DESKTOP, async (page) => {
  await seedFixtures(page);
  await seedUnread(page, alphaId, 1);
  await seedUnread(page, charlieId, 1);
  await expandRail(page);
  await unreadDot(page, wsAlpha).waitFor({ state: 'visible' });

  // The keyword matches only the workspace NAME: every Session card hides
  // while the workspace row stays visible.
  await searchInput(page).fill('alpha ws');
  await page.waitForTimeout(300);
  assert.equal(await page.locator('[data-session-card-id]').count(), 0, 'session cards were expected to hide');
  await workspaceRow(page, wsAlpha).waitFor({ state: 'visible' });

  await readAll(page, wsAlpha);

  await waitForUnread(page, 'Alpha Session', 0);
  await waitForUnread(page, 'Charlie Session', 0);
  await searchInput(page).fill('');
});

// ── Case 4: the mobile drawer menu exposes Read all and it works ──

await runCase('mobile drawer workspace menu exposes read all', MOBILE, async (page) => {
  await seedFixtures(page);
  await seedUnread(page, bravoId, 1);
  await page.locator('button[aria-label="打开侧边栏"]').click();
  const mobileHandle = page.locator('button[aria-label="展开工作区"]').first();
  await mobileHandle.waitFor({ state: 'visible' });
  await mobileHandle.click();
  await page.getByTestId('mobile-workspace-rail-expanded').waitFor({ state: 'visible' });

  await unreadDot(page, wsBeta).waitFor({ state: 'visible' });
  await readAll(page, wsBeta);

  await waitForUnread(page, 'Bravo Session', 0);
  await unreadDot(page, wsBeta).waitFor({ state: 'hidden' });
  await page.getByText('1 succeeded, 0 failed').waitFor({ state: 'visible', timeout: 4000 });
});

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
