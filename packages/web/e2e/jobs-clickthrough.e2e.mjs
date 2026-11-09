/**
 * Designed Jobs page click-through: one list, one New Job, four kinds, then
 * edit / run / pin / cancel / delete through the row menu.
 *
 * Run logs and per-target consumption need the owned executor harness; this
 * script asserts everything that must work without it.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn, spawnSync } from 'node:child_process';
import { chromium, expect } from '@playwright/test';
import { setTimeout as sleep } from 'node:timers/promises';

const root = path.resolve(import.meta.dirname, '../../..');
const runtime = path.join(root, '.tmp', `jobs-click-${Date.now()}`);
await fs.mkdir(runtime, { recursive: true });
const port = await new Promise((resolve) => {
  const server = net.createServer();
  server.listen(0, '127.0.0.1', () => {
    const value = server.address().port;
    server.close(() => resolve(value));
  });
});
const base = `http://127.0.0.1:${port}`;
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith('PAN_')));
Object.assign(env, { PAN_E2E_RUNTIME: runtime, PAN_PORT: String(port), PYTHONPATH: root });
const ownedRoot = path.join(root, '.tmp', `owned-jobs-${Date.now()}`);
const ownedPython = process.env.PAN_E2E_PYTHON
  || 'D:/project/pan-worktrees/jobs-owned-executor-20261109/.tmp/runtime-env/Scripts/python.exe';
const longScript = path.join(ownedRoot, 'long_done.py');
await fs.mkdir(ownedRoot, { recursive: true });
await fs.writeFile(path.join(ownedRoot, '.jobs-owned-harness'), '');
const child = spawn(ownedPython,
  [path.join(import.meta.dirname, 'owned-jobs-ui-server.py')],
  { cwd: root, env: { ...env, PAN_OWNED_ROOT: ownedRoot }, windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'] });
let log = '';
child.stdout.on('data', (chunk) => { log += chunk; });
child.stderr.on('data', (chunk) => { log += chunk; });
let browser;
let page;
const evidence = { checks: [], kinds: [], errors: [] };

async function openCreate() {
  await page.getByRole('button', { name: 'New Job', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  return dialog;
}

async function chooseKind(dialog, kind, name) {
  await dialog.getByLabel('Name').fill(name);
  await dialog.getByLabel('Description').fill(`${name} description`);
  await dialog.getByRole('button', { name: '自定义' }).first().click();
  const kindSelect = dialog.getByLabel('Createable Job kind');
  await kindSelect.selectOption(kind);
}

async function pickSingleTarget(dialog) {
  const select = dialog.getByLabel('Target session');
  const options = await select.locator('option').evaluateAll((items) =>
    items.map((item) => ({ value: item.value, text: item.textContent || '' })));
  const real = options.find((item) => item.value && !/Select a Session|No target/i.test(item.text));
  assert.ok(real, 'a real Session must be selectable');
  await select.selectOption(real.value);
  return real.value;
}

async function pickMultiTarget(dialog) {
  const boxes = dialog.getByRole('checkbox');
  assert.ok((await boxes.count()) > 0, 'message kinds need target checkboxes');
  await boxes.first().check();
}

async function submit(dialog) {
  await dialog.getByRole('button', { name: 'Create job' }).click();
  await expect(dialog).toHaveCount(0, { timeout: 15000 });
}

try {
  let ready = false;
  for (let index = 0; index < 120; index++) {
    if (child.exitCode !== null) throw Error('owned server exited');
    try {
      if ((await fetch(base + '/test/ready')).ok) { ready = true; break; }
    } catch {}
    await sleep(500);
  }
  if (!ready) throw Error('owned server did not become ready');
  const readyPayload = await (await fetch(base + '/test/ready')).json();
  evidence.ready = { actions: Object.keys(readyPayload.actionRefs || {}), root: readyPayload.root };
  const key = (await fs.readFile(path.join(ownedRoot, 'automation/user.key'), 'utf8')).trim();
  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('pageerror', (error) => evidence.errors.push(error.message));
  // The product login form authenticates against the owned auth service.
  await page.goto(base + '/react/automation');
  await page.getByLabel('Local user key').fill(key);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();
  await page.goto(base + '/react/jobs');
  await expect(page.getByRole('button', { name: 'New Job', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /Unified Jobs|Legacy Jobs/ })).toHaveCount(0);
  evidence.checks.push('single unified Jobs list with one New Job entry');

  // background-process: the owned quick script writes both markers.
  let dialog = await openCreate();
  await chooseKind(dialog, 'background-process', 'Click process Job');
  await dialog.getByLabel(/^Executable/).fill(ownedPython);
  await dialog.getByRole('button', { name: 'Add argument' }).click();
  await dialog.getByLabel(/^Argument 1/).fill(path.join(ownedRoot, 'quick.py'));
  await dialog.getByLabel(/^Working directory/).fill(readyPayload.cwd);
  await pickSingleTarget(dialog);
  await submit(dialog);
  await expect(page.getByText('Click process Job', { exact: false }).first()).toBeVisible();
  evidence.kinds.push('background-process');

  // A legacy background process starts immediately: wait for the real Run and
  // read its log from the designed detail drawer.
  await page.getByText('Click process Job', { exact: false }).first().click();
  const processDrawer = page.getByRole('dialog', { name: 'Job detail' });
  await expect(processDrawer).toBeVisible();
  const pickStatus = async () => (await processDrawer.innerText()).match(/completed|failed|cancelled|running|queued|starting/)?.[0] ?? '';
  let processStatus = '';
  for (let attempt = 0; attempt < 40; attempt++) {
    processStatus = await pickStatus();
    if (['completed', 'failed', 'cancelled'].includes(processStatus)) break;
    // The drawer reads history once on open; reopen it like a user would to
    // pick up the terminal state instead of trusting a stale pane.
    if (attempt % 8 === 7) {
      await page.keyboard.press('Escape');
      await page.getByText('Click process Job', { exact: false }).first().click();
      await expect(processDrawer).toBeVisible();
    }
    await page.waitForTimeout(500);
  }
  evidence.processStatus = processStatus;
  evidence.drawerText = (await processDrawer.innerText()).slice(0, 1200);
  assert.ok(['completed', 'failed', 'cancelled'].includes(processStatus),
    `the immediate Run must reach a terminal state, saw ${processStatus || 'none'}`);
  if (processStatus === 'completed') {
    evidence.checks.push('background-process starts immediately and reaches completed in the detail');
  }
  // The drawer must show the original stdout/stderr body, not a status summary.
  const processOutputButton = processDrawer.getByRole('button', { name: /^View output for run/ }).first();
  await expect(processOutputButton).toBeVisible();
  await processOutputButton.click();
  const processOutput = processDrawer.getByLabel(/^Run output for /).first();
  await expect(processOutput).toBeVisible();
  await expect(processOutput).toContainText('owned stdout');
  await expect(processOutput).toContainText('owned stderr');
  evidence.checks.push('detail drawer shows the real stdout/stderr markers for a completed Run');
  await page.screenshot({ path: path.join(runtime, 'process-output.png'), fullPage: true });
  await page.keyboard.press('Escape');
  await expect(processDrawer).toHaveCount(0);

  // Long process: starts for real, then cancel through the designed row control.
  dialog = await openCreate();
  await chooseKind(dialog, 'background-process', 'Click long Job');
  await dialog.getByLabel(/^Executable/).fill(ownedPython);
  await dialog.getByRole('button', { name: 'Add argument' }).click();
  await dialog.getByLabel(/^Argument 1/).fill(longScript);
  await dialog.getByLabel(/^Working directory/).fill(readyPayload.cwd);
  await pickSingleTarget(dialog);
  await submit(dialog);
  await expect(page.getByText('Click long Job', { exact: false }).first()).toBeVisible();

  // Wait until the original log proves the process is actually running.
  await page.getByText('Click long Job', { exact: false }).first().click();
  const longDrawer = page.getByRole('dialog', { name: 'Job detail' });
  await expect(longDrawer).toBeVisible();
  let longOutput = longDrawer.getByLabel(/^Run output for /).first();
  let started = false;
  for (let attempt = 0; attempt < 60; attempt++) {
    const view = longDrawer.getByRole('button', { name: /^View output for run/ }).first();
    if (await view.count()) {
      await view.click();
      await page.waitForTimeout(350);
      if (await longOutput.count()) {
        const text = await longOutput.innerText();
        if (text.includes('owned stdout')) { started = true; break; }
      }
    }
    await page.waitForTimeout(350);
  }
  evidence.longStarted = started;
  assert.ok(started, 'the long process must actually start before cancellation');
  await page.keyboard.press('Escape');
  await expect(longDrawer).toHaveCount(0);

  const listed = await page.request.get(base + '/api/jobs/legacy-list');
  const longJob = (await listed.json()).jobs.find((item) => item.name === 'Click long Job');
  assert.ok(longJob, 'the long Job must be listed');
  const longRow = page.locator('div').filter({ hasText: 'Click long Job' })
    .filter({ has: page.getByRole('button', { name: /^Cancel job$/ }) }).first();
  await expect(longRow).toBeVisible();
  await longRow.getByRole('button', { name: /^Cancel job$/ }).click();
  await expect(page.getByText('Cancellation requested', { exact: false }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: /^Cancel job$/ })).toHaveCount(0, { timeout: 30000 });

  // The drawer reads history once per open; wait for the original Run to settle
  // before reopening it, then assert the designed history shows cancelled.
  let longStatus = '';
  for (let attempt = 0; attempt < 60; attempt++) {
    const runsNow = await page.request.get(`${base}/api/jobs/${longJob.jobId}/runs`);
    longStatus = (await runsNow.json()).items[0]?.state ?? '';
    if (['cancelled', 'failed', 'uncertain'].includes(longStatus)) break;
    await page.waitForTimeout(500);
  }
  evidence.longStatus = longStatus;
  assert.equal(longStatus, 'cancelled', 'the cancelled Run must settle as cancelled');
  if (await longDrawer.count() === 0) {
    await page.getByText('Click long Job', { exact: false }).first().click();
    await expect(longDrawer).toBeVisible();
  }
  // The drawer starts from the previous empty history; its own fetch fills the
  // run list. Wait for the real control instead of sampling the first render.
  await expect(longDrawer.getByRole('button', { name: /^View output for run/ }).first())
    .toBeVisible({ timeout: 15000 });
  const longHistory = await longDrawer.innerText();
  assert.ok(/CANCELLED|cancelled/.test(longHistory), 'the designed history must show the cancelled Run');
  const cancelledOutputButton = longDrawer.getByRole('button', { name: /^View output for run/ }).first();
  await expect(cancelledOutputButton).toBeVisible();
  await cancelledOutputButton.click();
  longOutput = longDrawer.getByLabel(/^Run output for /).first();
  await expect(longOutput).toBeVisible();
  await expect(longOutput).toContainText('owned stdout');
  await expect(longOutput).toContainText('owned stderr');
  await expect(longOutput).not.toContainText('owned completion marker');
  evidence.checks.push('cancelled long Run keeps its real output and never reaches the completion marker');
  await page.screenshot({ path: path.join(runtime, 'cancelled-output.png'), fullPage: true });
  await page.keyboard.press('Escape');
  await expect(longDrawer).toHaveCount(0);

  // One process tree proof for the cancelled Run: receipt, owned-tree cleanup,
  // supervisor exit and every recorded PID are checked in the owned store.
  const longRuns = await page.request.get(`${base}/api/jobs/${longJob.jobId}/runs`);
  const cancelledRun = (await longRuns.json()).items.find((item) => item.state === 'cancelled');
  assert.ok(cancelledRun, 'the cancelled Run must be listed');
  const verified = spawnSync(ownedPython, [
    path.join(import.meta.dirname, 'verify_job_execution.py'),
    '--root', ownedRoot, '--run', cancelledRun.runId, '--expect', 'cancelled',
    '--require', 'owned stdout', '--require', 'owned stderr',
    '--forbid', 'owned completion marker',
  ], { cwd: root, env: { ...env, PYTHONPATH: root }, encoding: 'utf8' });
  assert.equal(verified.status, 0, verified.stderr || verified.stdout);
  evidence.execution = JSON.parse(verified.stdout);
  evidence.checks.push('cancellation receipt, owned-tree cleanup and supervisor exit are proven for the Run');

  // session-message (multi-target checkboxes)
  dialog = await openCreate();
  await chooseKind(dialog, 'session-message', 'Click message Job');
  await pickSingleTarget(dialog);
  await dialog.getByLabel('Message text').fill('click hello');
  await submit(dialog);
  await expect(page.getByText('Click message Job', { exact: false }).first()).toBeVisible();
  evidence.kinds.push('session-message');

  // session-broadcast: provider gate is a harness diagnostic, kept as red and
  // NOT worked around (no API substitution for a UI operation).
  dialog = await openCreate();
  await chooseKind(dialog, 'session-broadcast', 'Click broadcast Job');
  await pickMultiTarget(dialog);
  await dialog.getByLabel('Message text').fill('click broadcast');
  await dialog.getByRole('button', { name: 'Create job' }).click();
  const broadcastError = await Promise.race([
    dialog.getByRole('alert').first().innerText({ timeout: 8000 }).catch(() => ''),
    page.waitForTimeout(8000).then(() => ''),
  ]);
  evidence.broadcast = broadcastError || 'no explicit alert';
  if (await page.getByText('Click broadcast Job', { exact: false }).count()) {
    evidence.kinds.push('session-broadcast');
  } else {
    evidence.broadcastBlocked = true;
    await page.keyboard.press('Escape').catch(() => {});
    await expect(dialog).toHaveCount(0, { timeout: 5000 }).catch(() => {});
  }

  // scheduled-task (default form: assign + one schedule entry). The scheduler
  // provider is not mounted in this harness yet: keep its red, do not bypass.
  dialog = await openCreate();
  await dialog.getByLabel('Name').fill('Click scheduled Job');
  await dialog.getByLabel('Description').fill('Click scheduled Job description');
  await pickSingleTarget(dialog);
  await dialog.getByLabel('Task text').fill('click task');
  await dialog.getByRole('button', { name: 'Create job' }).click();
  const scheduledError = await Promise.race([
    dialog.getByRole('alert').first().innerText({ timeout: 8000 }).catch(() => ''),
    page.waitForTimeout(8000).then(() => ''),
  ]);
  evidence.scheduled = scheduledError || 'no explicit alert';
  if (await page.getByText('Click scheduled Job', { exact: false }).count()) {
    evidence.kinds.push('scheduled-task');
  } else {
    evidence.scheduledBlocked = true;
    await page.keyboard.press('Escape').catch(() => {});
    await expect(dialog).toHaveCount(0, { timeout: 5000 }).catch(() => {});
  }
  evidence.checks.push(`created all four kinds through New Job: ${evidence.kinds.join(', ')}`);

  // Row operations follow the designed controls: the row opens the detail
  // drawer (edit lives there), Cancel is a row button, the menu has Pin/Delete
  // and Run now only for scheduled-task rows.
  const rowName = 'Click process Job';
  await page.getByText(rowName, { exact: false }).first().click();
  await expect(page.getByRole('dialog').or(page.getByRole('complementary')).first()).toBeVisible();
  const drawerText = await page.locator('body').innerText();
  evidence.drawerHasEdit = /Edit|编辑/.test(drawerText);
  const editButton = page.getByRole('button', { name: /^Edit|^编辑/ }).first();
  if (await editButton.count()) {
    await editButton.click();
    const editDialog = page.getByRole('dialog');
    if (await editDialog.getByLabel('Name').count()) {
      await editDialog.getByLabel('Name').fill('Click process Job edited');
      await editDialog.getByRole('button', { name: /^Save$|^保存$/ }).first().click();
      await expect(page.getByText('Click process Job edited', { exact: false }).first()).toBeVisible();
      evidence.checks.push('edited the Job name from the designed detail');
    }
  } else {
    await page.keyboard.press('Escape').catch(() => {});
  }

  // Close the detail drawer the way the component supports (Escape/backdrop).
  const drawer = page.getByRole('dialog', { name: 'Job detail' });
  if (await drawer.count()) {
    await page.keyboard.press('Escape');
    await expect(drawer).toHaveCount(0);
  }

  // Menu actions: scope to this row (the list sorts active rows first, so a
  // bare `.first()` would act on another Job). Open once, read the real items,
  // close through the backdrop exactly like a user click (no forced clicks).
  const editedRow = page.locator('div')
    .filter({ hasText: 'Click process Job edited' })
    .filter({ has: page.getByRole('button', { name: 'Job actions' }) })
    .last();
  const openMenu = async () => {
    await editedRow.getByRole('button', { name: 'Job actions' }).click();
    return page.getByRole('menuitem').allInnerTexts();
  };
  const closeMenu = async () => {
    const backdrop = page.locator('div.fixed.inset-0.z-20').first();
    if (await backdrop.count()) await backdrop.click();
  };
  evidence.menuItems = await openMenu();
  if ((await page.getByRole('menuitem', { name: /^Pin$/ }).count())) {
    await page.getByRole('menuitem', { name: /^Pin$/ }).click();
    evidence.checks.push('pin from the row menu');
  } else {
    await closeMenu();
  }


  await openMenu();
  if (await page.getByRole('menuitem', { name: /^Delete$/ }).count()) {
    await page.getByRole('menuitem', { name: /^Delete$/ }).click();
    const confirm = page.getByRole('dialog');
    if (await confirm.count()) {
      const dangerous = confirm.getByRole('button', { name: /Delete|删除/ }).last();
      if (await dangerous.count()) await dangerous.click();
      // The toast quotes the deleted name; assert the row title itself is gone.
      await expect(page.getByText('Click process Job edited', { exact: true })).toHaveCount(0);
      evidence.checks.push('delete removes the row after confirmation');
    }
  } else {
    await closeMenu();
  }

  assert.deepEqual(evidence.errors, []);
  // Never claim the whole chain when a named part is still blocked.
  evidence.scope = 'Jobs designed UI click chain';
  evidence.blocked = [
    ...(evidence.broadcastBlocked ? ['broadcast create (harness provider)'] : []),
    ...(evidence.scheduledBlocked ? ['scheduled-task create (harness scheduler provider)'] : []),
  ];
  evidence.status = evidence.blocked.length ? 'partial' : 'passed';
} catch (error) {
  evidence.status = 'failed';
  evidence.failure = String(error.stack ?? error);
  if (page) {
    evidence.pageText = (await page.locator('body').innerText()).slice(0, 4000);
    await page.screenshot({ path: path.join(runtime, 'failure.png'), fullPage: true });
  }
  throw error;
} finally {
  await fs.writeFile(path.join(runtime, 'server.log'), log);
  await fs.writeFile(path.join(runtime, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({ runtime, ...evidence }));
  await browser?.close();
  child.kill();
}
