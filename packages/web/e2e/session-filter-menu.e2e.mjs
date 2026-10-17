/* global console, localStorage, process, document, innerWidth */
// Real-browser E2E for the Session filter menu: viewport-constrained scrollable
// window, collapsible Adapter/State second-level groups, multi-select with
// OR-in-group / AND-across-groups semantics, clear, count, keyboard and
// Escape/outside-click behavior, at desktop and 390px viewports.
//
// Runs against the isolated e2e/server.py instance: real FastAPI routes, real
// /ws broadcast path, real React build, real product API fixtures. Adapter
// fixtures are created through the real POST /api/sessions route; worker state
// is injected through the test-only /__e2e/broadcast route exactly like
// replayed worker events. Ports 8767/8768 are refused; the runner owns an
// isolated profile/cache and a dynamic port. No frontend mock is used: every
// assertion below judges the real product behavior.

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

const baseURL = process.env.PAN_E2E_BASE_URL || 'http://127.0.0.1:8765';
const runtime = process.env.PAN_E2E_RUNTIME || path.resolve('test-results/pan-e2e-session-filter-menu');
const artifacts = path.join(runtime, 'artifacts');
await fs.mkdir(artifacts, { recursive: true });

assert.ok(!/:8767|:8768/.test(baseURL), `refusing protected service ports: ${baseURL}`);

const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };
// Short narrow viewport: forces the expanded menu to scroll internally.
const SHORT_MOBILE = { width: 390, height: 600 };

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
  // panE2E exposes the read-only UI-store inspection seam for store-level
  // assertions (the same seam the workspace E2E family uses).
  await page.goto(`${baseURL}/react/?panE2E=1`);
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

// ── API helpers (real product routes on the isolated server) ──

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
        workerId: 'e2e-filter-worker',
        status,
        generation: workerGeneration,
        ...(session ? { session } : {}),
      },
    },
  });
}

async function clearWorkerStatus(page, sessionId) {
  workerGeneration += 1;
  await api(page, '/__e2e/broadcast', {
    method: 'post',
    body: {
      event: {
        type: 'worker.status',
        sessionId,
        workerId: 'e2e-filter-worker',
        status: null,
        generation: workerGeneration,
      },
    },
  });
}

// ── Fixtures: one session per adapter, created via the real product route ──
// The API validates session names as ^\S+$ (no whitespace), so fixtures use
// hyphenated names; the seeded server fixtures keep their spaced names.

const ADAPTER_FIXTURES = [
  ['Filter-Codex-One', 'codex'],
  ['Filter-Codex-Two', 'codex'],
  ['Filter-Kimi-One', 'kimi'],
  ['Filter-Opencode-One', 'opencode'],
  ['Filter-Claude-One', 'claude'],
];

async function seedAdapterFixtures(page) {
  for (const [name, adapter] of ADAPTER_FIXTURES) {
    const { sessions } = await api(page, '/api/sessions?summary=1');
    if (sessions.some((item) => item.name === name)) continue;
    const created = await api(page, '/api/sessions', {
      method: 'post',
      // mcpServers: [] is the product-route override that skips the default
      // template's MCP wiring (the isolated fixture config registers no MCP
      // server); it does not touch any other session field.
      body: { name, adapter, mcpServers: [] },
    });
    assert.ok(created.id, `create ${name} failed: ${JSON.stringify(created)}`);
  }
  // session.created broadcasts reach the live client; wait for the cards.
  await page.locator('[data-session-card-id]').filter({ hasText: 'Filter-Codex-One' }).first()
    .waitFor({ state: 'visible' });
}

// ── UI helpers ──

function filterTrigger(page) {
  return page.getByTestId('session-filter-trigger');
}

function filterMenu(page) {
  return page.getByTestId('session-filter-menu');
}

function groupHeader(page, group) {
  return page.getByTestId(`session-filter-group-${group}`);
}

function groupItem(page, text) {
  return page.locator('[role="menuitemcheckbox"]').filter({ hasText: text }).first();
}

async function openFilterMenu(page) {
  const trigger = filterTrigger(page);
  if ((await trigger.getAttribute('aria-expanded')) !== 'true') await trigger.click();
  await filterMenu(page).waitFor({ state: 'visible' });
}

async function closeFilterMenu(page) {
  // The click-away backdrop covers the trigger; click the backdrop itself
  // (top-left corner, clear of the menu panel) to dismiss.
  await page.locator('div.fixed.inset-0.z-20').first().click({ position: { x: 3, y: 3 } });
  await filterMenu(page).waitFor({ state: 'detached' });
}

async function toggleGroup(page, group) {
  const header = groupHeader(page, group);
  const before = await header.getAttribute('aria-expanded');
  await header.click();
  await page.waitForFunction(
    ([selector, expected]) =>
      document.querySelector(selector)?.getAttribute('aria-expanded') !== expected,
    [`[data-testid="session-filter-group-${group}"]`, before],
  );
  assert.equal(await header.getAttribute('aria-expanded'), before === 'true' ? 'false' : 'true');
}

/** Expand a group if it is collapsed (idempotent; State starts expanded). */
async function ensureGroupExpanded(page, group) {
  const header = groupHeader(page, group);
  if ((await header.getAttribute('aria-expanded')) !== 'true') await header.click();
  await page.waitForFunction(
    (selector) => document.querySelector(selector)?.getAttribute('aria-expanded') === 'true',
    `[data-testid="session-filter-group-${group}"]`,
  );
}

function card(page, name) {
  return page.locator('[data-session-card-id]').filter({ hasText: name }).first();
}

async function expectHidden(locator) {
  await locator.waitFor({ state: 'hidden', timeout: 5000 });
}

function uiStoreState(page) {
  return page.evaluate(() => {
    const store = window.__panUIStore?.getState?.();
    return {
      specialFilters: [...(store?.specialFilters ?? [])],
      adapterFilters: [...(store?.adapterFilters ?? [])],
    };
  });
}

/** The menu panel must never leave the viewport (either axis). */
async function assertMenuInsideViewport(page) {
  const viewport = page.viewportSize();
  const box = await filterMenu(page).boundingBox();
  assert.ok(box, 'filter menu has no bounding box');
  assert.ok(box.x >= 0, `menu overflows left: ${box.x}`);
  assert.ok(box.y >= 0, `menu overflows top: ${box.y}`);
  assert.ok(box.x + box.width <= viewport.width + 0.5, `menu overflows right: ${box.x + box.width} > ${viewport.width}`);
  assert.ok(box.y + box.height <= viewport.height + 0.5, `menu overflows bottom: ${box.y + box.height} > ${viewport.height}`);
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    true,
    'page scrolls horizontally',
  );
}

// ── Case 1: structure + viewport constraint (desktop) ──

await runCase('desktop menu structure and viewport constraint', DESKTOP, async (page) => {
  await seedAdapterFixtures(page);
  await openFilterMenu(page);
  assert.equal(await filterTrigger(page).getAttribute('aria-expanded'), 'true');

  // Two collapsible second-level groups: Adapter collapsed, State expanded
  // (the pre-existing filters stay immediately available).
  assert.equal(await groupHeader(page, 'adapter').getAttribute('aria-expanded'), 'false');
  assert.equal(await groupHeader(page, 'state').getAttribute('aria-expanded'), 'true');
  assert.equal(await groupHeader(page, 'adapter').textContent(), 'Adapter');
  assert.equal(await groupHeader(page, 'state').textContent(), 'State');
  assert.equal(await page.locator('#session-filter-adapter-items').count(), 0);
  assert.equal(await page.locator('#session-filter-state-items [role="menuitemcheckbox"]').count(), 6);

  await assertMenuInsideViewport(page);
  await closeFilterMenu(page);
});

// ── Case 2: adapter options come from the live session list (desktop) ──

await runCase('desktop adapter options derive from the live session list', DESKTOP, async (page) => {
  await seedAdapterFixtures(page);
  await openFilterMenu(page);
  await ensureGroupExpanded(page, 'adapter');

  // Exactly the adapters the real sessions carry (cbc seeded + 4 created).
  for (const adapter of ['cbc', 'claude', 'codex', 'kimi', 'opencode']) {
    await groupItem(page, adapter).waitFor({ state: 'visible' });
  }
  assert.equal(
    await page.locator('#session-filter-adapter-items [role="menuitemcheckbox"]').count(),
    5,
  );
  // The State group keeps its six pre-existing filters.
  assert.equal(await page.locator('#session-filter-state-items [role="menuitemcheckbox"]').count(), 6);
  await closeFilterMenu(page);
});

// ── Case 3: multi-select OR, collapse preserves checkmarks, menu stays open ──

await runCase('desktop multi-select adapters are OR-ed and survive collapse', DESKTOP, async (page) => {
  await seedAdapterFixtures(page);
  await openFilterMenu(page);
  await toggleGroup(page, 'adapter');

  // One adapter: only its sessions remain.
  await groupItem(page, 'codex').click();
  await card(page, 'Filter-Codex-One').waitFor({ state: 'visible' });
  await card(page, 'Filter-Codex-Two').waitFor({ state: 'visible' });
  await expectHidden(card(page, 'Filter-Kimi-One'));
  await expectHidden(card(page, 'Alpha Session')); // seeded cbc session
  assert.deepEqual((await uiStoreState(page)).adapterFilters, ['codex']);
  // Checking an item does NOT close the menu.
  await filterMenu(page).waitFor({ state: 'visible' });

  // Second adapter: OR union.
  await groupItem(page, 'kimi').click();
  await card(page, 'Filter-Kimi-One').waitFor({ state: 'visible' });
  await card(page, 'Filter-Codex-One').waitFor({ state: 'visible' });
  await expectHidden(card(page, 'Alpha Session'));

  // Collapsing the group keeps the selection and the filtered list.
  await toggleGroup(page, 'adapter');
  await card(page, 'Filter-Kimi-One').waitFor({ state: 'visible' });
  await expectHidden(card(page, 'Alpha Session'));
  assert.deepEqual((await uiStoreState(page)).adapterFilters, ['codex', 'kimi']);

  // Re-expanding shows both checkmarks still on.
  await toggleGroup(page, 'adapter');
  assert.equal(await groupItem(page, 'codex').getAttribute('aria-checked'), 'true');
  assert.equal(await groupItem(page, 'kimi').getAttribute('aria-checked'), 'true');
  assert.equal(await groupItem(page, 'cbc').getAttribute('aria-checked'), 'false');

  // Unchecking everything restores the unfiltered list.
  await groupItem(page, 'codex').click();
  await groupItem(page, 'kimi').click();
  await card(page, 'Alpha Session').waitFor({ state: 'visible' });
  assert.deepEqual((await uiStoreState(page)).adapterFilters, []);
  await closeFilterMenu(page);
});

// ── Case 4: cross-group AND with the real worker projection ──

await runCase('desktop adapter and state filters combine with AND', DESKTOP, async (page) => {
  await seedAdapterFixtures(page);
  const codexOneId = await sessionIdOf(page, 'Filter-Codex-One');
  const alphaId = await sessionIdOf(page, 'Alpha Session');

  await openFilterMenu(page);
  await toggleGroup(page, 'adapter');
  await groupItem(page, 'codex').click();
  await card(page, 'Filter-Codex-One').waitFor({ state: 'visible' });

  // Real worker projection: Codex One actually runs.
  await broadcastWorkerStatus(page, codexOneId, 'running', { lastLegalWorkerState: 'running' });
  await ensureGroupExpanded(page, 'state');
  await groupItem(page, 'Running state').click();

  // Running AND codex → only the running codex session.
  await card(page, 'Filter-Codex-One').waitFor({ state: 'visible' });
  await expectHidden(card(page, 'Filter-Codex-Two'));

  // A RUNNING session of another adapter stays hidden (cross-group AND).
  await broadcastWorkerStatus(page, alphaId, 'running', { lastLegalWorkerState: 'running' });
  await page.waitForTimeout(400);
  await expectHidden(card(page, 'Alpha Session'));

  // Dropping the adapter group keeps the state filter (running sessions only).
  await groupItem(page, 'codex').click();
  await card(page, 'Alpha Session').waitFor({ state: 'visible' });
  await card(page, 'Filter-Codex-One').waitFor({ state: 'visible' });
  await expectHidden(card(page, 'Filter-Kimi-One'));

  await clearWorkerStatus(page, codexOneId);
  await clearWorkerStatus(page, alphaId);
  await closeFilterMenu(page);
});

// ── Case 5: count badge + clear filters ──

await runCase('desktop count badge and clear filters', DESKTOP, async (page) => {
  await seedAdapterFixtures(page);
  assert.equal(await page.getByTestId('session-filter-count').count(), 0);

  await openFilterMenu(page);
  await toggleGroup(page, 'adapter');
  await groupItem(page, 'kimi').click();
  await ensureGroupExpanded(page, 'state');
  await groupItem(page, 'Has unread').click();
  assert.equal(await page.getByTestId('session-filter-count').textContent(), '2');
  await expectHidden(card(page, 'Alpha Session'));

  // Clear filters restores everything and hides the badge; the menu stays open.
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await card(page, 'Alpha Session').waitFor({ state: 'visible' });
  assert.equal(await page.getByTestId('session-filter-count').count(), 0);
  assert.deepEqual(await uiStoreState(page), { specialFilters: [], adapterFilters: [] });
  await filterMenu(page).waitFor({ state: 'visible' });
  await closeFilterMenu(page);
});

// ── Case 6: Escape, outside click, keyboard operation ──

await runCase('desktop escape outside click and keyboard operation', DESKTOP, async (page) => {
  await seedAdapterFixtures(page);

  // Escape closes and returns focus to the trigger.
  await openFilterMenu(page);
  await page.keyboard.press('Escape');
  await filterMenu(page).waitFor({ state: 'detached' });
  assert.equal(await filterTrigger(page).getAttribute('aria-expanded'), 'false');
  assert.equal(
    await page.evaluate(() => document.activeElement?.getAttribute('data-testid')),
    'session-filter-trigger',
  );

  // Outside click on the backdrop closes.
  await openFilterMenu(page);
  await page.locator('div.fixed.inset-0.z-20').first().click({ position: { x: 3, y: 3 } });
  await filterMenu(page).waitFor({ state: 'detached' });

  // Keyboard: Enter on the focused group header expands it.
  await openFilterMenu(page);
  await groupHeader(page, 'adapter').focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction(
    () => document.querySelector('[data-testid="session-filter-group-adapter"]')?.getAttribute('aria-expanded') === 'true',
  );

  // Keyboard: Space on a focused checkbox toggles it and filters the list.
  const codexBox = groupItem(page, 'codex').locator('input[type="checkbox"]');
  await codexBox.focus();
  await page.keyboard.press('Space');
  await card(page, 'Filter-Codex-One').waitFor({ state: 'visible' });
  await expectHidden(card(page, 'Alpha Session'));
  assert.deepEqual((await uiStoreState(page)).adapterFilters, ['codex']);

  // Keyboard: Escape from inside the menu closes it, selection is retained.
  await page.keyboard.press('Escape');
  await filterMenu(page).waitFor({ state: 'detached' });
  assert.deepEqual((await uiStoreState(page)).adapterFilters, ['codex']);
  await card(page, 'Filter-Codex-One').waitFor({ state: 'visible' });
});

// ── Case 7: 390px — no overflow, menu inside viewport, filtering works ──

await runCase('mobile 390px menu stays inside the viewport and filters', MOBILE, async (page) => {
  await seedAdapterFixtures(page);
  await page.getByRole('button', { name: '打开侧边栏', exact: true }).click();
  await openFilterMenu(page);
  await assertMenuInsideViewport(page);

  await toggleGroup(page, 'adapter');
  await groupItem(page, 'kimi').click();
  await card(page, 'Filter-Kimi-One').waitFor({ state: 'visible' });
  await expectHidden(card(page, 'Alpha Session'));
  await assertMenuInsideViewport(page);

  await ensureGroupExpanded(page, 'state');
  await assertMenuInsideViewport(page);
  await closeFilterMenu(page);
});

// ── Case 8: short narrow viewport — internal scroll, last item reachable ──

await runCase('short 390px menu scrolls internally with the last item reachable', SHORT_MOBILE, async (page) => {
  await seedAdapterFixtures(page);
  await page.getByRole('button', { name: '打开侧边栏', exact: true }).click();
  await openFilterMenu(page);
  await toggleGroup(page, 'adapter');
  await ensureGroupExpanded(page, 'state');

  // The panel is viewport-constrained and must scroll internally.
  await assertMenuInsideViewport(page);
  const metrics = await filterMenu(page).evaluate((el) => {
    const scroller = el.querySelector('.overflow-y-auto') ?? el;
    return {
      scrollHeight: scroller.scrollHeight,
      clientHeight: scroller.clientHeight,
      menuHeight: el.getBoundingClientRect().height,
    };
  });
  assert.ok(
    metrics.scrollHeight > metrics.clientHeight,
    `expanded menu does not need internal scrolling: ${JSON.stringify(metrics)}`,
  );

  // Activate one filter so the pinned Clear action exists; it must stay
  // visible without scrolling the group list.
  await groupItem(page, 'kimi').click();
  const clearButton = page.getByRole('button', { name: 'Clear filters' });
  await clearButton.waitFor({ state: 'visible' });
  const menuBox = await filterMenu(page).boundingBox();
  const clearBox = await clearButton.boundingBox();
  assert.ok(clearBox && menuBox, 'clear button or menu has no box');
  assert.ok(
    clearBox.y + clearBox.height <= menuBox.y + menuBox.height + 0.5,
    `pinned Clear action is cut off: ${JSON.stringify({ clearBox, menuBox })}`,
  );

  // Scroll the group list to the very bottom and operate the last item.
  await filterMenu(page).evaluate((el) => {
    const scroller = el.querySelector('.overflow-y-auto') ?? el;
    scroller.scrollTop = scroller.scrollHeight;
  });
  const lastItem = groupItem(page, 'Has queued messages');
  await lastItem.waitFor({ state: 'visible' });
  const lastBox = await lastItem.boundingBox();
  assert.ok(lastBox && menuBox, 'last item or menu has no box');
  assert.ok(
    lastBox.y >= menuBox.y - 0.5 && lastBox.y + lastBox.height <= menuBox.y + menuBox.height + 0.5,
    `last item is outside the menu panel: ${JSON.stringify({ lastBox, menuBox })}`,
  );
  await lastItem.click();
  assert.deepEqual((await uiStoreState(page)).specialFilters, ['queue']);
  assert.deepEqual((await uiStoreState(page)).adapterFilters, ['kimi']);

  // Clear filters restores the full list; the menu stays open.
  await clearButton.click();
  await card(page, 'Alpha Session').waitFor({ state: 'visible' });
  assert.deepEqual(await uiStoreState(page), { specialFilters: [], adapterFilters: [] });
  await filterMenu(page).waitFor({ state: 'visible' });
  await closeFilterMenu(page);
});

// ── helpers used above (declared after use is fine: function hoisting) ──

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
