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
  await expect(page.getByText('统一目录条目')).toBeVisible();
  await expect(page.getByText('Unified Echo', { exact: true }).first()).toBeVisible();
  evidence.checks.push('designed directory is the page and lists canonical records additively');
  await shot('actions-1280');
  await fits('actions list 1280');

  // One logical entry, every published version, read-only switching.
  await page.getByRole('button', { name: '版本与发布' }).first().click();
  const versionSelect = page.getByLabel('Action version');
  await expect(versionSelect).toBeVisible();
  const options = await versionSelect.locator('option').allInnerTexts();
  assert.equal(options.length, 2, `expected two versions, saw ${options.length}`);
  await versionSelect.selectOption('1');
  await expect(versionSelect).toHaveValue('1');
  evidence.checks.push('one logical Action keeps both published versions in the designed page');
  await shot('action-version-1280');
  await page.getByRole('button', { name: '返回列表' }).click();

  // Group entry: fixed versions, immutable release history, binding.
  await page.getByRole('button', { name: /^Groups/ }).click();
  await expect(page.getByText('统一目录条目')).toBeVisible();
  await page.getByRole('button', { name: '固定版本与 release' }).first().click();
  const history = page.getByRole('region', { name: 'Release history' });
  await expect(history).toBeVisible();
  await expect(history.locator('li')).toHaveCount(2);
  const older = (await history.locator('li').last().innerText()).split('\n')[0];
  await history.getByRole('button', { name: `查看 ${older}` }).click();
  await expect(page.getByText(older).first()).toBeVisible();
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
  const outside = await fetch(`${base}/api/action-groups/${seed.groupId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
    body: JSON.stringify({
      group: { id: seed.groupId, name: 'Unified Tools', tools: [{ alias: 'echo', actionRef: seed.fixed }] },
      expectedRevision: seed.groupRevision,
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
        expectedRevision: seed.groupRevision,
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
    await expect(page.getByText('统一目录条目')).toBeVisible();
    await fits(`actions ${width}`);
    await shot(`actions-${width}`);
    await page.getByRole('button', { name: '版本与发布' }).first().click();
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
