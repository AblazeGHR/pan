/* global process, console, document, window, localStorage, sessionStorage, fetch */
import assert from 'node:assert/strict';
import { spawn, spawnSync, execFileSync } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { chromium, expect } from '@playwright/test';
import { createHash } from 'node:crypto';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../../..');
const web = path.resolve(here, '..');
const runtime = path.join(web, 'test-results', `automation-${Date.now()}`);
const out = process.env.PAN_AUTOMATION_E2E_OUT || path.join(root, 'artifacts/event-trigger-ui');
await fs.mkdir(runtime, { recursive: true }); await fs.mkdir(out, { recursive: true });
async function dataSnapshot(directory = path.join(root, 'data')) {
  const entries = await fs.readdir(directory, { withFileTypes: true }).catch(() => []);
  const result = {};
  for (const entry of entries) {
    const name = path.join(directory, entry.name); assert.ok(!entry.isSymbolicLink());
    if (entry.isDirectory()) Object.assign(result, await dataSnapshot(name));
    else result[path.relative(root, name)] = createHash('sha256').update(await fs.readFile(name)).digest('hex');
  }
  return result;
}
const checkoutDataBefore = await dataSnapshot();
const port = await new Promise((resolve) => { const probe = net.createServer(); probe.listen(0, '127.0.0.1', () => { const value = probe.address().port; probe.close(() => resolve(value)); }); });
assert.ok(![8767, 8768].includes(port));
const base = `http://127.0.0.1:${port}`;
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith('PAN_') && key.toUpperCase() !== 'PYTHONPATH'));
const cli = path.join(root, 'tests/support/frontend_stream_cli.py');
const python = process.env.PAN_E2E_PYTHON || 'D:/project/Pan-main/.venv/Scripts/python.exe';
const server = spawn(python, [path.join(here, 'automation.server.py')], { cwd: root, windowsHide: true, env: { ...env, PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime, PAN_E2E_FAKE_CLI: cli }, stdio: ['ignore', 'pipe', 'pipe'] });
const log = createWriteStream(path.join(runtime, 'server.log')); server.stdout.pipe(log); server.stderr.pipe(log);
const results = []; let browser; let identity; let failure; let bearer; let browserBearer; let portReleased = false;
async function poll(read, accepts, label, timeout = 25000) {
  const end = Date.now() + timeout; let last;
  while (Date.now() < end) { try { last = await read(); if (accepts(last)) return last; } catch { /* startup */ } await sleep(150); }
  throw new Error(`Timed out: ${label} (${JSON.stringify(last)?.slice(0, 400)})`);
}
async function owned() {
  const actual = await (await fetch(`${base}/__e2e/identity`)).json();
  if (actual.pid !== server.pid) {
    // Windows venv python.exe launches the configured interpreter as its child.
    // Validate the real server's ancestry instead of treating the launcher PID
    // as the listening process or bypassing the deployment interpreter.
    const proc = JSON.parse(execFileSync('powershell', ['-NoProfile', '-Command', `Get-CimInstance Win32_Process -Filter 'ProcessId = ${Number(actual.pid)}' | Select-Object ParentProcessId,CommandLine | ConvertTo-Json -Compress`], { encoding: 'utf8', windowsHide: true }));
    assert.equal(proc.ParentProcessId, server.pid); assert.ok(proc.CommandLine.includes('automation.server.py'));
  }
  assert.equal(actual.port, port);
  assert.equal(path.resolve(actual.checkout), root); assert.equal(path.resolve(actual.runtime), runtime);
  assert.equal(path.resolve(actual.sessionDir), path.join(runtime, 'sessions')); assert.equal(path.resolve(actual.fakeCli), cli);
  return actual;
}
async function api(route, method = 'GET', body, auth = false) {
  if (method !== 'GET') await owned();
  const r = await fetch(base + route, { method, headers: { 'Content-Type': 'application/json', ...(auth ? { Authorization: `Bearer ${bearer}` } : {}) }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  assert.equal(r.ok, true, `${method} ${route}: ${r.status}`); return r.json();
}
async function check(name, fn) { await fn(); results.push({ name, ok: true }); console.log(`PASS ${name}`); }
try {
  await poll(() => fetch(`${base}/__e2e/identity`).then((r) => r.json()), (v) => Number.isInteger(v.pid), 'isolated server ready');
  identity = await owned();
  const fixtures = JSON.parse(await fs.readFile(path.join(runtime, 'fixtures.json'), 'utf8'));
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.route(/^https?:\/\/fonts\./, (route) => route.abort());
  // Verify identity before *every* browser-originated management write too.
  await context.route(/\/api\/(automation|actions)/, async (route) => {
    if (route.request().method() !== 'GET') { await owned(); browserBearer = route.request().headers().authorization ?? browserBearer; }
    await route.continue();
  });
  const page = await context.newPage(); const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message));
  await page.goto(`${base}/react/automation`);
  await expect(page.getByRole('button', { name: 'New Trigger' })).toBeVisible();
  await check('production UI creates an explicit Session Operation Action', async () => {
    await page.getByRole('button', { name: 'Pause Everything template', exact: true }).click();
    await page.getByLabel('Action name', { exact: true }).fill('Browser pause');
    await page.getByRole('group', { name: 'Target Sessions' }).getByText('Selected target').click();
    await page.getByRole('button', { name: 'Create Session Operation Action' }).click();
    await expect(page.getByText(/Action created:/)).toBeVisible();
    await page.getByRole('button', { name: 'Close template' }).click();
  });
  const catalog = await api('/api/actions'); const action = catalog.actions.find((a) => a.name === 'Browser pause');
  assert.equal(action.execution.selector.ids[0], fixtures['Selected target']);
  await check('disabled draft persists without login; boolean State editor', async () => {
    await page.getByRole('button', { name: 'New Trigger' }).click();
    await page.getByLabel('Trigger name').fill('Idle browser pause');
    await page.getByLabel('workers.all_idle · Available', { exact: true }).check();
    await page.getByLabel('Action reference').selectOption(action.id);
    await page.getByLabel('Check State after Event').check();
    await page.getByLabel('Condition type').selectOption('and');
    await page.getByRole('button', { name: 'Add condition', exact: true }).click();
    await page.getByLabel('Comparison', { exact: true }).nth(1).selectOption('gte');
    await page.getByRole('button', { name: 'Save disabled draft' }).click();
    await expect(page.getByText('Trigger saved.')).toBeVisible();
    const view = await api('/api/automation'); const trigger = Object.values(view.triggers).find((t) => t.name === 'Idle browser pause');
    assert.equal(trigger.enabled, false); assert.equal(trigger.executionMode, 'serial'); assert.equal(trigger.condition.and.length, 2);
  });
  await check('disabled configuration causes no Pause effect on a real fake-CLI worker edge', async () => {
    await api('/api/send', 'POST', { sessionId: fixtures['Event producer'], text: 'disabled-edge', source: 'user' });
    await poll(() => api(`/api/sessions/${fixtures['Event producer']}`), (s) => s.lastResult?.result?.includes('disabled-edge') || s.lastResult?.includes?.('disabled-edge'), 'fake Worker result');
    assert.equal((await api(`/api/sessions/${fixtures['Selected target']}`)).queuePaused, false);
  });
  const key = (await fs.readFile(path.join(runtime, 'automation/user.key'), 'utf8')).trim();
  await check('memory login and explicit enable review; real authenticated HTTP', async () => {
    await page.getByLabel('Local user key').fill(key); await page.getByRole('button', { name: 'Log in', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Log out' })).toBeVisible();
    await page.getByRole('button', { name: 'Review and enable' }).click();
    await expect(page.getByRole('region', { name: 'Enable review' })).toContainText('Browser pause');
    await page.getByRole('button', { name: 'Confirm enable' }).click();
    await expect(page.getByRole('button', { name: 'Save enabled configuration' })).toBeVisible();
    const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage }, url: window.location.href }));
    assert.ok(!storage.includes(key)); assert.ok(!storage.includes('Bearer'));
    assert.ok(browserBearer && !storage.includes(browserBearer.slice(7)));
    ({ token: bearer } = await api('/api/automation/login', 'POST', { key }));
  });
  let success;
  await check('real Worker positive-to-zero → Run → Pause Everything persists targets and queue pause', async () => {
    await api('/api/send', 'POST', { sessionId: fixtures['Event producer'], text: 'enabled-edge', source: 'user' });
    success = await poll(() => api('/api/automation'), (s) => Object.values(s.runs).some((r) => r.actionId === action.id && r.status === 'succeeded'), 'succeeded automatic Run');
    const run = Object.values(success.runs).find((r) => r.actionId === action.id && r.status === 'succeeded');
    assert.deepEqual(run.targetIds, [fixtures['Selected target']]); assert.equal(run.targetResults.length, 1); assert.ok(run.actionDigest);
    const target = await api(`/api/sessions/${fixtures['Selected target']}`); const control = await api(`/api/sessions/${fixtures['Control target']}`);
    assert.equal(target.queuePaused, true); assert.equal(control.queuePaused, false);
    const durableTarget = JSON.parse(await fs.readFile(path.join(runtime, 'sessions', `${fixtures['Selected target']}.json`), 'utf8'));
    assert.ok(durableTarget.queue_pending.length > 0);
    await api(`/api/sessions/${fixtures['Selected target']}/queue/fixture-pending/lock`, 'PATCH', { locked: false });
    await api(`/api/sessions/${fixtures['Control target']}/queue/fixture-pending/lock`, 'PATCH', { locked: false });
    await api('/api/send', 'POST', { sessionId: fixtures['Control target'], text: 'unpaused-control-delivery', source: 'user' });
    await poll(() => api(`/api/sessions/${fixtures['Control target']}`), (s) => s.lastResult?.result?.includes('unpaused-control-delivery'), 'unpaused control consumes work');
    await sleep(1200);
    const stillQueued = await api(`/api/sessions/${fixtures['Selected target']}/queue`);
    assert.equal(stillQueued.queuePaused, true); assert.ok(stillQueued.items.some((item) => item.id === 'fixture-pending' || item.queueItemId === 'fixture-pending'));
    await page.getByRole('button', { name: 'Runs', exact: true }).click(); await page.getByRole('button', { name: 'Refresh Runs' }).click();
    await expect(page.getByText(`succeeded · ${run.id}`, { exact: true })).toBeVisible();
    const article = page.getByRole('article').filter({ hasText: run.id });
    await article.getByText('Actual Action at execution start', { exact: true }).click();
    await article.getByText('Per-target results', { exact: true }).click();
    await page.screenshot({ path: path.join(out, 'desktop-runs.png'), fullPage: true });
  });
  await check('queued cancellation and recovered uncertain are visible without replay controls', async () => {
    await expect(page.getByText('uncertain · run_interrupted_fixture', { exact: true })).toBeVisible();
    const queued = page.getByRole('article').filter({ hasText: 'run_queued_fixture' });
    await queued.getByRole('button', { name: 'Cancel queued Run' }).click();
    await expect(page.getByText('cancelled · run_queued_fixture', { exact: true })).toBeVisible();
  });
  await check('failure records keep literal/Event arguments and individual target outcomes', async () => {
    const catalog = await api('/api/actions');
    const bad = await api('/api/actions/actions', 'POST', { expectedRevision: catalog.revision, item: {
      name: 'Partial failure fixture', description: 'One missing target', enabled: true, effect: 'write_local',
      input_schema: { type: 'object', properties: { count: { type: 'integer' }, label: { type: 'string' } }, additionalProperties: false },
      execution: { kind: 'session_operation', operation: 'pause_everything', selector: { mode: 'explicit', ids: [fixtures['Control target'], 'ses_missing_fixture'] } },
    } });
    await api('/api/automation/triggers', 'POST', { expectedRevision: 0, item: { name: 'Partial failure Trigger', enabled: true, eventTypes: ['workers.all_idle'], actionId: bad.item.id, arguments: { count: { from: 'event', path: 'payload.runningCount' }, label: { from: 'literal', value: 'retained' } } } }, true);
    await api('/api/send', 'POST', { sessionId: fixtures['Event producer'], text: 'partial-failure-edge', source: 'user' });
    const state = await poll(() => api('/api/automation'), (s) => Object.values(s.runs).some((r) => r.actionId === bad.item.id && r.status === 'failed'), 'failed Run with partial results');
    const run = Object.values(state.runs).find((r) => r.actionId === bad.item.id && r.status === 'failed');
    assert.deepEqual(run.arguments, { count: 0, label: 'retained' }); assert.equal(run.targetResults.length, 2);
    assert.equal(run.result.succeeded, 1); assert.equal(run.result.failed, 1);
    await page.getByRole('button', { name: 'Refresh Runs' }).click();
    const article = page.getByRole('article').filter({ hasText: run.id });
    await expect(article.getByText(`failed · ${run.id}`, { exact: true })).toBeVisible();
    await article.getByText('Per-target results', { exact: true }).click();
    await expect(article).toContainText('ses_missing_fixture');
  });
  await check('revision conflict preserves edited draft against the real API', async () => {
    await page.getByRole('button', { name: 'Triggers', exact: true }).click();
    await page.getByLabel('Trigger name').fill('Local name preserved');
    const view = await api('/api/automation'); const t = Object.values(view.triggers).find((t) => t.actionId === action.id);
    const { revision, createdBy, createdAt, updatedAt, ...config } = t; void createdBy; void createdAt; void updatedAt;
    await api('/api/automation/triggers', 'POST', { item: { ...config, description: 'concurrent edit' }, expectedRevision: revision }, true);
    await page.getByRole('button', { name: 'Save enabled configuration' }).click();
    await expect(page.getByText(/Revision conflict/)).toBeVisible(); await expect(page.getByLabel('Trigger name')).toHaveValue('Local name preserved');
    await page.getByRole('button', { name: 'Keep draft against latest revision' }).click();
    await page.getByRole('button', { name: 'Save and disable' }).click();
    await expect(page.getByRole('button', { name: 'Save disabled draft' })).toBeVisible();
  });
  await check('desktop, 390px and 320px editor geometry has no horizontal overflow', async () => {
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.getByLabel('Trigger name').fill('Long trigger ' + 'x'.repeat(180));
      const overflow = await page.getByRole('region', { name: 'Automation management' }).evaluate((el) => el.scrollWidth - el.clientWidth); assert.ok(overflow <= 1, `main overflow ${width}: ${overflow}`);
      const body = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth); assert.ok(body <= 1);
      await page.getByLabel('Trigger name').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(out, `editor-${width}.png`) });
      await page.getByRole('button', { name: 'Save disabled draft' }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(out, `editor-bottom-${width}.png`) });
    }
  });
  await check('Session Operation remains safe to view in existing Actions; refresh drops login', async () => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('link', { name: 'Manage Actions' }).click();
    await page.getByRole('button', { name: 'Edit Browser pause' }).click();
    await expect(page.getByText('Pause Everything · Session Operation (automation only)')).toBeVisible();
    await page.goto(`${base}/react/automation`); await expect(page.getByRole('button', { name: 'Log in', exact: true })).toBeVisible();
  });
  await check('production template also creates Event-field targets; disabled Trigger deletion retains history', async () => {
    await page.getByRole('button', { name: 'Pause Everything template', exact: true }).click();
    await page.getByLabel('Action name', { exact: true }).fill('Event targets');
    await page.getByLabel('Target source').selectOption('event');
    await page.getByLabel('Target Event path').fill('payload.sessionIds');
    await page.getByRole('button', { name: 'Create Session Operation Action' }).click();
    await expect(page.getByText(/Action created:/)).toBeVisible();
    const a = (await api('/api/actions')).actions.find((a) => a.name === 'Event targets');
    assert.deepEqual(a.execution.selector, { mode: 'event', path: 'payload.sessionIds' });
    await page.getByRole('button', { name: 'Close template' }).click();
    await page.getByLabel('Local user key').fill(key); await page.getByRole('button', { name: 'Log in', exact: true }).click();
    await page.getByRole('button', { name: 'Edit Local name preserved' }).click();
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: 'Delete Trigger', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Edit Local name preserved' })).toHaveCount(0);
    assert.ok(Object.values((await api('/api/automation')).runs).some((r) => r.actionId === action.id));
  });
  await check('a real revoked Bearer returns 401, clears login and preserves the draft', async () => {
    bearer = browserBearer.slice(7); await api('/api/automation/logout', 'POST', undefined, true);
    await page.getByRole('button', { name: 'New Trigger' }).click();
    await page.getByLabel('Trigger name').fill('Preserved after 401');
    await page.getByLabel('workers.all_idle · Available', { exact: true }).check();
    await page.getByLabel('Action reference').selectOption(action.id);
    await page.getByRole('button', { name: 'Review and enable' }).click();
    await page.getByRole('button', { name: 'Confirm enable' }).click();
    await expect(page.getByText('User login required. Your draft is preserved.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Log in', exact: true })).toBeVisible();
    await expect(page.getByLabel('Trigger name')).toHaveValue('Preserved after 401');
  });
  assert.deepEqual(pageErrors, []);
  await fs.writeFile(path.join(out, 'http-evidence.json'), JSON.stringify({ identity, fixtures, run: Object.values(success.runs).find((r) => r.actionId === action.id && r.status === 'succeeded'), noPageErrors: true }, null, 2));
} catch (e) { failure = e; console.error(e); results.push({ name: 'failure', ok: false, error: String(e) }); }
finally {
  await browser?.close();
  if (server.exitCode === null) {
    await owned();
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true }); else server.kill('SIGTERM');
    await poll(async () => server.exitCode, (code) => code !== null, 'owned server exit', 10000);
  }
  portReleased = await new Promise((resolve) => { const probe = net.createServer(); probe.once('error', () => resolve(false)); probe.listen(port, '127.0.0.1', () => probe.close(() => resolve(true))); });
  if (!portReleased) failure ||= new Error('Owned listener port was not released');
  const checkoutDataUnchanged = JSON.stringify(checkoutDataBefore) === JSON.stringify(await dataSnapshot());
  if (!checkoutDataUnchanged) failure ||= new Error('Fixture escaped its runtime root');
  log.end();
  await fs.writeFile(path.join(out, 'browser-results.json'), JSON.stringify({ head: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(), identity, runtime, results, serverExitCode: server.exitCode, portReleased, checkoutDataUnchanged, ok: !failure }, null, 2));
}
if (failure) process.exitCode = 1;
