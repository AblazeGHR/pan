/* global localStorage, console, process */
// Real-browser E2E for the global unread-recording policy (App Settings →
// Notification). Runs against the isolated e2e/server.py instance: real
// FastAPI routes, real /ws broadcast path, real React build. Durable unread
// cursors are seeded through the test-only /__e2e/unread route to simulate
// legacy/durable store state; every projection and coordination path is real.

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

const baseURL = process.env.PAN_E2E_BASE_URL || 'http://127.0.0.1:8765';
const runtime = process.env.PAN_E2E_RUNTIME || path.resolve('test-results/pan-e2e-unread-policy');
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
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  await page.goto(`${baseURL}/react/`);
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  return { context, page };
}

async function runCase(name, viewport, body) {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  let context;
  let page;
  const started = new Date().toISOString();
  try {
    ({ context, page } = await openPage(viewport));
    await body(page);
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

async function setPolicy(page, scopes) {
  const response = await page.request.put(`${baseURL}/api/settings/ui`, {
    data: { notifications: { unreadRecording: scopes } },
  });
  assert.equal(response.ok(), true, `PUT settings failed: ${response.status()}`);
  return response.json();
}

async function readPolicy(page) {
  const response = await page.request.get(`${baseURL}/api/settings/ui`);
  return (await response.json()).notifications.unreadRecording;
}

async function readSummaryRow(page, name) {
  const response = await page.request.get(`${baseURL}/api/sessions?summary=1`);
  assert.equal(response.ok(), true);
  return (await response.json()).sessions.find((row) => row.name === name);
}

async function seedUnread(page, sessionId, control) {
  const response = await page.request.post(`${baseURL}/__e2e/unread`, {
    data: { sessionId, ...control },
  });
  assert.equal(response.ok(), true, `seed unread failed: ${response.status()}`);
  return response.json();
}

function badgeLocator(page, sessionId) {
  return page.locator(`[data-session-card-id="${sessionId}"] [data-testid="session-unread-done-badge"]`);
}

async function sessionIdOf(page, name) {
  const card = page.locator('[data-session-card-id]').filter({ hasText: name }).first();
  await card.waitFor({ state: 'visible' });
  const sessionId = await card.getAttribute('data-session-card-id');
  assert.ok(sessionId, `no session id for ${name}`);
  return sessionId;
}

async function openNotificationSettings(page) {
  // Mobile: the settings button lives in the sidebar drawer; reveal it first
  // (the hamburger only renders while the drawer is closed).
  const hamburger = page.locator('button[aria-label="打开侧边栏"]');
  if (await hamburger.count() > 0) {
    await hamburger.click();
  }
  await page.locator('button[title="App settings"]:visible').first().click();
  await page.getByRole('tab', { name: 'Notification' }).click();
  await page.getByTestId('unread-recording-panel').waitFor({ state: 'visible' });
}

async function savePolicyViaUi(page, scope) {
  await page.getByTestId(`unread-recording-${scope}`).click();
  await page.getByTestId('unread-recording-save').click();
  await page.getByText(/Saved\. Excluded Sessions keep no unread/).waitFor({ state: 'visible' });
}

// ── Case 1: saving "No sessions" clears durable unread and hides it everywhere ──

await runCase('policy save clears existing unread and hides badges desktop', DESKTOP, async (page) => {
  await setPolicy(page, ['all']);
  const sessionId = await sessionIdOf(page, 'Alpha Session');
  await seedUnread(page, sessionId, { advance: 2 });
  await badgeLocator(page, sessionId).waitFor({ state: 'visible' });

  await openNotificationSettings(page);
  await savePolicyViaUi(page, 'none');
  assert.deepEqual(await readPolicy(page), ['none']);

  await badgeLocator(page, sessionId).waitFor({ state: 'hidden' });
  assert.equal((await readSummaryRow(page, 'Alpha Session')).unreadDoneCount, 0);

  // A new done while excluded stays invisible (authoritative zero, no badge).
  await seedUnread(page, sessionId, { advance: 1 });
  await page.waitForTimeout(400);
  assert.equal(await badgeLocator(page, sessionId).count(), 0);
  assert.equal((await readSummaryRow(page, 'Alpha Session')).unreadDoneCount, 0);
});

// ── Case 2: re-included Sessions count from new completions only ──

await runCase('reincluded sessions count from new completions only', DESKTOP, async (page) => {
  await setPolicy(page, ['none']);
  const sessionId = await sessionIdOf(page, 'Bravo Session');
  await seedUnread(page, sessionId, { advance: 2 }); // durable leftover while excluded
  await page.waitForTimeout(300);
  assert.equal((await readSummaryRow(page, 'Bravo Session')).unreadDoneCount, 0);

  await setPolicy(page, ['all']);
  await page.waitForTimeout(400);
  assert.equal(
    (await readSummaryRow(page, 'Bravo Session')).unreadDoneCount, 0,
    're-including must not restore older unread',
  );
  assert.equal(await badgeLocator(page, sessionId).count(), 0);

  await seedUnread(page, sessionId, { advance: 1 }); // the next real done generation
  await badgeLocator(page, sessionId).waitFor({ state: 'visible' });
  assert.equal((await badgeLocator(page, sessionId).textContent())?.trim(), '1');
});

// ── Case 3: panel exclusivity rules (bell/pin/all/none) ──

await runCase('unread recording panel exclusivity rules', DESKTOP, async (page) => {
  await setPolicy(page, ['all']);
  await openNotificationSettings(page);
  const checked = (scope) => page.getByTestId(`unread-recording-${scope}`).isChecked();
  assert.equal(await checked('all'), true);

  await page.getByTestId('unread-recording-bell-on').click();
  assert.equal(await checked('all'), false);
  assert.equal(await checked('bell-on'), true);

  await page.getByTestId('unread-recording-bell-off').click();
  assert.equal(await checked('bell-on'), false, 'bell on and bell off are mutually exclusive');
  assert.equal(await checked('bell-off'), true);

  await page.getByTestId('unread-recording-pinned').click();
  assert.equal(await checked('bell-off'), true);
  assert.equal(await checked('pinned'), true);

  await page.getByTestId('unread-recording-all').click();
  assert.equal(await checked('all'), true);
  assert.equal(await checked('bell-off'), false, 'All sessions clears the categories');
  assert.equal(await checked('pinned'), false);
  assert.equal(await checked('none'), false);

  await page.getByTestId('unread-recording-all').click();
  assert.equal(await checked('none'), true, 'empty set falls back to No sessions');
  assert.equal(await checked('all'), false);
});

// ── Case 4: mobile Notification tab drives the same policy ──

await runCase('mobile notification panel saves policy', MOBILE, async (page) => {
  await setPolicy(page, ['all']);
  await page.locator('button[aria-label="打开侧边栏"]').click();
  const sessionId = await sessionIdOf(page, 'Charlie Session');
  await seedUnread(page, sessionId, { advance: 2 });

  await openNotificationSettings(page);
  await savePolicyViaUi(page, 'none');
  assert.deepEqual(await readPolicy(page), ['none']);
  assert.equal((await readSummaryRow(page, 'Charlie Session')).unreadDoneCount, 0);
});

// ── Case 5: workspace rail unread dot follows the policy; settings sync cross-client ──

async function runWorkspaceAndSyncCase() {
  const name = 'workspace rail dot and cross-client settings sync';
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  const started = new Date().toISOString();
  const contextA = await browser.newContext({ viewport: DESKTOP, deviceScaleFactor: 1 });
  const contextB = await browser.newContext({ viewport: DESKTOP, deviceScaleFactor: 1 });
  await contextA.tracing.start({ screenshots: true, snapshots: true, sources: true });
  let pageA;
  let pageB;
  try {
    pageA = await contextA.newPage();
    await pageA.goto(`${baseURL}/react/`);
    await pageA.evaluate(() => localStorage.clear());
    await pageA.reload();
    await pageA.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
    pageB = await contextB.newPage();
    await pageB.goto(`${baseURL}/react/`);
    await pageB.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });

    // One workspace owning a recorded Session with unread.
    const created = await pageA.request.post(`${baseURL}/api/workspaces`, {
      data: { name: `Unread Policy WS ${Date.now()}` },
    });
    assert.equal(created.ok(), true);
    const workspaceId = (await created.json()).workspace.id;
    const sessionId = await sessionIdOf(pageA, 'Alpha Session');
    const moved = await pageA.request.put(`${baseURL}/api/sessions/${encodeURIComponent(sessionId)}/workspaces`, {
      data: { workspaceIds: [workspaceId] },
    });
    assert.equal(moved.ok(), true);

    await setPolicy(pageA, ['all']);
    await seedUnread(pageA, sessionId, { advance: 2 });
    // The desktop rail starts collapsed (width 0); expand it with the
    // existing handle so the dot's visibility reflects the unread state.
    await pageA.getByRole('button', { name: '全部' }).click();
    const railDot = pageA.locator(`[data-testid="workspace-unread-${workspaceId}"]`);
    await railDot.waitFor({ state: 'visible' });

    // Client B opens the Notification tab; client A (via API) switches the
    // policy to none. The rail dot must vanish and B must reload the setting.
    await openNotificationSettings(pageB);
    await setPolicy(pageA, ['none']);
    await railDot.waitFor({ state: 'hidden' });
    assert.equal((await readSummaryRow(pageA, 'Alpha Session')).unreadDoneCount, 0);
    await pageB.getByTestId('unread-recording-none').waitFor({ state: 'visible' });
    await assertEventually(async () => pageB.getByTestId('unread-recording-none').isChecked(), 'client B did not reload the persisted policy');

    await contextA.tracing.stop({ path: path.join(artifacts, `${slug}.zip`) });
    await pageA.screenshot({ path: path.join(artifacts, `${slug}-a.png`), fullPage: true });
    await pageB.screenshot({ path: path.join(artifacts, `${slug}-b.png`), fullPage: true });
    results.push({ name, status: 'passed', started, screenshot: `${slug}-a.png` });
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    if (pageA) await pageA.screenshot({ path: path.join(artifacts, `${slug}-failure.png`), fullPage: true }).catch(() => {});
    await contextA.tracing.stop({ path: path.join(artifacts, `${slug}-failure.zip`) }).catch(() => {});
    results.push({ name, status: 'failed', started, error: message, screenshot: `${slug}-failure.png` });
  } finally {
    await contextA.close();
    await contextB.close();
  }
}

async function assertEventually(read, message, timeout = 5000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    last = await read();
    if (last) return last;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`${message} (last=${JSON.stringify(last)})`);
}

await runWorkspaceAndSyncCase();

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
