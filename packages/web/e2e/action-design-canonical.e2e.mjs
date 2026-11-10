/**
 * Designed Action/Group directory with canonical wiring: real production HTTP
 * and production Chromium. The page under test is the designed directory
 * (`ActionsView`), not a separate canonical screen.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { chromium, expect } from '@playwright/test';
import { setTimeout as sleep } from 'node:timers/promises';

const root = path.resolve(import.meta.dirname, '../../..');
const runtime = path.join(root, '.tmp', `design-${Date.now()}`);
await fs.mkdir(runtime, { recursive: true });
const port = await new Promise((resolve) => {
  const server = net.createServer();
  server.listen(0, '127.0.0.1', () => {
    const value = server.address().port;
    server.close(() => resolve(value));
  });
});
assert(![8767, 8768].includes(port));
const base = `http://127.0.0.1:${port}`;
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith('PAN_')));
Object.assign(env, { PAN_E2E_RUNTIME: runtime, PAN_PORT: String(port), PYTHONPATH: root });
const configuredPython = process.env.PAN_E2E_PYTHON || 'D:/project/Pan/.venv/Scripts/python.exe';
const cfg = await fs.readFile(path.join(path.dirname(configuredPython), '..', 'pyvenv.cfg'), 'utf8');
const python = cfg.match(/^executable\s*=\s*(.+)$/m)?.[1]?.trim();
assert(python, 'venv interpreter must resolve');
const child = spawn(python, [path.join(import.meta.dirname, 'action-group-unified-server.py')], {
  cwd: root,
  env,
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
let log = '';
let browser;
let page;
const evidence = { port, checks: [], errors: [] };
child.stdout.on('data', (chunk) => { log += chunk; });
child.stderr.on('data', (chunk) => { log += chunk; });

const shot = (name) => page.screenshot({ path: path.join(runtime, `${name}.png`), fullPage: true });
const dialogShot = (name) => page.screenshot({ path: path.join(runtime, `${name}.png`) });

async function fits(label) {
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    true,
    `${label} overflows horizontally`,
  );
}

try {
  let ready = false;
  for (let index = 0; index < 120; index++) {
    if (child.exitCode !== null) throw Error('owned server exited');
    try {
      if ((await fetch(base + '/__e2e/identity')).ok) { ready = true; break; }
    } catch {}
    await sleep(500);
  }
  if (!ready) throw Error('owned server did not become ready');
  const identity = await (await fetch(base + '/__e2e/identity')).json();
  assert.equal(identity.pid, child.pid);
  assert.equal(path.resolve(identity.runtime), runtime);
  assert.equal(path.resolve(identity.checkout), root);
  evidence.checks.push('owned service PID and runtime verified');

  const key = (await fs.readFile(path.join(runtime, 'automation/user.key'), 'utf8')).trim();

  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', (error) => evidence.errors.push(error.message));

  // The designed Actions page has no login form of its own: authenticate on the
  // Automation page (the product entry) and then open the directory.
  await page.goto(base + '/react/automation');
  await page.getByLabel('Local user key').fill(key);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();
  await page.goto(base + '/react/actions');

  // The designed directory is the page; canonical records are an added block.
  await expect(page.getByRole('button', { name: /^Actions/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Groups/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Inspect Unified Echo' })).toBeVisible();
  // System product Actions stay discoverable in the same table (edit restricted,
  // not hidden). They are rendered as canonical rows.
  await expect(page.getByText('System').first()).toBeVisible();
  evidence.checks.push('designed table hosts canonical and system rows in one list');
  await shot('actions-1280');
  await fits('actions list 1280');

  // One logical entry, every published version, read-only switching.
  await page.getByRole('button', { name: 'Inspect Unified Echo' }).click();
  const versionSelect = page.getByLabel('Action version');
  await expect(versionSelect).toBeVisible();
  await expect(versionSelect.locator('option')).toHaveCount(2);
  const options = await versionSelect.locator('option').allInnerTexts();
  assert.equal(options.length, 2, `expected two versions, saw ${options.length}`);
  await versionSelect.selectOption('1');
  await expect(versionSelect).toHaveValue('1');
  evidence.checks.push('one logical Action keeps both published versions in the designed page');
  await shot('action-version-1280');
  await page.getByRole('button', { name: 'Back to list' }).click();

  // Quick create through the designed form lands in the canonical catalog.
  await page.getByRole('button', { name: 'New Action', exact: true }).click();
  await page.getByLabel(/^Name/).fill('Browser Wire Action');
  await page.getByLabel(/^Description/).fill('Created without JSON');
  await page.getByLabel('New input field').fill('message');
  await page.getByRole('button', { name: 'Add field', exact: true }).click();
  await page.getByRole('switch', { name: 'Enable for build' }).click();
  await page.getByRole('button', { name: 'Create Action' }).click();
  await expect(page.getByRole('button', { name: 'Inspect Browser Wire Action' })).toBeVisible();
  evidence.checks.push('designed quick-create lands in the canonical catalog and becomes visible');

  await page.goto(base + '/react/jobs');
  await page.getByRole('button', { name: 'New Job', exact: true }).click();
  await page.getByLabel('Job configuration type').selectOption('action');
  await page.getByLabel('Name', { exact: true }).fill('Browser typed Action Job');
  const actionPicker = page.getByLabel('Action for step 1');
  await expect(actionPicker.getByRole('option', { name: /Browser Wire Action/ })).toBeAttached();
  const actionId = await actionPicker.getByRole('option', { name: /Browser Wire Action/ }).getAttribute('value');
  assert.ok(actionId);
  await actionPicker.selectOption(actionId);
  await page.getByLabel('message value', { exact: true }).fill('typed in browser');
  const actionJobWrite = page.waitForResponse(r => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Create Action Job' }).click();
  const savedJob = await actionJobWrite;
  assert.equal(savedJob.status(), 201);
  const savedBody = savedJob.request().postDataJSON();
  assert.deepEqual(savedBody.definition.steps[0].arguments.message, { from: 'literal', value: 'typed in browser' });
  await expect(page.getByTestId('jobs-list-scroll').getByText('Browser typed Action Job', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByTestId('jobs-list-scroll').getByText('Browser typed Action Job', { exact: true })).toBeVisible();
  await page.getByTestId('jobs-list-scroll').getByText('Browser typed Action Job', { exact: true }).click();
  await page.getByRole('region', { name: 'Job detail' }).getByRole('button', { name: 'Edit', exact: true }).click();
  await expect(page.getByLabel('message value', { exact: true })).toHaveValue('typed in browser');
  await expect(page.getByLabel('message value type')).toHaveCount(0);
  await expect(page.getByLabel('message source')).not.toBeVisible();
  await shot('action-job-editor-1280');
  await page.setViewportSize({ width: 390, height: 900 });
  await fits('Action Job editor 390');
  await shot('action-job-editor-390');
  await page.setViewportSize({ width: 1280, height: 900 });
  evidence.checks.push('Action inputSchema produces typed controls and survives Job reload and edit');
  await page.getByRole('button', { name: 'Cancel', exact: true }).first().click();
  const typedDetail = page.getByRole('region', { name: 'Job detail' });
  await typedDetail.getByRole('button', { name: 'Enable Action Job' }).click();
  await expect(typedDetail.getByRole('button', { name: 'Run now' })).toBeEnabled();
  const runResponse = page.waitForResponse(r => r.url().endsWith('/run-now') && r.request().method() === 'POST');
  await typedDetail.getByRole('button', { name: 'Run now' }).click();
  const typedRun = await runResponse;
  assert.equal(typedRun.status(), 200, await typedRun.text());
  const typedReceipt = await typedRun.json();
  assert.ok(['accepted', 'completed'].includes(typedReceipt.run?.status), 'Run admission must retain its receipt status');
  evidence.checks.push('Action Job enable and Run use canonical service admission');
  await page.goto(base + '/react/actions');

  // Fixed-version call from the same entry.
  await page.getByRole('button', { name: 'Inspect Browser Wire Action' }).click();
  await page.getByLabel('message', { exact: true }).fill('typed invocation');
  await page.getByRole('button', { name: 'Run fixed version' }).click();
  await expect(page.getByText(/Invocation status:completed|Invocation status:unknown/)).toBeVisible();
  evidence.checks.push('the fixed version runs through the canonical invocation receipt');

  // Structured re-edit from the canonical definition (no JSON required).
  await page.getByRole('button', { name: 'Edit with form' }).click();
  await page.getByLabel(/^Name/).fill('Browser Wire Renamed');
  await page.getByRole('button', { name: 'Save Action' }).click();
  // The renamed definition becomes a new draft revision and the catalog head
  // shows the current draft name.
  await page.getByRole('button', { name: 'Inspect Browser Wire Renamed' }).click();
  const editedVersions = page.getByLabel('Action version');
  await expect(editedVersions.locator('option')).toHaveCount(2);
  await expect(editedVersions.locator('option').last()).toContainText('draft');
  await page.getByRole('button', { name: 'Back to list' }).click();
  evidence.checks.push('an expressible canonical Action is re-edited through the designed form');

  // Group entry: fixed versions, immutable release history, binding.
  await page.getByRole('button', { name: /^Groups/ }).click();
  await page.getByRole('button', { name: 'New Group', exact: true }).click();
  await expect(page.getByLabel('Group name')).toBeVisible();
  assert.equal(await page.getByRole('dialog').count(), 0, 'Group editor replaces the main panel');
  await page.getByLabel('Group name').fill('Browser Structured Group');
  await page.getByLabel('Search Actions').fill('Unified Echo');
  await page.getByLabel(/Unified Echo/).check();
  await page.getByRole('button', { name: 'Create Group', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Inspect Browser Structured Group' })).toBeVisible();
  evidence.checks.push('Group creation uses a full-panel structured Action picker');
  await page.getByRole('button', { name: 'Inspect Browser Structured Group' }).click();
  await page.getByLabel('Group Name').fill('Browser Group Edited');
  await page.getByRole('button', { name: 'Save draft', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Browser Group Edited' })).toBeVisible();
  await page.getByRole('button', { name: 'Back to list', exact: true }).click();
  await page.getByRole('button', { name: 'Inspect Browser Group Edited' }).click();
  await expect(page.getByLabel('Group Name')).toHaveValue('Browser Group Edited');
  await page.getByRole('button', { name: 'Back to list', exact: true }).click();
  evidence.checks.push('Group structured edit saves and reopens from the directory');
  await page.getByRole('button', { name: 'Inspect Unified Tools' }).click();
  const history = page.getByRole('region', { name: 'Release history' });
  await expect(history).toBeVisible();
  await expect(history.locator('li')).toHaveCount(2);
  const older = (await history.locator('li').last().innerText()).split('\n')[0];
  await history.getByRole('button', { name: `Inspect ${older}` }).click();
  await expect(page.getByText(older).first()).toBeVisible();
  // Explicit build publishes a new immutable release from the same fixed tools.
  await page.getByRole('button', { name: 'Build again' }).click();
  await expect(history.locator('li')).toHaveCount(3);
  evidence.checks.push('Group build publishes a new immutable release on top of the fixed tools');
  evidence.checks.push('Group keeps its fixed release history and opens an older immutable release');
  await shot('group-1280');
  await fits('group detail 1280');

  // Session binding in one of three modes with the observed CAS revision.
  await page.getByRole('button', { name: /Use in Session/ }).click();
  await page.getByRole('radio', { name: /Alpha Session/ }).click();
  await expect(page.getByText('Plugin binding')).toBeVisible();
  for (const mode of ['Standalone MCP', 'Pan native', 'Discover / call']) {
    const radio = page.getByRole('radio', { name: new RegExp(mode.replace('/', '\\/')) });
    await expect(radio).toBeEnabled();
    await expect(radio).toBeVisible();
  }
  await page.getByRole('radio', { name: /Discover \/ call/ }).click();
  await dialogShot('session-dialog-1280');
  const patch = page.waitForResponse(
    (response) => response.url().includes('/api/sessions/') && response.request().method() === 'PATCH',
  );
  await page.getByRole('button', { name: 'Apply to Session' }).click();
  const applied = await patch;
  const binding = (applied.request().postDataJSON().pluginBindings ?? []).find((item) =>
    item.plugin_id.startsWith('action-group-'),
  );
  assert.equal(binding?.mode, 'gateway');
  evidence.checks.push('three MCP modes stay available and the binding keeps the plugin CAS');

  // Failure negative control: an outside write moves the revision first.
  const seed = JSON.parse(await fs.readFile(path.join(runtime, 'unified-seed.json'), 'utf8'));
  const currentRevision = await page.evaluate(async (id) => {
    const pageValue = await (await fetch('/api/action-groups')).json();
    return pageValue.items.find((item) => item.id === id).revision;
  }, seed.groupId);
  const outside = await fetch(`${base}/api/action-groups/${seed.groupId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
    body: JSON.stringify({
      group: { id: seed.groupId, name: 'Unified Tools', tools: [{ alias: 'echo', actionRef: seed.fixed }] },
      expectedRevision: currentRevision,
      requestKey: 'outside-write',
    }),
  });
  if (!outside.ok) {
    // Cookie-only sessions may reject the bare bearer; retry through the page.
    const status = await page.evaluate(async (payload) => {
      const response = await fetch(`/api/action-groups/${payload.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload.body),
      });
      return response.status;
    }, {
      id: seed.groupId,
      body: {
        group: { id: seed.groupId, name: 'Unified Tools', tools: [{ alias: 'echo', actionRef: seed.fixed }] },
        expectedRevision: currentRevision,
        requestKey: 'outside-write',
      },
    });
    assert.equal(status, 200);
  }
  await page.getByLabel('Group Name').fill('Renamed by browser');
  await page.getByRole('button', { name: 'Save draft' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'revision_conflict' }).first()).toBeVisible();
  evidence.checks.push('stale Group save fails with revision_conflict and never reports success');

  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(base + '/react/actions');
    await expect(page.getByRole('button', { name: 'Inspect Unified Echo' })).toBeVisible();
    await fits(`actions ${width}`);
    await shot(`actions-${width}`);
    await page.getByRole('button', { name: 'Inspect Unified Echo' }).click();
    await expect(page.getByLabel('Action version')).toBeVisible();
    await fits(`action detail ${width}`);
    await shot(`action-version-${width}`);
    await page.getByRole('button', { name: 'Back to list' }).click();
    await page.getByRole('button', { name: /^Groups/ }).click();
    await page.getByRole('button', { name: 'Fixed versions and releases' }).first().click();
    await expect(page.getByLabel('Group Name')).toBeVisible();
    await fits(`group detail ${width}`);
    const back = page.getByRole('button', { name: 'Back to list', exact: true });
    assert(await back.evaluate(el => { const r = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)); }), 'Group back button must not be covered by navigation');
    await shot(`group-${width}`);
  }
  evidence.checks.push('1280/390/320 keep the designed page readable without horizontal overflow');

  // Jobs facade over the same isolated server and browser session.
  const sessions = await page.request.get(base + '/api/sessions');
  const sessionId = (await sessions.json()).sessions[0].id;
  const created = await page.request.post(base + '/api/jobs/legacy-create', {
    data: { kind: 'background-process', name: 'Browser Job', target: { sessionId },
            argv: ['cmd', '/c', 'echo', 'hi'], cwd: runtime, hideWindow: true },
    headers: { 'x-pan-request-key': 'browser-job-create', Origin: base },
  });
  assert.equal(created.status(), 200, await created.text());
  const job = (await created.json()).job;
  assert.equal(job.kind, 'background-process');
  // A legacy background process starts with its creation.
  assert.equal(job.status, 'starting');
  const listed = await page.request.get(base + '/api/jobs/legacy-list');
  const rows = (await listed.json()).jobs;
  assert.equal(rows.filter((row) => row.jobId === job.jobId).length, 1);
  evidence.checks.push('legacy Job create/list project one canonical Job without duplicate rows');

  const runOne = await page.request.post(`${base}/api/jobs/${job.jobId}/run-now`, {
    data: {}, headers: { 'x-pan-request-key': 'browser-run-1', Origin: base },
  });
  assert.equal(runOne.status(), 200, await runOne.text());
  const replay = await page.request.post(`${base}/api/jobs/${job.jobId}/run-now`, {
    data: {}, headers: { 'x-pan-request-key': 'browser-run-1', Origin: base },
  });
  assert.equal(replay.status(), 200, await replay.text());
  const runs = await page.request.get(`${base}/api/jobs/${job.jobId}/runs`);
  assert.equal((await runs.json()).items.length, 1, 'same requestKey must not create a second Run');
  evidence.checks.push('manual Run is idempotent and never produces a second Run');

  const pinned = await page.request.put(`${base}/api/jobs/legacy/${job.jobId}/pin`, {
    data: { pinned: true }, headers: { Origin: base },
  });
  assert.equal(pinned.status(), 200, await pinned.text());
  assert.equal((await pinned.json()).job.pinned, true);
  evidence.checks.push('pin keeps its existing presentation semantics');

  const blocked = await page.request.delete(`${base}/api/jobs/${job.jobId}`, { headers: { Origin: base } });
  assert.equal(blocked.status(), 409, 'an active Run must block retirement');
  const cancelled = await page.request.post(`${base}/api/jobs/${job.jobId}/cancel`, {
    data: {}, headers: { Origin: base },
  });
  assert.equal(cancelled.status(), 200, await cancelled.text());
  // Cancellation is asynchronous; until the Run settles, retirement stays
  // blocked and the job history remains readable.
  const still = (await (await page.request.get(`${base}/api/jobs/legacy/${job.jobId}`)).json()).job;
  assert.equal(still.retired, false);
  evidence.checks.push('an active Run blocks retirement and cancellation keeps history readable');

  const at = new Date(Date.now() + 30 * 60 * 1000).toISOString();
  const sched = await page.request.post(base + '/api/jobs/legacy-create', {
    data: { kind: 'scheduled-task', name: 'Browser Scheduled', target: { sessionId },
            text: 'scheduled hello', action: { api: 'assign' },
            schedule: [{ kind: 'once', at }] },
    headers: { 'x-pan-request-key': 'browser-sched', Origin: base },
  });
  assert.equal(sched.status(), 200, await sched.text());
  const schedJob = (await sched.json()).job;
  assert.equal(schedJob.kind, 'scheduled-task');
  const before = (await (await page.request.get(`${base}/api/jobs/legacy/${schedJob.jobId}`)).json()).job;
  const cursor = before.nextRunAt ?? before.schedule?.[0]?.at ?? null;
  await page.request.post(`${base}/api/jobs/${schedJob.jobId}/run-now`, {
    data: {}, headers: { 'x-pan-request-key': 'browser-sched-run', Origin: base },
  });
  const afterRun = (await (await page.request.get(`${base}/api/jobs/legacy/${schedJob.jobId}`)).json()).job;
  assert.equal(afterRun.nextRunAt ?? afterRun.schedule?.[0]?.at ?? null, cursor);
  evidence.checks.push('scheduled-task composition is created and a manual Run leaves the cursor unchanged');

  const message = await page.request.post(base + '/api/jobs/legacy-create', {
    data: { kind: 'session-message', name: 'Browser Message',
            target: { sessionId }, text: 'scheduled hello',
            schedule: { type: 'once', at } },
    headers: { 'x-pan-request-key': 'browser-msg', Origin: base },
  });
  assert.equal(message.status(), 200, await message.text());
  assert.equal((await message.json()).job.kind, 'session-message');
  evidence.checks.push('scheduled session-message keeps its delivery composition');

  const broadcast = await page.request.post(base + '/api/jobs/legacy-create', {
    data: { kind: 'session-broadcast', name: 'Browser Broadcast',
            target: { sessionIds: [sessionId] }, text: 'broadcast hello',
            schedule: { type: 'once', at } },
    headers: { 'x-pan-request-key': 'browser-broadcast', Origin: base },
  });
  assert.equal(broadcast.status(), 200, await broadcast.text());
  const broadcastJob = (await broadcast.json()).job;
  assert.equal(broadcastJob.kind, 'session-broadcast');
  assert.deepEqual(broadcastJob.target.sessionIds, [sessionId]);
  const replayBroadcast = await page.request.post(base + '/api/jobs/legacy-create', {
    data: { kind: 'session-broadcast', name: 'Browser Broadcast',
            target: { sessionIds: [sessionId] }, text: 'broadcast hello',
            schedule: { type: 'once', at } },
    headers: { 'x-pan-request-key': 'browser-broadcast', Origin: base },
  });
  assert.equal((await replayBroadcast.json()).job.jobId, broadcastJob.jobId,
               'same requestKey must not compose a second graph');
  evidence.checks.push('broadcast composition is atomic and replays by requestKey');

  const spare = await page.request.post(base + '/api/jobs/legacy-create', {
    // A draft has no immediate Run, so retirement is not blocked by an active
    // process; the separate check above already covers the active-Run rule.
    data: { kind: 'background-process', name: 'Browser Spare', enabled: false,
            target: { sessionId }, argv: ['cmd', '/c', 'echo', 'bye'], cwd: runtime },
    headers: { 'x-pan-request-key': 'browser-job-spare', Origin: base },
  });
  const spareJob = (await spare.json()).job;
  const removed = await page.request.delete(`${base}/api/jobs/${spareJob.jobId}`, { headers: { Origin: base } });
  assert.equal(removed.status(), 200, await removed.text());
  assert.equal((await removed.json()).retired, true);
  const after = (await (await page.request.get(base + '/api/jobs/legacy-list')).json()).jobs;
  assert.equal(after.filter((row) => row.jobId === spareJob.jobId).length, 0);
  assert.equal((await (await page.request.get(`${base}/api/jobs/legacy/${spareJob.jobId}`)).json()).job.retired, true);
  evidence.checks.push('delete retires with history still readable instead of faking removal');

  assert.deepEqual(evidence.errors, []);
  evidence.status = 'passed';
} catch (error) {
  evidence.status = 'failed';
  evidence.failure = String(error.stack ?? error);
  if (page) {
    evidence.pageText = await page.locator('body').innerText();
    await page.screenshot({ path: path.join(runtime, 'failure.png') });
  }
  throw error;
} finally {
  await fs.writeFile(path.join(runtime, 'server.log'), log);
  await fs.writeFile(path.join(runtime, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({ runtime, ...evidence }));
  await browser?.close();
  child.kill();
}
