/* global process, console, fetch, URL */
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
const runtime = path.join(web, 'test-results', `eta-action-panel-${Date.now()}`);
const out = process.env.PAN_AUTOMATION_E2E_OUT || path.join(root, 'artifacts/eta-action-panel-integration');
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
  const fixtures = JSON.parse(await fs.readFile(path.join(runtime, 'fixtures.json'), 'utf8'));
  let registry = await api('/api/actions');
  await api('/api/actions/actions', 'POST', { expectedRevision: registry.revision, item: { name: 'Read control', enabled: true, effect: 'read', input_schema: { type: 'object', properties: {}, additionalProperties: false }, execution: { kind: 'api', method: 'GET', path: '/api/sessions' } } });
  registry = await api('/api/actions'); const read = registry.actions.find((a) => a.name === 'Read control');
  const groupResult = await api('/api/actions/groups', 'POST', { expectedRevision: registry.revision, item: { name: 'Immutable control', action_ids: [read.id] } });
  const group = groupResult.item;
  await api(`/api/actions/groups/${group.id}/build`, 'POST', { expectedRevision: groupResult.revision });
  const releaseBefore = (await api('/api/actions')).groups.find((g) => g.id === group.id).build;
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.route(/^https?:\/\/fonts\./, (route) => route.abort());
  const requests = []; let holdRead; let readHeld = false; let readCaptured;
  await context.route(/\/api\/(automation|actions)/, async (route) => {
    const req = route.request();
    if (req.method() !== 'GET') await owned();
    if (req.url().endsWith('/api/actions') && req.method() === 'GET' && holdRead) {
      const response = await route.fetch(); readCaptured = await response.json(); readHeld = true;
      await holdRead; await route.fulfill({ response }); return;
    }
    await route.continue();
  });
  const page = await context.newPage(); const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('request', (r) => { const pathname = new URL(r.url()).pathname;
    if (pathname.startsWith('/api/')) requests.push({ path: pathname, method: r.method(), body: r.method() === 'GET' || pathname.endsWith('/login') || pathname.endsWith('/logout') ? undefined : r.postDataJSON() });
  });
  async function dismissToasts() {
    // Screenshot cleanup only: invoke each existing dismiss handler together.
    // Per-index actionability waits can chase a different toast as animated
    // siblings retire. Product workflow interactions below still use real clicks.
    await page.getByRole('button', { name: 'Dismiss toast', exact: true }).evaluateAll((buttons) => buttons.forEach((button) => button.click()));
    await expect(page.getByRole('button', { name: 'Dismiss toast', exact: true })).toHaveCount(0);
  }
  const count = (method = 'GET', suffix = '/api/actions') => requests.filter((r) => r.method === method && r.path === suffix).length;
  async function goAutomation() {
    await page.getByRole('navigation', { name: '界面入口' }).getByRole('link', { name: 'Automation', exact: true }).click();
    await expect(page.getByRole('button', { name: 'New Trigger', exact: true })).toBeVisible();
  }
  await page.goto(`${base}/react/actions`);
  await expect(page.getByRole('button', { name: 'Edit Fixture pause', exact: true })).toBeVisible();
  await check('compact Action list and Automation share one fresh registry read across routes', async () => {
    assert.equal(count(), 1); assert.equal(requests.filter((r) => r.path === '/api/mcp/servers').length, 0);
    await goAutomation(); await page.getByRole('link', { name: 'Manage Actions' }).click();
    await expect(page.getByRole('button', { name: 'Edit Fixture pause', exact: true })).toBeVisible();
    assert.equal(count(), 1);
  });
  await check('full panel edits Session Operation and commits mutation response without registry refetch', async () => {
    await page.getByRole('button', { name: 'Edit Fixture pause', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Fixture pause', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'New Action', exact: true })).toHaveCount(0);
    await expect(page.getByLabel('Effect', { exact: true })).toBeDisabled();
    await page.getByLabel('Target source').selectOption('event');
    await page.getByLabel('Target Event path').fill('payload.sessionIds');
    const gets = count();
    await page.getByRole('button', { name: 'Save Action', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Edit Fixture pause', exact: true })).toBeVisible();
    assert.equal(count(), gets);
    assert.deepEqual((await api('/api/actions')).actions.find((a) => a.id === 'fixture_pause').execution.selector, { mode: 'event', path: 'payload.sessionIds' });
  });
  let copyId;
  await check('Duplicate preserves operation, independent targets and original immutable Group release', async () => {
    const gets = count();
    await page.getByRole('button', { name: 'More actions for Fixture pause', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Duplicate Action', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Fixture pause copy', exact: true })).toBeVisible();
    await expect(page.getByLabel('Target Event path')).toHaveValue('payload.sessionIds');
    await page.getByLabel('Target source').selectOption('explicit');
    await page.getByRole('group', { name: 'Target Sessions' }).getByText('Control target').click();
    await page.getByRole('button', { name: 'Save Action', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Edit Fixture pause copy', exact: true })).toBeVisible();
    const current = await api('/api/actions'); const copy = current.actions.find((a) => a.name === 'Fixture pause copy'); copyId = copy.id;
    assert.notEqual(copy.id, 'fixture_pause'); assert.deepEqual(copy.execution.selector, { mode: 'explicit', ids: [fixtures['Control target']] });
    assert.deepEqual(current.actions.find((a) => a.id === 'fixture_pause').execution.selector, { mode: 'event', path: 'payload.sessionIds' });
    assert.deepEqual(current.groups.find((g) => g.id === group.id).build, releaseBefore); assert.equal(count(), gets);
  });
  await check('new full-panel Session Operation creation and Group build rejection coexist with legacy build', async () => {
    await page.getByRole('button', { name: 'New Action', exact: true }).click();
    await page.getByLabel(/^Name/).fill('Created in full panel');
    await page.getByLabel(/^Description/).fill('Pause selected Sessions');
    await page.getByRole('button', { name: 'Session Operation', exact: true }).click();
    await page.getByRole('group', { name: 'Target Sessions' }).getByText('Selected target').click();
    await page.getByRole('button', { name: 'Create Action', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Edit Created in full panel', exact: true })).toBeVisible();
    await page.getByRole('button', { name: /^Groups/ }).click();
    await page.getByRole('button', { name: 'New Group', exact: true }).click();
    await page.getByLabel(/^Group name/).fill('Automation only blocked');
    await page.getByText('Created in full panel', { exact: true }).click();
    await page.getByRole('button', { name: 'Create Group', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Edit Automation only blocked', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Edit Automation only blocked', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Build Group', exact: true })).toBeDisabled();
    await expect(page.getByText(/Session Operation is automation only/).first()).toBeVisible();
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    const current = await api('/api/actions'); const blocked = current.groups.find((g) => g.name === 'Automation only blocked');
    await owned(); const denied = await fetch(`${base}/api/actions/groups/${blocked.id}/build`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expectedRevision: current.revision }) });
    assert.equal(denied.status, 400);
    assert.deepEqual(current.groups.find((g) => g.id === group.id).build, releaseBefore);
  });
  await check('Automation Pause template mutates shared registry once with no ledger or registry read', async () => {
    await goAutomation();
    const gets = count(); const ledgerReads = requests.filter((r) => r.method === 'GET' && r.path === '/api/automation').length;
    await page.getByRole('button', { name: 'Pause Everything template', exact: true }).click();
    await page.getByLabel('Action name', { exact: true }).fill('Shared template');
    await page.getByLabel('Target source').selectOption('explicit');
    await page.getByRole('group', { name: 'Target Sessions' }).getByText('Selected target').click();
    await page.getByRole('button', { name: 'Create Session Operation Action', exact: true }).click();
    await expect(page.getByText(/Disabled Action created:/)).toBeVisible();
    assert.equal(count(), gets); assert.equal(requests.filter((r) => r.method === 'GET' && r.path === '/api/automation').length, ledgerReads);
    await page.getByRole('button', { name: 'Close template', exact: true }).click();
    await page.getByRole('button', { name: 'New Trigger', exact: true }).click();
    await expect(page.getByLabel('Action reference').getByRole('option', { name: /Shared template/ })).toHaveCount(1);
    await page.getByRole('button', { name: 'Close editor', exact: true }).click();
    await page.getByRole('link', { name: 'Manage Actions' }).click();
    await expect(page.getByRole('button', { name: 'Edit Shared template', exact: true })).toBeVisible(); assert.equal(count(), gets);
  });
  await check('late registry GET cannot replace edited targets or newer mutation; explicit Refresh converges', async () => {
    let release; holdRead = new Promise((resolve) => { release = resolve; });
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await poll(async () => readHeld, Boolean, 'captured registry GET');
    await page.getByRole('button', { name: 'Edit Fixture pause copy', exact: true }).click();
    await page.getByLabel('Session ID to add').fill('ses_missing_preserved'); await page.getByRole('button', { name: 'Add Session ID', exact: true }).click();
    await page.getByRole('button', { name: 'Save Action', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Edit Fixture pause copy', exact: true })).toBeVisible();
    release(); holdRead = undefined;
    await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: 'Edit Fixture pause copy', exact: true }).click();
    await expect(page.getByText(/Not in current Session list · ses_missing_preserved/)).toBeVisible();
    await dismissToasts();
    for (const width of [390, 320]) { await page.setViewportSize({ width, height: 1000 }); await expect.poll(() => page.locator('main').last().evaluate((e) => e.scrollWidth - e.clientWidth)).toBeLessThanOrEqual(1); await page.screenshot({ path: path.join(out, `session-operation-${width}.png`) }); }
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    const gets = count(); await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled(); assert.equal(count(), gets + 1);
    const current = await api('/api/actions'); assert.ok(current.revision > readCaptured.revision);
    assert.ok(current.actions.find((a) => a.id === copyId).execution.selector.ids.includes('ses_missing_preserved'));
  });

  let queryAction;
  await check('server-discovered query template is disabled; structured nested editor and raw errors remain safe', async () => {
    await goAutomation();
    await page.getByRole('button', { name: 'Pause Everything template', exact: true }).click();
    await page.getByLabel('Action name', { exact: true }).fill('Query selected');
    await expect(page.getByLabel('Target source')).toHaveValue('query');
    await page.getByLabel('Query condition type').selectOption('and');
    await page.getByLabel('Query text value').fill('Selected target');
    await page.getByRole('button', { name: 'Add query condition', exact: true }).click();
    await page.getByLabel('Query field').nth(1).selectOption('readonlySession');
    await page.getByRole('button', { name: 'Advanced query JSON', exact: true }).click();
    await page.getByLabel('Query JSON').fill('{unfinished');
    await expect(page.getByRole('button', { name: 'Create Session Operation Action', exact: true })).toBeDisabled();
    const query = { and: [{ field: 'name', op: 'eq', value: 'Selected target' }, { field: 'readonlySession', op: 'eq', value: false }, { not: { field: 'adapter', op: 'in', value: ['codex'] } }] };
    await page.getByLabel('Query JSON').fill(JSON.stringify(query));
    await page.getByRole('button', { name: 'Create Session Operation Action', exact: true }).click();
    await expect(page.getByText(/Disabled Action created:/)).toBeVisible();
    queryAction = (await api('/api/actions')).actions.find((a) => a.name === 'Query selected');
    assert.equal(queryAction.enabled, false); assert.deepEqual(queryAction.execution.selector.query, query);
    await page.getByRole('button', { name: 'Close template', exact: true }).click();
    await page.getByRole('link', { name: 'Manage Actions' }).click();
    await page.getByRole('button', { name: 'Edit Query selected', exact: true }).click();
    await dismissToasts();
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      await expect.poll(() => page.getByRole('main', { name: 'Actions Panel' }).evaluate((e) => Array.from(e.querySelectorAll('*')).filter((el) => el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 1 && ['DIV', 'SECTION', 'MAIN', 'FIELDSET'].includes(el.tagName) && !el.classList.contains('truncate')).map((el) => el.tagName + ':' + el.className))).toEqual([]);
      await page.getByLabel('Query field').first().scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(out, `query-${width}.png`) });
      const geometry = await page.locator('main').last().evaluate((e) => ({ width: e.clientWidth, scroll: e.scrollWidth, overflowing: Array.from(e.querySelectorAll('*')).filter((el) => el.getBoundingClientRect().right > e.getBoundingClientRect().right + 1).map((el) => ({ tag: el.tagName, class: el.className, width: el.getBoundingClientRect().width })).slice(0, 15) }));
      assert.ok(geometry.scroll <= geometry.width + 1, JSON.stringify(geometry));
      await page.getByRole('button', { name: 'Save Action', exact: true }).scrollIntoViewIfNeeded();
      await expect(page.getByRole('button', { name: 'Save Action', exact: true })).toBeInViewport();
      await page.screenshot({ path: path.join(out, `query-bottom-${width}.png`) });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('switch', { name: 'Enable for automation', exact: true }).click();
    await page.getByRole('button', { name: 'Save Action', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Edit Query selected', exact: true })).toBeVisible();
  });
  await check('real HTTP and production UI query → frozen target → Pause Everything → Run selection diagnostics', async () => {
    await goAutomation();
    const key = (await fs.readFile(path.join(runtime, 'automation/user.key'), 'utf8')).trim();
    await page.getByLabel('Local user key').fill(key); await page.getByRole('button', { name: 'Log in', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'New Trigger', exact: true }).click();
    await page.getByLabel('Trigger name').fill('Query trigger');
    await page.getByLabel('workers.all_idle · Available', { exact: true }).check();
    await page.getByLabel('Action reference').selectOption(queryAction.id);
    await page.getByRole('button', { name: 'Review and enable', exact: true }).click();
    await expect(page.getByRole('region', { name: 'Enable review' })).toContainText('Query selected');
    await page.getByRole('button', { name: 'Confirm enable', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Save enabled configuration', exact: true })).toBeVisible();
    await api('/api/send', 'POST', { sessionId: fixtures['Event producer'], text: 'query-production-edge', source: 'user' });
    const state = await poll(() => api('/api/automation'), (s) => Object.values(s.runs).some((r) => r.actionId === queryAction.id && r.status === 'succeeded'), 'query Run completed');
    const run = Object.values(state.runs).find((r) => r.actionId === queryAction.id && r.status === 'succeeded');
    assert.deepEqual(run.targetIds, [fixtures['Selected target']]); assert.equal(run.selection.matched, 1); assert.ok(run.selection.candidateCount >= 3); assert.equal(run.targetResults[0].ok, true);
    assert.equal((await api(`/api/sessions/${fixtures['Selected target']}/queue`)).queuePaused, true);
    assert.equal((await api(`/api/sessions/${fixtures['Control target']}/queue`)).queuePaused, false);
    await api(`/api/sessions/${fixtures['Selected target']}`, 'PATCH', { name: 'Renamed after frozen query' });
    await page.getByRole('button', { name: 'Runs', exact: true }).click(); await page.getByRole('button', { name: 'Refresh Runs', exact: true }).click();
    const article = page.getByRole('article').filter({ hasText: run.id });
    await expect(article).toContainText('Query selection: 1 matched');
    await article.getByText('Fixed target IDs', { exact: true }).click(); await expect(article).toContainText(fixtures['Selected target']);
    await article.getByText('Selection diagnostics', { exact: true }).click();
    await dismissToasts();
    await article.getByText('Selection diagnostics', { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(out, 'query-run.png') });
    assert.deepEqual((await api('/api/automation')).runs[run.id].targetIds, run.targetIds);
  });
  await check('zero-match query succeeds honestly; malformed predicates fail closed before creation', async () => {
    const key = (await fs.readFile(path.join(runtime, 'automation/user.key'), 'utf8')).trim(); ({ token: bearer } = await api('/api/automation/login', 'POST', { key }));
    let current = await api('/api/actions');
    const empty = await api('/api/actions/actions', 'POST', { expectedRevision: current.revision, item: { ...queryAction, id: undefined, name: 'Empty query', enabled: true, execution: { kind: 'session_operation', operation: 'pause_everything', selector: { mode: 'query', query: { field: 'name', op: 'contains', value: 'NoSuchSession_ExactCase' } } } } });
    await api('/api/automation/triggers', 'POST', { expectedRevision: 0, item: { name: 'Empty query trigger', enabled: true, eventTypes: ['workers.all_idle'], actionId: empty.item.id } }, true);
    await api('/api/send', 'POST', { sessionId: fixtures['Event producer'], text: 'empty-query-edge', source: 'user' });
    const state = await poll(() => api('/api/automation'), (s) => Object.values(s.runs).some((r) => r.actionId === empty.item.id && r.status === 'succeeded'), 'zero-match Run');
    const run = Object.values(state.runs).find((r) => r.actionId === empty.item.id && r.status === 'succeeded');
    assert.deepEqual(run.targetIds, []); assert.equal(run.selection.matched, 0); assert.equal(run.result.matched, 0);
    await page.getByRole('button', { name: 'Refresh Runs', exact: true }).click();
    await expect(page.getByRole('article').filter({ hasText: run.id })).toContainText('Query selection: 0 matched');
    current = await api('/api/actions'); await owned();
    const invalid = await fetch(`${base}/api/actions/actions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expectedRevision: current.revision, item: { ...queryAction, id: undefined, name: 'Invalid query rejected', execution: { kind: 'session_operation', operation: 'pause_everything', selector: { mode: 'query', query: { field: 'pinned', op: 'eq', value: 'false' } } } } }) });
    assert.equal(invalid.status, 400); assert.equal((await api('/api/actions')).revision, current.revision);
  });

  await check('query Duplicate retains nested typed predicates and creates an independent disabled-capable editor', async () => {
    await page.getByRole('button', { name: 'Triggers', exact: true }).click();
    await page.getByRole('link', { name: 'Manage Actions' }).click();
    // The zero-match setup above deliberately wrote through a separate HTTP
    // client. Explicit refresh reconciles that external registry revision.
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: 'More actions for Query selected', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Duplicate Action', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Query selected copy', exact: true })).toBeVisible();
    await expect(page.getByLabel('Query condition type').first()).toHaveValue('and');
    await page.getByRole('switch', { name: 'Enable for automation', exact: true }).click();
    await page.getByRole('button', { name: 'Save Action', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Edit Query selected copy', exact: true })).toBeVisible();
    const registry = await api('/api/actions'); const copy = registry.actions.find((a) => a.name === 'Query selected copy');
    assert.notEqual(copy.id, queryAction.id); assert.equal(copy.enabled, false);
    assert.deepEqual(copy.execution.selector.query, registry.actions.find((a) => a.id === queryAction.id).execution.selector.query);
    assert.equal(registry.actions.find((a) => a.id === queryAction.id).enabled, true);
  });
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(out, 'http-evidence.json'), JSON.stringify({ identity, requests, originalRelease: releaseBefore, finalRegistry: await api('/api/actions'), noPageErrors: true }, null, 2));

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
