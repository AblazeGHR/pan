/* global process, document, navigator, console, fetch, URL */
/**
 * Real Chromium check for the queue preview's structured agent origin.
 *
 * Uses the isolated e2e server (packages/web/e2e/server.py) and the real
 * product HTTP routes: the queued agent message is enqueued through
 * POST /api/sessions/broadcast with a structured sourceSessionId, exactly like
 * an agent send. Nothing about the origin is mocked in the browser.
 *
 *   node e2e/queue-source-session.chromium.mjs <identity.json>
 *
 * identity.json: {"port": <isolated port>, "runtime": "<e2e runtime dir>",
 *                 "artifacts": "<dir inside runtime, optional>"}
 *
 * Guards (all before the first mutation, all failing closed):
 *  - the protected application ports 8767/8768 are rejected outright;
 *  - the process that owns the port must answer the read-only
 *    GET /__e2e/identity route and declare THIS checkout, THIS runtime root and
 *    a deterministic fake provider CLI;
 *  - that fake CLI must resolve to this checkout's tests/support stub and match
 *    it byte for byte (sha256), so an auto-spawned Worker cannot reach a real
 *    provider;
 *  - the seeded fixture sessions must exist and use the stubbed adapter.
 * Nothing here trusts a caller-declared flag: identity comes from the server.
 */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const CHECKOUT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const FAKE_CLI = path.join(CHECKOUT, 'tests/support/frontend_stream_cli.py');
const PROTECTED_PORTS = new Set([8767, 8768]);

const fail = (message) => {
  console.error(`[guard] ${message}`);
  process.exit(1);
};

const identityPath = process.argv[2];
if (!identityPath) fail('usage: node e2e/queue-source-session.chromium.mjs <identity.json>');
const identity = JSON.parse(await fs.readFile(identityPath, 'utf8'));

// ── guard 1: port policy (no network call yet) ──────────────────────────────
const port = identity.port;
if (!Number.isInteger(port) || port < 1 || port > 65535) fail(`invalid port: ${JSON.stringify(port)}`);
if (PROTECTED_PORTS.has(port)) fail(`port ${port} is the application port; use an isolated e2e port`);
if (typeof identity.runtime !== 'string' || !identity.runtime) fail('identity.runtime is required');
const runtime = path.resolve(identity.runtime);
if (runtime === CHECKOUT || runtime.startsWith(`${CHECKOUT}${path.sep}`)) {
  fail(`runtime ${runtime} is inside the checkout; use a disposable directory`);
}
const artifacts = identity.artifacts ? path.resolve(identity.artifacts) : path.join(runtime, 'artifacts');
if (artifacts !== runtime && !artifacts.startsWith(`${runtime}${path.sep}`)) {
  fail(`artifacts ${artifacts} must stay inside the runtime root`);
}
const base = `http://127.0.0.1:${port}`;

const sha256 = async (file) => crypto.createHash('sha256').update(await fs.readFile(file)).digest('hex');

// ── guard 2: the process owning the port must declare the isolated fixture ──
let owner;
try {
  const probe = await fetch(`${base}/__e2e/identity`);
  if (!probe.ok) fail(`identity probe returned HTTP ${probe.status}`);
  owner = await probe.json();
} catch (error) {
  fail(`identity probe failed (${error instanceof Error ? error.message : String(error)}); refusing to mutate`);
}
if (Number(owner.port) !== port) fail(`probe port ${owner.port} does not match identity port ${port}`);
if (path.resolve(String(owner.checkout)) !== CHECKOUT) {
  fail(`server checkout ${owner.checkout} is not this checkout ${CHECKOUT}`);
}
if (path.resolve(String(owner.runtime)) !== runtime) {
  fail(`server runtime ${owner.runtime} is not the requested runtime ${runtime}`);
}
if (!String(owner.sessionDir || '').startsWith(`${runtime}${path.sep}`)) {
  fail(`server session dir ${owner.sessionDir} is outside the runtime root`);
}

// ── guard 3: the provider stub must be this checkout's file, byte for byte ──
if (typeof owner.fakeCli !== 'string' || !owner.fakeCli) {
  fail('server has no fake CLI; a real provider could be spawned');
}
const declaredCli = path.resolve(owner.fakeCli);
if (declaredCli !== FAKE_CLI) fail(`server fake CLI ${declaredCli} is not ${FAKE_CLI}`);
const [declaredHash, referenceHash] = await Promise.all([sha256(declaredCli), sha256(FAKE_CLI)]);
if (declaredHash !== referenceHash) {
  fail(`fake CLI hash ${declaredHash} does not match ${referenceHash}`);
}

// ── guard 4: fixture sessions must exist and use the stubbed adapter ────────
const { sessions } = await (async () => {
  const response = await fetch(`${base}/api/sessions?summary=1`);
  if (!response.ok) fail(`session list returned HTTP ${response.status}`);
  return response.json();
})();
const target = sessions.find((session) => session.name === 'Charlie Session');
const source = sessions.find((session) => session.name === 'Alpha Session');
if (!target || !source) fail('e2e fixture sessions are missing; this is not the seeded fixture instance');
for (const session of [target, source]) {
  if (session.adapter !== 'cbc') fail(`fixture session ${session.name} uses adapter ${session.adapter}, not the stubbed cbc`);
}

await fs.mkdir(artifacts, { recursive: true });

// Session names cannot contain spaces (server validation), so the long name is
// a single long token: it still exceeds the preview column width at 320px.
const LONG_SOURCE_NAME = 'Orchestrator_Session_With_An_Extremely_Long_Descriptive_Name';
const AGENT_TEXT = 'agent queued message with structured origin';

const evidence = {
  port,
  runtime,
  guards: {
    protectedPortsRejected: [...PROTECTED_PORTS],
    ownerPid: owner.pid,
    ownerCheckout: owner.checkout,
    fakeCli: declaredCli,
    fakeCliSha256: declaredHash,
  },
  checks: {},
  browser: {},
  errors: [],
};
const json = async (response) => {
  const body = await response.json();
  assert.ok(response.ok, `HTTP ${response.status}: ${JSON.stringify(body)}`);
  return body;
};

// ── real server setup (mutations start here) ────────────────────────────────
const renamed = await json(await fetch(`${base}/api/sessions/${source.id}/rename`, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ name: LONG_SOURCE_NAME }),
}));
assert.equal(renamed.name, LONG_SOURCE_NAME, `rename must apply: ${JSON.stringify(renamed)}`);
evidence.sourceSessionId = source.id;
evidence.targetSessionId = target.id;

// Pause everything on the target so the durable queue item stays queued for the
// preview (the global watchdog would otherwise hand it to a Worker and the row
// would leave the pending view). Pause does not change the row's identity.
await json(await fetch(`${base}/api/sessions/${target.id}/queue/paused`, {
  method: 'PATCH',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ paused: true }),
}));

const queued = await json(await fetch(`${base}/api/sessions/broadcast`, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ sessionIds: [target.id], text: AGENT_TEXT, sourceSessionId: source.id }),
}));
assert.equal(queued.status, 'queued', `enqueue must be durable: ${JSON.stringify(queued)}`);

const projection = await json(await fetch(`${base}/api/sessions/${target.id}/queue`));
const row = projection.items.find((item) => item.text === AGENT_TEXT);
assert.ok(row, 'queued agent item must be projected');
assert.equal(row.source, 'agent');
assert.equal(row.meta?.sourceSessionId, source.id);
evidence.checks.serverProjectsStructuredOrigin = true;

const browser = await chromium.launch({ headless: true });

async function openPreview(page, width) {
  await page.goto(`${base}/react/?session=${target.id}`);
  await page.locator('#send-queue-button').waitFor({ state: 'visible' });
  await page.locator('#send-queue-button').click();
  const rowButton = page.getByRole('button', { name: AGENT_TEXT }).first();
  await rowButton.waitFor({ state: 'visible' });
  await rowButton.click();
  const dialog = page.getByRole('dialog');
  await dialog.waitFor({ state: 'visible' });
  // Below 640px the shared Modal enters as a bottom sheet; wait for that
  // animation so geometry and screenshots describe the settled dialog.
  await page.locator('.modal-card').evaluate((el) => Promise.all(
    (el.getAnimations?.() ?? []).map((animation) => animation.finished.catch(() => undefined)),
  ));
  evidence.browser[width] = { dialog: true };
  return dialog;
}

try {
  // ── wide viewport: copy, jump, feedback ────────────────────────────────────
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    permissions: ['clipboard-read', 'clipboard-write'],
  });
  const page = await context.newPage();
  page.on('pageerror', (error) => evidence.errors.push(String(error)));
  let dialog = await openPreview(page, 1280);

  const dialogText = await dialog.innerText();
  // The label is rendered uppercase by CSS; match case-insensitively.
  assert.match(dialogText, /source session/i);
  assert.match(dialogText, new RegExp(LONG_SOURCE_NAME.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.match(dialogText, new RegExp(source.id));
  evidence.checks.showsNameAndId = true;

  await page.getByRole('button', { name: 'Copy source session name' }).click();
  await page.getByText('Copied source session name').waitFor({ state: 'visible' });
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), LONG_SOURCE_NAME);
  evidence.checks.copiesName = true;

  await page.getByRole('button', { name: 'Copy source session ID' }).click();
  await page.getByText('Copied source session ID').waitFor({ state: 'visible' });
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), source.id);
  evidence.checks.copiesId = true;

  await page.screenshot({ path: path.join(artifacts, 'preview-wide.png') });

  await page.getByText('Open source chat').click();
  await dialog.waitFor({ state: 'detached' });
  const sourceCard = page.locator(`[data-session-card-id="${source.id}"]`);
  await sourceCard.waitFor({ state: 'visible' });
  // The jump reuses the product deeplink: workspace scope + selection together.
  const deadline = Date.now() + 5000;
  let activeClass = '';
  while (Date.now() < deadline) {
    activeClass = (await sourceCard.getAttribute('class')) || '';
    if (activeClass.includes('border-l-accent')) break;
    await page.waitForTimeout(100);
  }
  assert.match(activeClass, /border-l-accent/, `source session must be selected, got "${activeClass}"`);
  assert.match(new URL(page.url()).pathname, /\/react\/?$/, 'jump stays inside the chat view');
  evidence.checks.jumpsToSourceSession = true;
  await page.screenshot({ path: path.join(artifacts, 'after-jump.png') });
  await context.close();

  // ── 320px viewport: long name and id must stay inside the dialog ───────────
  const narrow = await browser.newContext({ viewport: { width: 320, height: 720 } });
  const narrowPage = await narrow.newPage();
  narrowPage.on('pageerror', (error) => evidence.errors.push(String(error)));
  dialog = await openPreview(narrowPage, 320);

  const overflow = await narrowPage.evaluate(() => ({
    documentScrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  assert.ok(overflow.documentScrollWidth <= overflow.clientWidth,
    `page must not scroll horizontally: ${JSON.stringify(overflow)}`);
  const dialogBox = await dialog.boundingBox();
  assert.ok(dialogBox.width <= 320, `dialog must fit 320px, got ${dialogBox.width}`);
  for (const text of [LONG_SOURCE_NAME, source.id]) {
    const box = await dialog.getByText(text, { exact: true }).boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= 320,
      `"${text}" must stay inside the viewport, got ${JSON.stringify(box)}`);
  }
  const openBox = await dialog.getByText('Open source chat').boundingBox();
  assert.ok(openBox.x >= 0 && openBox.x + openBox.width <= 320,
    `open button must stay inside the viewport, got ${JSON.stringify(openBox)}`);
  evidence.checks.narrowLayout320 = overflow;
  await narrowPage.screenshot({ path: path.join(artifacts, 'preview-320.png') });
  await narrow.close();

  // ── deleted source session: English feedback, id still copyable ───────────
  await json(await fetch(`${base}/api/sessions/${source.id}`, { method: 'DELETE' }));
  const degradedContext = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    permissions: ['clipboard-read', 'clipboard-write'],
  });
  const degradedPage = await degradedContext.newPage();
  degradedPage.on('pageerror', (error) => evidence.errors.push(String(error)));
  dialog = await openPreview(degradedPage, 'deleted');
  const degradedText = await dialog.innerText();
  assert.match(degradedText, /Unavailable/);
  assert.match(degradedText, /not in the loaded session list/);
  assert.match(degradedText, new RegExp(source.id));
  await degradedPage.getByRole('button', { name: 'Copy source session ID' }).click();
  await degradedPage.getByText('Copied source session ID').waitFor({ state: 'visible' });
  assert.equal(await degradedPage.evaluate(() => navigator.clipboard.readText()), source.id);
  await degradedPage.getByText('Open source chat').click();
  await degradedPage.getByRole('alert')
    .filter({ hasText: 'The source session no longer exists, so its chat could not be opened.' })
    .waitFor({ state: 'visible' });
  evidence.checks.deletedSourceFeedback = true;
  await degradedPage.screenshot({ path: path.join(artifacts, 'preview-deleted-source.png') });
  await degradedContext.close();

  assert.deepEqual(evidence.errors, [], `page errors: ${JSON.stringify(evidence.errors)}`);
  evidence.status = 'passed';
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.stack || error.message : String(error);
  throw error;
} finally {
  await browser.close();
  await fs.writeFile(path.join(artifacts, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify(evidence, null, 2));
}
