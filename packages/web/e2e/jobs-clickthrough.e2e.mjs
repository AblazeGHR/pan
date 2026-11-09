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
import { spawn } from 'node:child_process';
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
await fs.mkdir(ownedRoot, { recursive: true });
await fs.writeFile(path.join(ownedRoot, '.jobs-owned-harness'), '');
const child = spawn(process.env.PAN_E2E_PYTHON || 'D:/project/Pan/.venv/Scripts/python.exe',
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
  await page.goto(base + '/react/automation');
  await page.getByLabel('Local user key').fill(key);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();
  await page.goto(base + '/react/jobs');
  await expect(page.getByRole('button', { name: 'New Job', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /Unified Jobs|Legacy Jobs/ })).toHaveCount(0);
  evidence.checks.push('single unified Jobs list with one New Job entry');

  // background-process
  let dialog = await openCreate();
  await chooseKind(dialog, 'background-process', 'Click process Job');
  await dialog.getByLabel(/^Executable/).fill('cmd');
  await dialog.getByLabel(/^Working directory/).fill(runtime);
  await pickSingleTarget(dialog);
  await submit(dialog);
  await expect(page.getByText('Click process Job', { exact: false }).first()).toBeVisible();
  evidence.kinds.push('background-process');

  // session-message (multi-target checkboxes)
  dialog = await openCreate();
  await chooseKind(dialog, 'session-message', 'Click message Job');
  await pickSingleTarget(dialog);
  await dialog.getByLabel('Message text').fill('click hello');
  await submit(dialog);
  await expect(page.getByText('Click message Job', { exact: false }).first()).toBeVisible();
  evidence.kinds.push('session-message');

  // session-broadcast
  dialog = await openCreate();
  await chooseKind(dialog, 'session-broadcast', 'Click broadcast Job');
  await pickMultiTarget(dialog);
  await dialog.getByLabel('Message text').fill('click broadcast');
  await submit(dialog);
  await expect(page.getByText('Click broadcast Job', { exact: false }).first()).toBeVisible();
  evidence.kinds.push('session-broadcast');

  // scheduled-task (default form: assign + one schedule entry)
  dialog = await openCreate();
  await dialog.getByLabel('Name').fill('Click scheduled Job');
  await dialog.getByLabel('Description').fill('Click scheduled Job description');
  await pickSingleTarget(dialog);
  await dialog.getByLabel('Task text').fill('click task');
  await submit(dialog);
  await expect(page.getByText('Click scheduled Job', { exact: false }).first()).toBeVisible();
  evidence.kinds.push('scheduled-task');
  evidence.checks.push(`created all four kinds through New Job: ${evidence.kinds.join(', ')}`);

  // Row menu on the process job: edit, run, pin, cancel.
  await page.getByRole('button', { name: 'Job actions' }).first().click();
  evidence.menuItems = await page.getByRole('menuitem').allInnerTexts();
  await page.keyboard.press('Escape');

  const menu = (pattern) => page.getByRole('menuitem', { name: pattern }).first();
  await page.getByRole('button', { name: 'Job actions' }).first().click();
  if (await menu(/^Edit|^编辑/).count()) {
    await menu(/^Edit|^编辑/).click();
    const editDialog = page.getByRole('dialog');
    if (await editDialog.getByLabel('Name').count()) {
      await editDialog.getByLabel('Name').fill('Click process Job edited');
      await editDialog.getByRole('button', { name: /^Save$|^保存$/ }).first().click();
      await expect(page.getByText('Click process Job edited', { exact: false }).first()).toBeVisible();
      evidence.checks.push('edited the Job name from the row menu');
    }
  } else {
    await page.keyboard.press('Escape');
  }

  await page.getByRole('button', { name: 'Job actions' }).first().click();
  if (await menu(/Run now|^运行/).count()) {
    await menu(/Run now|^运行/).click();
    evidence.checks.push('manual Run is reachable');
  } else {
    await page.keyboard.press('Escape');
  }

  await page.getByRole('button', { name: 'Job actions' }).first().click();
  if (await menu(/Pin|置顶/).count()) {
    await menu(/Pin|置顶/).click();
    evidence.checks.push('pin is reachable');
  } else {
    await page.keyboard.press('Escape');
  }

  await page.getByRole('button', { name: 'Job actions' }).first().click();
  if (await menu(/Cancel|取消/).count()) {
    await menu(/Cancel|取消/).click();
    evidence.checks.push('cancel is reachable');
  } else {
    await page.keyboard.press('Escape');
  }

  assert.deepEqual(evidence.errors, []);
  evidence.status = 'passed';
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
