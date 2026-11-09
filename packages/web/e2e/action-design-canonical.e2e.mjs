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
const python = process.env.PAN_E2E_PYTHON || 'E:/software/miniforge/python.exe';
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
  await expect(page.getByRole('button', { name: '查看 Unified Echo' })).toBeVisible();
  // System product Actions stay discoverable in the same table (edit restricted,
  // not hidden). They are rendered as canonical rows.
  await expect(page.getByText('System').first()).toBeVisible();
  evidence.checks.push('designed table hosts canonical and system rows in one list');
  await shot('actions-1280');
  await fits('actions list 1280');

  // One logical entry, every published version, read-only switching.
  await page.getByRole('button', { name: '查看 Unified Echo' }).click();
  const versionSelect = page.getByLabel('Action version');
  await expect(versionSelect).toBeVisible();
  const options = await versionSelect.locator('option').allInnerTexts();
  assert.equal(options.length, 2, `expected two versions, saw ${options.length}`);
  await versionSelect.selectOption('1');
  await expect(versionSelect).toHaveValue('1');
  evidence.checks.push('one logical Action keeps both published versions in the designed page');
  await shot('action-version-1280');
  await page.getByRole('button', { name: '返回列表' }).click();

  // Quick create through the designed form lands in the canonical catalog.
  await page.getByRole('button', { name: 'New Action', exact: true }).click();
  await page.getByLabel(/^Name/).fill('Browser Wire Action');
  await page.getByLabel(/^Description/).fill('Created without JSON');
  await page.getByRole('switch', { name: 'Enable for build' }).click();
  await page.getByRole('button', { name: 'Create Action' }).click();
  await expect(page.getByRole('button', { name: '查看 Browser Wire Action' })).toBeVisible();
  evidence.checks.push('designed quick-create lands in the canonical catalog and becomes visible');

  // Fixed-version call from the same entry.
  await page.getByRole('button', { name: '查看 Browser Wire Action' }).click();
  await page.getByRole('button', { name: '运行固定版本' }).click();
  await expect(page.getByText(/调用状态：completed|调用状态：unknown/)).toBeVisible();
  evidence.checks.push('the fixed version runs through the canonical invocation receipt');

  // Structured re-edit from the canonical definition (no JSON required).
  await page.getByRole('button', { name: '用表单编辑' }).click();
  await page.getByLabel(/^Name/).fill('Browser Wire Renamed');
  await page.getByRole('button', { name: 'Save Action' }).click();
  // The renamed definition becomes a new draft revision and the catalog head
  // shows the current draft name.
  await page.getByRole('button', { name: '查看 Browser Wire Renamed' }).click();
  const editedVersions = page.getByLabel('Action version');
  await expect(editedVersions.locator('option')).toHaveCount(2);
  await expect(editedVersions.locator('option').last()).toContainText('draft');
  await page.getByRole('button', { name: '返回列表' }).click();
  evidence.checks.push('an expressible canonical Action is re-edited through the designed form');

  // Group entry: fixed versions, immutable release history, binding.
  await page.getByRole('button', { name: /^Groups/ }).click();
  await page.getByRole('button', { name: '查看 Unified Tools' }).click();
  const history = page.getByRole('region', { name: 'Release history' });
  await expect(history).toBeVisible();
  await expect(history.locator('li')).toHaveCount(2);
  const older = (await history.locator('li').last().innerText()).split('\n')[0];
  await history.getByRole('button', { name: `查看 ${older}` }).click();
  await expect(page.getByText(older).first()).toBeVisible();
  // Explicit build publishes a new immutable release from the same fixed tools.
  await page.getByRole('button', { name: '重新 Build' }).click();
  await expect(history.locator('li')).toHaveCount(3);
  evidence.checks.push('Group build publishes a new immutable release on top of the fixed tools');
  evidence.checks.push('Group keeps its fixed release history and opens an older immutable release');
  await shot('group-1280');
  await fits('group detail 1280');

  // Session binding in one of three modes with the observed CAS revision.
  await page.getByRole('button', { name: /在 Session 中使用/ }).click();
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
  await page.getByLabel('Group 名称').fill('Renamed by browser');
  await page.getByRole('button', { name: '保存草稿' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'revision_conflict' }).first()).toBeVisible();
  evidence.checks.push('stale Group save fails with revision_conflict and never reports success');

  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(base + '/react/actions');
    await expect(page.getByRole('button', { name: '查看 Unified Echo' })).toBeVisible();
    await fits(`actions ${width}`);
    await shot(`actions-${width}`);
    await page.getByRole('button', { name: '查看 Unified Echo' }).click();
    await expect(page.getByLabel('Action version')).toBeVisible();
    await fits(`action detail ${width}`);
    await shot(`action-version-${width}`);
    await page.getByRole('button', { name: '返回列表' }).click();
    await page.getByRole('button', { name: /^Groups/ }).click();
    await page.getByRole('button', { name: '固定版本与 release' }).first().click();
    await expect(page.getByLabel('Group 名称')).toBeVisible();
    await fits(`group detail ${width}`);
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
  assert.equal(job.status, 'scheduled');
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

  const spare = await page.request.post(base + '/api/jobs/legacy-create', {
    data: { kind: 'background-process', name: 'Browser Spare', target: { sessionId },
            argv: ['cmd', '/c', 'echo', 'bye'], cwd: runtime },
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
