/* global localStorage, console, process, setTimeout */
// Real-browser E2E for the Session info-area Job indicator (J) width-driven
// visibility. Runs against the isolated e2e/server.py instance: real FastAPI
// routes, the real /api/jobs backend, the real /ws broadcast path and the real
// React build. Creates real Jobs (background-process + session-message)
// targeting real Sessions, seeds durable queue/unread counts through the real
// test-only routes, then verifies the fit decision with bounding boxes:
//   - roomy desktop AND roomy mobile both show J (never viewport-disabled);
//   - as the info area runs out of width, J disappears ENTIRELY — no leftover
//     pill, no reserved gap — while the numeric badges stay complete and the
//     row's right edge never moves;
//   - multi-digit numbers, a long adapter and two-number rows decide by width.
// Artifacts (screenshots + traces) land in the runtime artifacts directory.

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium, expect } from '@playwright/test';

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

async function createSession(page, name, adapter) {
  const response = await page.request.post(`${baseURL}/api/sessions`, { data: { name, adapter } });
  assert.equal(response.ok(), true, `POST /api/sessions failed: ${response.status()} ${await response.text()}`);
  const session = await response.json();
  assert.ok(session?.id, `session creation response missing id: ${JSON.stringify(session)}`);
  assert.equal(session.adapter, adapter, `created session adapter must be ${adapter}`);
  return session.id;
}

/** Durable unread-done count through the real test-only route (monotonic). */
async function seedUnread(page, sessionId, advance) {
  const response = await page.request.post(`${baseURL}/__e2e/unread`, {
    data: { sessionId, advance },
  });
  assert.equal(response.ok(), true, `seed unread failed: ${response.status()}`);
  return response.json();
}

/** Clear the durable unread-done count (read cursor == generation). */
async function clearUnread(page, sessionId) {
  const response = await page.request.post(`${baseURL}/__e2e/unread`, {
    data: { sessionId, generation: 999, readGeneration: 999 },
  });
  assert.equal(response.ok(), true, `clear unread failed: ${response.status()}`);
  const body = await response.json();
  assert.equal(body.unreadDoneCount, 0, 'clear unread must zero the count');
  return body;
}

/** Durable queue_pending rows through the real test-only route (real envelopes). */
async function seedQueue(page, sessionId, count) {
  const response = await page.request.post(`${baseURL}/__e2e/queue`, {
    data: { sessionId, count },
  });
  assert.equal(response.ok(), true, `seed queue failed: ${response.status()}`);
  const body = await response.json();
  assert.equal(body.queuePendingCount, count, 'queue seed must report the seeded count');
  return body;
}

const jobBadge = (page, sessionId) =>
  page.locator(`[data-session-card-id="${sessionId}"] [data-testid="session-job-badge"]`);

const cardLocator = (page, sessionId) =>
  page.locator(`[data-session-card-id="${sessionId}"]`);

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

async function boxOf(locator) {
  const box = await locator.boundingBox();
  assert.ok(box, 'element must have a box');
  return box;
}

const runningJob = (sessionId) => ({
  kind: 'background-process',
  name: 'e2e-session-job-running',
  description: 'e2e: session info-area J indicator (running)',
  target: { sessionId },
  argv: [PYTHON, '-c', 'import time; time.sleep(120)'],
  cwd: path.resolve(runtime),
  hideWindow: true,
});

// ── Case 1: width ladder — J appears, disappears ENTIRELY, returns ──────────

await runCase('desktop width ladder hides J entirely and keeps numbers right-aligned', DESKTOP, async (page) => {
  const sessionId = await createSession(page, 'Delta Width', 'cbc');
  await createJob(page, runningJob(sessionId));
  await seedUnread(page, sessionId, 3); // "3" next to the (running) J

  const badge = jobBadge(page, sessionId);
  const card = cardLocator(page, sessionId);
  const rail = page.locator(`[data-session-card-id="${sessionId}"] [data-testid="session-quick-action-rail"]`);
  const row = page.locator(`[data-session-card-id="${sessionId}"] .session-quick-action-adapter-row`);
  const done = page.locator(`[data-session-card-id="${sessionId}"] [data-testid="session-unread-done-badge"]`);
  const adapter = page.locator(`[data-session-card-id="${sessionId}"] [data-testid="session-adapter-badge"]`);
  const queue = page.locator(`[data-session-card-id="${sessionId}"] [data-testid="session-queue-badge"]`);

  // (a) Roomy: J + done(3) + adapter(cbc) = 14 + 38 + 6 = 58 <= 71 -> visible.
  await badge.waitFor({ state: 'visible', timeout: 20000 });
  await page.waitForFunction(
    (sid) => document.querySelector(`[data-session-card-id="${sid}"] [data-testid="session-job-badge"]`)
      ?.getAttribute('data-job-state') === 'running',
    sessionId,
    { timeout: 20000 },
  );
  const jBoxA = await boxOf(badge);
  const rowBoxA = await boxOf(row);
  const railBoxA = await boxOf(rail);
  const adapterBoxA = await boxOf(adapter);
  const cardBoxA = await boxOf(card);
  // Blue accent, geometry shared with the numeric badge.
  const jStyle = await styleOf(badge);
  const doneStyle = await styleOf(done);
  for (const key of ['fontSize', 'fontWeight', 'height', 'borderRadius', 'letterSpacing']) {
    assert.equal(jStyle[key], doneStyle[key], `J and numeric badge must share ${key}`);
  }
  const accent = await page.evaluate(() => {
    const el = document.createElement('span');
    el.style.color = 'var(--color-accent)';
    document.body.appendChild(el);
    const color = getComputedStyle(el).color;
    el.remove();
    return color;
  });
  assert.equal(jStyle.color, accent, `running J must be accent blue, got ${jStyle.color} vs ${accent}`);
  // J is the left-most item; the row hugs the rail's right edge.
  assert.ok(jBoxA.x < (await boxOf(done)).x, 'J must sit left of the numeric badge');
  assert.ok(
    Math.abs(rowBoxA.x + rowBoxA.width - (railBoxA.x + railBoxA.width)) < 4,
    'the info-area row must hug the rail right edge',
  );
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({ path: path.join(artifacts, 'element-width-ladder-roomy.png') });

  // (b) A queue badge appears: J + queue(9) + done(3) + adapter = 75 > 71.
  await seedQueue(page, sessionId, 1);
  await expect(queue).toHaveText('1', { timeout: 10000 });
  await badge.waitFor({ state: 'detached', timeout: 10000 });
  assert.equal(await jobBadge(page, sessionId).count(), 0, 'J must disappear entirely (no leftover pill)');
  const rowBoxB = await boxOf(row);
  // No reserved gap: the row shrinks by exactly the J width plus its gap.
  const shrink = rowBoxA.width - rowBoxB.width;
  assert.ok(
    Math.abs(shrink - (jBoxA.width + 3)) < 0.75,
    `row must shrink by exactly jWidth+gap, got ${shrink} vs ${jBoxA.width + 3}`,
  );
  // Right edge unmoved; numbers complete inside the rail.
  const railBoxB = await boxOf(rail);
  const adapterBoxB = await boxOf(adapter);
  const queueBox = await boxOf(queue);
  const doneBoxB = await boxOf(done);
  assert.ok(
    Math.abs((rowBoxB.x + rowBoxB.width) - (rowBoxA.x + rowBoxA.width)) < 0.75,
    'the row right edge must not move when J hides',
  );
  assert.ok(
    Math.abs((adapterBoxB.x + adapterBoxB.width) - (railBoxB.x + railBoxB.width)) < 4,
    'the right-most identifier must stay right-aligned to the rail',
  );
  for (const [name, box] of [['queue', queueBox], ['done', doneBoxB]]) {
    assert.ok(
      box.x >= railBoxB.x - 0.5 && box.x + box.width <= railBoxB.x + railBoxB.width + 0.5,
      `${name} badge must stay complete inside the rail`,
    );
  }

  // (c) Multi-digit numbers: still hidden, numbers still complete, right edge
  // still unmoved.
  await seedQueue(page, sessionId, 120);
  await expect(queue).toHaveText('99+', { timeout: 10000 });
  await seedUnread(page, sessionId, 120);
  await expect(done).toHaveText('99+', { timeout: 10000 });
  assert.equal(await jobBadge(page, sessionId).count(), 0, 'multi-digit crowding must keep J hidden');
  const railBoxC = await boxOf(rail);
  const adapterBoxC = await boxOf(adapter);
  const queueBoxC = await boxOf(queue);
  const doneBoxC = await boxOf(done);
  assert.ok(
    Math.abs((adapterBoxC.x + adapterBoxC.width) - (railBoxC.x + railBoxC.width)) < 4,
    'the right-most identifier must stay right-aligned while crowded',
  );
  for (const [name, box] of [['queue', queueBoxC], ['done', doneBoxC]]) {
    assert.ok(
      box.x >= railBoxC.x - 0.5 && box.x + box.width <= railBoxC.x + railBoxC.width + 0.5,
      `${name} badge must stay complete while crowded`,
    );
  }
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({ path: path.join(artifacts, 'element-width-ladder-crowded.png') });

  // (d) The numbers go away: 14 + 0 + 0 fits again -> J returns.
  await seedQueue(page, sessionId, 0);
  await expect(queue).toHaveCount(0, { timeout: 10000 });
  await clearUnread(page, sessionId);
  await expect(done).toHaveCount(0, { timeout: 10000 });
  await badge.waitFor({ state: 'visible', timeout: 10000 });
  assert.ok(jBoxA.x + jBoxA.width <= cardBoxA.x + cardBoxA.width, 'J must stay inside the card');
});

// ── Case 2: the adapter length decides with the same numbers ────────────────

await runCase('desktop long adapter hides J where a short adapter shows it', DESKTOP, async (page) => {
  const longId = await createSession(page, 'Delta Long Adapter', 'opencode');
  const shortId = await createSession(page, 'Delta Short Adapter', 'cbc');
  await createJob(page, runningJob(longId));
  await createJob(page, runningJob(shortId));
  await seedUnread(page, longId, 120);  // "99+"
  await seedUnread(page, shortId, 120); // "99+"

  const longBadge = jobBadge(page, longId);
  const shortBadge = jobBadge(page, shortId);
  // Same numbers, different adapter: 14 + 64 + 6 = 84 > 71 vs 14 + 48 + 6 = 68.
  await longBadge.waitFor({ state: 'detached', timeout: 20000 });
  await shortBadge.waitFor({ state: 'visible', timeout: 20000 });
  await page.waitForFunction(
    (sid) => document.querySelector(`[data-session-card-id="${sid}"] [data-testid="session-job-badge"]`)
      ?.getAttribute('data-job-state') === 'running',
    shortId,
    { timeout: 20000 },
  );
  assert.equal(await jobBadge(page, longId).count(), 0, 'long adapter must hide J');

  // The long-adapter card keeps its numbers complete and right-aligned.
  const rail = page.locator(`[data-session-card-id="${longId}"] [data-testid="session-quick-action-rail"]`);
  const done = page.locator(`[data-session-card-id="${longId}"] [data-testid="session-unread-done-badge"]`);
  const adapter = page.locator(`[data-session-card-id="${longId}"] [data-testid="session-adapter-badge"]`);
  const railBox = await boxOf(rail);
  const doneBox = await boxOf(done);
  const adapterBox = await boxOf(adapter);
  assert.equal(await done.textContent(), '99+');
  assert.ok(
    doneBox.x >= railBox.x - 0.5 && doneBox.x + doneBox.width <= railBox.x + railBox.width + 0.5,
    'numeric badge must stay complete next to a long adapter',
  );
  assert.ok(
    Math.abs(adapterBox.x + adapterBox.width - (railBox.x + railBox.width)) < 4,
    'the adapter chip must stay right-aligned next to a long adapter',
  );
  const card = cardLocator(page, longId);
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({ path: path.join(artifacts, 'element-long-adapter-j-hidden.png') });
});

// ── Case 3: mobile is width-driven too — a roomy mobile info area shows J ───

await runCase('mobile roomy info area shows J and crowded mobile hides it', MOBILE, async (page) => {
  const sessionId = await sessionIdOf(page, 'Delta Width');
  const badge = jobBadge(page, sessionId);
  const done = page.locator(`[data-session-card-id="${sessionId}"] [data-testid="session-unread-done-badge"]`);
  const queue = page.locator(`[data-session-card-id="${sessionId}"] [data-testid="session-queue-badge"]`);
  const rail = page.locator(`[data-session-card-id="${sessionId}"] [data-testid="session-quick-action-rail"]`);
  const row = page.locator(`[data-session-card-id="${sessionId}"] .session-quick-action-adapter-row`);

  // Roomy mobile (only the adapter + a small done count): J must be VISIBLE —
  // the decision is the info-area width, not the viewport breakpoint.
  await badge.waitFor({ state: 'visible', timeout: 20000 });
  const jBox = await boxOf(badge);
  const railBox = await boxOf(rail);
  assert.ok(jBox.x + jBox.width <= railBox.x + railBox.width, 'mobile J must stay inside the rail');

  // Crowded mobile (two multi-digit numbers): J disappears entirely.
  await seedQueue(page, sessionId, 120);
  await expect(queue).toHaveText('99+', { timeout: 10000 });
  await seedUnread(page, sessionId, 120);
  await expect(done).toHaveText('99+', { timeout: 10000 });
  await badge.waitFor({ state: 'detached', timeout: 10000 });
  assert.equal(await jobBadge(page, sessionId).count(), 0, 'crowded mobile must hide J entirely');
  const doneBox = await boxOf(done);
  const rowBox = await boxOf(row);
  assert.ok(
    Math.abs(rowBox.x + rowBox.width - (railBox.x + railBox.width)) < 4,
    'the row must stay right-aligned to the rail on mobile',
  );
  assert.ok(
    doneBox.x >= railBox.x - 0.5 && doneBox.x + doneBox.width <= railBox.x + railBox.width + 0.5,
    'mobile numeric badge must stay complete',
  );
  const card = cardLocator(page, sessionId);
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({ path: path.join(artifacts, 'element-mobile-crowded-j-hidden.png') });
});

// ── Case 4: aggregation + running priority over a live WS transition ─────────

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
  await createJob(page, runningJob(sessionId));
  await page.waitForFunction(
    (sid) => document.querySelector(`[data-session-card-id="${sid}"] [data-testid="session-job-badge"]`)
      ?.getAttribute('data-job-state') === 'running',
    sessionId,
    { timeout: 20000 },
  );
  assert.equal(await badge.count(), 1, 'two jobs must still aggregate to a single J');
});

// ── Case 5: scheduled message job shows the yellow J ────────────────────────

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
    (sid) => document.querySelector(`[data-session-card-id="${sid}"] [data-testid="session-job-badge"]`)
      ?.getAttribute('data-job-state') === 'scheduled',
    sessionId,
    { timeout: 20000 },
  );
  const jStyle = await styleOf(badge);
  const warning = await page.evaluate(() => {
    const el = document.createElement('span');
    el.style.color = 'var(--color-warning)';
    document.body.appendChild(el);
    const color = getComputedStyle(el).color;
    el.remove();
    return color;
  });
  assert.equal(jStyle.color, warning, `scheduled J must be warning yellow, got ${jStyle.color} vs ${warning}`);
  const card = cardLocator(page, sessionId);
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({ path: path.join(artifacts, 'element-desktop-scheduled-j.png') });
});

// ── Case 6: terminal/cancelled job removes J through the WS path ─────────────

await runCase('terminal job removes J via the live WS path', DESKTOP, async (page) => {
  const sessionId = await sessionIdOf(page, 'Charlie Session');
  const badge = jobBadge(page, sessionId);
  await badge.waitFor({ state: 'visible', timeout: 20000 });

  // Cancel every Job created through the real API; the job.deleted /
  // terminal job.updated broadcast must clear the badge.
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
