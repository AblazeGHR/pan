/* global process, console, fetch */
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
const runtime = path.join(web, 'test-results', `automation-review-${Date.now()}`);
const out = process.env.PAN_AUTOMATION_E2E_OUT || path.join(root, 'artifacts/event-trigger-ui-review');
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
const results = []; let browser; let identity; let failure; let bearer; let portReleased = false;
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
  await poll(() => fetch(`${base}/__e2e/identity`).then((r) => r.json()), (v) => Number.isInteger(v.pid), 'owned server ready');
  identity = await owned();
  const saved = await api('/api/automation/triggers', 'POST', { expectedRevision: 0, item: {
    name: 'Literal review', enabled: false, eventTypes: ['workers.all_idle'], actionId: 'fixture_pause', arguments: {},
    condition: { and: [{ field: 'workers.runningCount', op: 'eq', value: 11 }, { field: 'workers.runningCount', op: 'eq', value: 22 }] },
  } });
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.route(/^https?:\/\/fonts\./, (route) => route.abort());
  let hold; let held; const posts = [];
  await context.route(/\/api\/(automation|actions)/, async (route) => {
    if (route.request().method() !== 'GET') {
      await owned();
      if (route.request().url().endsWith('/automation/triggers')) { posts.push(route.request().postDataJSON()); if (hold) { held = true; await hold; } }
    }
    await route.continue();
  });
  const page = await context.newPage(); const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(`${base}/react/automation`);
  await page.getByRole('button', { name: 'Edit Literal review', exact: true }).click();
  await check('deleting first condition keeps visible 22 equal to real POST and ledger value', async () => {
    await page.getByRole('button', { name: 'Remove condition 1' }).click();
    await expect(page.getByLabel('Compare value (JSON)')).toHaveValue('22');
    await page.getByRole('button', { name: 'Save disabled draft' }).click();
    await expect(page.getByText('Trigger saved.')).toBeVisible();
    assert.equal(posts.at(-1).item.condition.and[0].value, 22);
    assert.equal((await api('/api/automation')).triggers[saved.item.id].condition.and[0].value, 22);
  });
  await check('cancelled New, Edit, Close and router departure retain unfinished JSON and validity', async () => {
    await page.getByLabel('Compare value (JSON)').fill('22e');
    for (const control of [page.getByRole('button', { name: 'New Trigger', exact: true }), page.getByRole('button', { name: 'Edit Waiting dependency fixture', exact: true }), page.getByRole('button', { name: 'Close editor' }), page.getByRole('link', { name: 'Manage Actions' })]) {
      page.once('dialog', (dialog) => dialog.dismiss()); await control.click();
      await expect(page.getByLabel('Compare value (JSON)')).toHaveValue('22e');
      assert.equal(await page.getByLabel('Compare value (JSON)').evaluate((el) => el.checkValidity()), false);
      assert.ok(page.url().endsWith('/automation'));
    }
    const count = posts.length;
    await page.getByRole('button', { name: 'Save disabled draft' }).click();
    assert.equal(posts.length, count);
    await page.getByLabel('Compare value (JSON)').fill('22.0');
    await page.getByLabel('Description', { exact: true }).fill('Unrelated rerender');
    await expect(page.getByLabel('Compare value (JSON)')).toHaveValue('22.0');
  });
  async function concurrent(value) {
    const current = (await api('/api/automation')).triggers[saved.item.id];
    const { revision, createdBy, createdAt, updatedAt, ...config } = current; void createdBy; void createdAt; void updatedAt;
    return api('/api/automation/triggers', 'POST', { expectedRevision: revision, item: { ...config, condition: { and: [{ field: 'workers.runningCount', op: 'eq', value }] } } });
  }
  await check('real 409 rebase preserves invalid JSON; load server replaces value and validity', async () => {
    await concurrent(99);
    await page.getByRole('button', { name: 'Save disabled draft' }).click();
    await expect(page.getByText(/Revision conflict/)).toBeVisible();
    await expect(page.getByLabel('Compare value (JSON)')).toHaveValue('22.0');
    await page.getByLabel('Compare value (JSON)').fill('22e');
    await page.getByRole('button', { name: 'Keep draft against latest revision' }).click();
    await expect(page.getByLabel('Compare value (JSON)')).toHaveValue('22e');
    assert.equal(await page.getByLabel('Compare value (JSON)').evaluate((el) => el.checkValidity()), false);
    await concurrent(33); await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await page.getByRole('button', { name: 'Discard draft and load server' }).click();
    await expect(page.getByLabel('Compare value (JSON)')).toHaveValue('33');
    assert.equal(await page.getByLabel('Compare value (JSON)').evaluate((el) => el.checkValidity()), true);
    await page.screenshot({ path: path.join(out, 'loaded-server.png') });
    await page.getByRole('button', { name: 'Close editor' }).click();
    await expect(page.getByLabel('Trigger name')).toHaveCount(0);
  });
  await check('accepted discard switches editor; condition structure replacement resets literal correctly', async () => {
    await page.getByRole('button', { name: 'New Trigger', exact: true }).click();
    await page.getByLabel('Trigger name').fill('Discard me');
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: 'Edit Literal review', exact: true }).click();
    await expect(page.getByLabel('Compare value (JSON)')).toHaveValue('33');
    await page.getByLabel('Condition type').first().selectOption('comparison');
    await expect(page.getByLabel('Compare value (JSON)')).toHaveValue('0');
  });
  await check('pending save blocks switching and duplicate submission; successful save closes without dirty prompt', async () => {
    let release; hold = new Promise((resolve) => { release = resolve; });
    const count = posts.length;
    await page.getByRole('button', { name: 'Save disabled draft' }).click();
    await poll(async () => held, Boolean, 'held browser save');
    await expect(page.getByRole('button', { name: 'New Trigger', exact: true })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Close editor' })).toBeDisabled();
    await page.getByRole('button', { name: 'Save disabled draft' }).evaluate((el) => { el.form.requestSubmit(); el.form.requestSubmit(); });
    assert.equal(posts.length, count + 1);
    release(); hold = undefined;
    await expect(page.getByText('Trigger saved.')).toBeVisible();
    assert.equal(posts.at(-1).item.condition.value, 0);
    await page.setViewportSize({ width: 320, height: 900 });
    await page.getByLabel('Compare value (JSON)').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(out, 'saved-320.png') });
    await page.getByRole('button', { name: 'Close editor' }).click();
    await expect(page.getByLabel('Trigger name')).toHaveCount(0);
    await page.getByRole('button', { name: 'New Trigger', exact: true }).click();
    await page.getByLabel('Trigger name').fill('Leave for Actions');
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('link', { name: 'Manage Actions' }).click();
    await expect(page.getByRole('heading', { name: 'Actions & Groups', exact: true })).toBeVisible();
  });
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(out, 'http-evidence.json'), JSON.stringify({ identity, posts, noPageErrors: true }, null, 2));

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
