/* global localStorage, console, process, setTimeout */
// Real-browser E2E for the Session info-area Job indicator (J).
// Runs against the isolated e2e/server.py instance: real FastAPI routes, the
// real /api/jobs backend, the real /ws broadcast path and the real React
// build. Creates real Jobs (background-process + session-message) targeting
// seeded fixture Sessions, then verifies the aggregated J badge:
//   - running  -> blue (accent), same geometry/font as the numeric badges;
//   - scheduled -> yellow (warning);
//   - many jobs per Session aggregate into a single J, running wins;
//   - mobile widths hide J (max-md) while numeric badges stay right-aligned;
//   - a terminal/cancelled job removes the badge via the WS path.
// Artifacts (screenshots + traces) land in the runtime artifacts directory.

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

const baseURL = process.env.PAN_E2E_BASE_URL || 'http://127.0.0.1:8765';
const runtime = process.env.PAN_E2E_RUNTIME || path.resolve('test-results/pan-e2e-session-job-badge');
const artifacts = path.join(runtime, 'artifacts');
await fs.mkdir(artifacts, { recursive: true });

assert.ok(!/:8767|:8768/.test(baseURL), `refusing protected service ports: ${baseURL}`);

const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };
// Interpreter used for the background-process Job argv; the isolated server
// spawns its own runner, this is only the payload command.
const PYTHON = process.env.PAN_E2E_PYTHON || 'python';

const browser = await chromium.launch({ headless: true });
const results = [];
const createdJobIds = [];

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
  // Mobile: the session list lives in the sidebar drawer; reveal it first.
  const hamburger = page.locator('button[aria-label="打开侧边栏"]');
  if (await hamburger.count() > 0) {
    await hamburger.click();
  }
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

async function sessionIdOf(page, name) {
  const card = page.locator('[data-session-card-id]').filter({ hasText: name }).first();
  await card.waitFor({ state: 'visible' });
  const sessionId = await card.getAttribute('data-session-card-id');
  assert.ok(sessionId, `no session id for ${name}`);
  return sessionId;
}

async function createJob(page, payload) {
  const response = await page.request.post(`${baseURL}/api/jobs`, { data: payload });
  assert.equal(response.ok(), true, `POST /api/jobs failed: ${response.status()} ${await response.text()}`);
  const body = await response.json();
  const job = body.job || body;
  assert.ok(job?.jobId, `job creation response missing jobId: ${JSON.stringify(body)}`);
  createdJobIds.push(job.jobId);
  return job;
}

const jobBadge = (page, sessionId) =>
  page.locator(`[data-session-card-id="${sessionId}"] [data-testid="session-job-badge"]`);

/** Computed style of one element, as a plain object. */
async function styleOf(locator) {
  return locator.evaluate((el) => {
    const s = getComputedStyle(el);
    return {
      color: s.color,
      fontSize: s.fontSize,
      fontWeight: s.fontWeight,
      lineHeight: s.lineHeight,
      height: s.height,
      minWidth: s.minWidth,
      borderRadius: s.borderRadius,
      letterSpacing: s.letterSpacing,
      display: s.display,
      visibility: s.visibility,
    };
  });
}

// ── Case 1: running background-process Job -> single blue J, geometry shared ──

await runCase('desktop running process job shows one blue J', DESKTOP, async (page) => {
  const sessionId = await sessionIdOf(page, 'Alpha Session');
  await createJob(page, {
    kind: 'background-process',
    name: 'e2e-session-job-running',
    description: 'e2e: session info-area J indicator (running)',
    target: { sessionId },
    argv: [PYTHON, '-c', 'import time; time.sleep(120)'],
    cwd: path.resolve(runtime),
    hideWindow: true,
  });

  const badge = jobBadge(page, sessionId);
  await badge.waitFor({ state: 'visible', timeout: 20000 });
  await page.waitForFunction(
    (sid) => {
      const el = document.querySelector(`[data-session-card-id="${sid}"] [data-testid="session-job-badge"]`);
      return el?.getAttribute('data-job-state') === 'running';
    },
    sessionId,
    { timeout: 20000 },
  );

  // Exactly one aggregated J for the session.
  assert.equal(await jobBadge(page, sessionId).count(), 1, 'running job must aggregate to a single J');

  const jStyle = await styleOf(badge);
  // Same visual language as the numeric badges: accent blue like the queue
  // badge family, identical 14px pill / 9px bold geometry.
  assert.match(jStyle.color, /^(rgb|oklch|color)/);
  assert.equal(jStyle.fontSize, '9px', 'J must share the numeric badge font size');
  assert.equal(jStyle.fontWeight, '700', 'J must share the numeric badge font weight');
  assert.equal(jStyle.height, '14px', 'J must share the numeric badge height');
  assert.equal(jStyle.borderRadius, '999px', 'J must share the numeric badge pill shape');

  // Blue == the accent color the queue badge uses (running semantics).
  const probe = await page.evaluate(() => {
    const el = document.createElement('span');
    el.style.color = 'var(--color-accent)';
    document.body.appendChild(el);
    const color = getComputedStyle(el).color;
    el.remove();
    return color;
  });
  assert.equal(jStyle.color, probe, `running J must be accent blue, got ${jStyle.color} vs ${probe}`);

  // Right-aligned inside the info-area rail on the card's right side.
  const box = (await badge.boundingBox())!;
  const card = (await page.locator(`[data-session-card-id="${sessionId}"]`).boundingBox())!;
  assert.ok(box.x + box.width <= card.x + card.width, 'J must stay inside the card');
  assert.ok(box.x > card.x + card.width / 2, 'J must sit in the right-half info area');
});

// ── Case 2: scheduled session-message Job -> yellow J ────────────────────────

await runCase('desktop scheduled message job shows one yellow J', DESKTOP, async (page) => {
  const sessionId = await sessionIdOf(page, 'Bravo Session');
  await createJob(page, {
    kind: 'session-message',
    name: 'e2e-session-job-scheduled',
    description: 'e2e: session info-area J indicator (scheduled)',
    target: { sessionId },
    text: 'e2e scheduled indicator probe',
    schedule: { type: 'once', delaySeconds: 7200 },
  });

  const badge = jobBadge(page, sessionId);
  await badge.waitFor({ state: 'visible', timeout: 20000 });
  await page.waitForFunction(
    (sid) => {
      const el = document.querySelector(`[data-session-card-id="${sid}"] [data-testid="session-job-badge"]`);
      return el?.getAttribute('data-job-state') === 'scheduled';
    },
    sessionId,
    { timeout: 20000 },
  );
  assert.equal(await jobBadge(page, sessionId).count(), 1, 'scheduled job must aggregate to a single J');

  const jStyle = await styleOf(badge);
  // Yellow == the warning color the unread-done badge uses (scheduled semantics).
  const probe = await page.evaluate(() => {
    const el = document.createElement('span');
    el.style.color = 'var(--color-warning)';
    document.body.appendChild(el);
    const color = getComputedStyle(el).color;
    el.remove();
    return color;
  });
  assert.equal(jStyle.color, probe, `scheduled J must be warning yellow, got ${jStyle.color} vs ${probe}`);
});

// ── Case 3: aggregation + running priority over a live WS transition ─────────

await runCase('desktop aggregates many jobs with running priority', DESKTOP, async (page) => {
  const sessionId = await sessionIdOf(page, 'Charlie Session');
  await createJob(page, {
    kind: 'session-message',
    name: 'e2e-session-job-agg-scheduled',
    description: 'e2e: aggregation (scheduled leg)',
    target: { sessionId },
    text: 'e2e aggregation probe',
    schedule: { type: 'once', delaySeconds: 7200 },
  });
  const badge = jobBadge(page, sessionId);
  await page.waitForFunction(
    (sid) => document.querySelector(`[data-session-card-id="${sid}"] [data-testid="session-job-badge"]`)
      ?.getAttribute('data-job-state') === 'scheduled',
    sessionId,
    { timeout: 20000 },
  );

  // A second, running job targeting the same Session flips the aggregate.
  await createJob(page, {
    kind: 'background-process',
    name: 'e2e-session-job-agg-running',
    description: 'e2e: aggregation (running leg)',
    target: { sessionId },
    argv: [PYTHON, '-c', 'import time; time.sleep(120)'],
    cwd: path.resolve(runtime),
    hideWindow: true,
  });
  await page.waitForFunction(
    (sid) => document.querySelector(`[data-session-card-id="${sid}"] [data-testid="session-job-badge"]`)
      ?.getAttribute('data-job-state') === 'running',
    sessionId,
    { timeout: 20000 },
  );
  assert.equal(await badge.count(), 1, 'two jobs must still aggregate to a single J');
});

// ── Case 4: numeric badges intact and right-aligned next to J ────────────────

await runCase('desktop keeps numeric badges intact and right aligned', DESKTOP, async (page) => {
  const sessionId = await sessionIdOf(page, 'Alpha Session');
  // Seed a durable unread-done count through the real test-only route so the
  // numeric badge is present next to the (already running) J.
  const seed = await page.request.post(`${baseURL}/__e2e/unread`, {
    data: { sessionId, advance: 7 },
  });
  assert.equal(seed.ok(), true, `seed unread failed: ${seed.status()}`);
  const done = page.locator(`[data-session-card-id="${sessionId}"] [data-testid="session-unread-done-badge"]`);
  await done.waitFor({ state: 'visible', timeout: 10000 });

  const badge = jobBadge(page, sessionId);
  await badge.waitFor({ state: 'visible', timeout: 20000 });

  // J shares the numeric badge geometry (same font metrics and pill shape).
  const jStyle = await styleOf(badge);
  const doneStyle = await styleOf(done);
  for (const key of ['fontSize', 'fontWeight', 'height', 'borderRadius', 'letterSpacing']) {
    assert.equal(jStyle[key], doneStyle[key], `J and numeric badge must share ${key}`);
  }

  // J is the left-most item of the right-anchored info-area row; the numeric
  // badge stays to its right and the row hugs the card's right edge.
  const row = page.locator(`[data-session-card-id="${sessionId}"] .session-quick-action-adapter-row`);
  const order = await row.evaluate((el) => Array.from(el.children).map((c) => c.getAttribute('data-testid')));
  assert.equal(order[0], 'session-job-badge', 'J must be the first (left-most) info-area item');
  assert.ok(order.includes('session-unread-done-badge'), 'numeric badge must remain in the row');

  const jBox = (await badge.boundingBox())!;
  const doneBox = (await done.boundingBox())!;
  assert.ok(jBox.x < doneBox.x, 'J must sit left of the numeric badge');
  const card = (await page.locator(`[data-session-card-id="${sessionId}"]`).boundingBox())!;
  assert.ok(
    Math.abs(doneBox.x + doneBox.width - (card.x + card.width)) < 24,
    'numeric badge must stay right-aligned inside the card',
  );
});

// ── Case 5: mobile width hides J first, numbers stay right-aligned ───────────

await runCase('mobile hides J while numeric badges stay right aligned', MOBILE, async (page) => {
  const sessionId = await sessionIdOf(page, 'Alpha Session');
  const seed = await page.request.post(`${baseURL}/__e2e/unread`, {
    data: { sessionId, advance: 4 },
  });
  assert.equal(seed.ok(), true, `seed unread failed: ${seed.status()}`);
  const done = page.locator(`[data-session-card-id="${sessionId}"] [data-testid="session-unread-done-badge"]`);
  await done.waitFor({ state: 'visible', timeout: 10000 });
  const badge = jobBadge(page, sessionId);
  await badge.waitFor({ state: 'attached', timeout: 20000 });

  const jStyle = await styleOf(badge);
  assert.equal(jStyle.display, 'none', 'J must be hidden at mobile width (max-md)');

  // The numeric badge survives and stays right-aligned.
  const doneStyle = await styleOf(done);
  assert.notEqual(doneStyle.display, 'none', 'numeric badge must stay visible at mobile width');
  const doneBox = (await done.boundingBox())!;
  const card = (await page.locator(`[data-session-card-id="${sessionId}"]`).boundingBox())!;
  assert.ok(
    Math.abs(doneBox.x + doneBox.width - (card.x + card.width)) < 24,
    'numeric badge must stay right-aligned at mobile width',
  );
  assert.equal(await done.textContent(), '4');
});

// ── Case 6: terminal/cancelled job removes J through the WS path ─────────────

await runCase('terminal job removes J via the live WS path', DESKTOP, async (page) => {
  const sessionId = await sessionIdOf(page, 'Charlie Session');
  const badge = jobBadge(page, sessionId);
  await badge.waitFor({ state: 'visible', timeout: 20000 });

  // Cancel every Job created for this session through the real API; the
  // job.deleted / terminal job.updated broadcast must clear the badge.
  for (const jobId of createdJobIds) {
    const response = await page.request.delete(`${baseURL}/api/jobs/${encodeURIComponent(jobId)}`);
    assert.ok(response.ok() || response.status() === 404, `cancel failed: ${response.status()}`);
  }
  await badge.waitFor({ state: 'detached', timeout: 20000 });
});

// ── Cleanup + summary ────────────────────────────────────────────────────────

for (const jobId of createdJobIds) {
  await fetch(`${baseURL}/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' }).catch(() => {});
}
await browser.close();

await fs.writeFile(
  path.join(runtime, 'results.json'),
  JSON.stringify({ baseURL, results }, null, 2),
  'utf-8',
);

const failed = results.filter((r) => r.status === 'failed');
for (const r of results) {
  console.log(`${r.status === 'passed' ? 'PASS' : 'FAIL'} ${r.name}`);
  if (r.error) console.log(r.error);
}
console.log(`artifacts: ${artifacts}`);
if (failed.length > 0) {
  console.error(`${failed.length} case(s) failed`);
  process.exitCode = 1;
}
