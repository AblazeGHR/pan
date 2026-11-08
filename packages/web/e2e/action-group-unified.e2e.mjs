/**
 * Unified Action/Group directory: real production HTTP + production Chromium.
 *
 * `--phase=before` drives the baseline checkout (whole-page legacy toggle),
 * `--phase=after` drives this branch (one navigation, Groups included). Both
 * phases use the same owned fixture, the same random port and the same build
 * output rules; no product route or frontend mock is involved.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { chromium, expect } from '@playwright/test';
import { setTimeout as sleep } from 'node:timers/promises';

const phase = (process.argv.find((value) => value.startsWith('--phase=')) ?? '--phase=after').split('=')[1];
assert(['before', 'after'].includes(phase), 'phase must be before or after');

const here = path.resolve(import.meta.dirname, '../../..');
const sourceRoot = path.resolve(
  process.env.PAN_E2E_SOURCE_ROOT || (phase === 'before' ? 'D:/project/pan-worktrees/baseline-action-dir-20261108' : here),
);
const runtime = path.join(sourceRoot, '.tmp', `unified-${phase}-${Date.now()}`);
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
Object.assign(env, {
  PAN_E2E_RUNTIME: runtime,
  PAN_E2E_SOURCE_ROOT: sourceRoot,
  PAN_PORT: String(port),
  PYTHONPATH: sourceRoot,
});
const python = process.env.PAN_E2E_PYTHON || 'E:/software/miniforge/python.exe';
const child = spawn(python, [path.join(here, 'packages/web/e2e/action-group-unified-server.py')], {
  cwd: sourceRoot,
  env,
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
let log = '';
let browser;
let page;
const requests = [];
// The automation bearer token lives in memory only, so capture it from the
// browser's own authorized write to drive the outside-write negative control.
let automationToken = '';
const evidence = { phase, port, sourceRoot, checks: [], errors: [] };
child.stdout.on('data', (chunk) => {
  log += chunk;
});
child.stderr.on('data', (chunk) => {
  log += chunk;
});

const shot = (name) => page.screenshot({ path: path.join(runtime, `${phase}-${name}.png`), fullPage: true });
// Portaled dialogs are fixed to the viewport, so a stitched full-page capture
// would drop them; capture the dialog itself instead.
const dialogShot = (name) => page.screenshot({ path: path.join(runtime, `${phase}-${name}.png`) });

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
      if ((await fetch(base + '/__e2e/identity')).ok) {
        ready = true;
        break;
      }
    } catch {}
    await sleep(500);
  }
  if (!ready) throw Error('owned server did not become ready');
  const identity = await (await fetch(base + '/__e2e/identity')).json();
  assert.equal(identity.pid, child.pid);
  assert.equal(path.resolve(identity.runtime), runtime);
  assert.equal(path.resolve(identity.checkout), sourceRoot);
  evidence.checks.push('owned service PID, checkout and runtime verified');

  const key = (await fs.readFile(path.join(runtime, 'automation/user.key'), 'utf8')).trim();

  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', (error) => evidence.errors.push(error.message));
  page.on('request', (request) => {
    const authorization = request.headers().authorization ?? '';
    if (authorization.startsWith('Bearer ')) automationToken = authorization.slice(7);
    if (request.method() !== 'GET' && request.url().includes('/api/')) {
      requests.push({
        method: request.method(),
        url: request.url(),
        body: request.postDataJSON?.(),
      });
    }
  });

  await page.goto(base + '/react/actions');
  await page.getByLabel('Local user key').fill(key);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();

  if (phase === 'before') {
    await expect(page.getByRole('button', { name: '旧定义与 Session 绑定' })).toBeVisible();
    evidence.checks.push('baseline: canonical catalog and legacy records are split by a whole-page toggle');
    await expect(page.getByRole('button', { name: /^Groups/ })).toHaveCount(0);
    evidence.checks.push('baseline: the canonical surface has no Groups entry at all');
    await shot('actions-1280');
    await page.getByRole('button', { name: '旧定义与 Session 绑定' }).click();
    await page.getByRole('button', { name: /^Groups/ }).click();
    await expect(page.getByText('Adopted Group').first()).toBeVisible();
    evidence.checks.push('baseline: Groups are only reachable after switching the whole page to the old system');
    await shot('groups-1280');
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(base + '/react/actions');
      await page.getByRole('button', { name: '旧定义与 Session 绑定' }).click();
      await fits(`baseline legacy view ${width}`);
      await shot(`groups-${width}`);
    }
    evidence.status = 'passed';
  } else {
    const seed = JSON.parse(await fs.readFile(path.join(runtime, 'unified-seed.json'), 'utf8'));
    const sourcePath = path.join(runtime, 'cooperative-actions/registry.json');
    const sourceBytes = await fs.readFile(sourcePath);

    // One navigation for both kinds, no whole-page system switch.
    await expect(page.getByRole('button', { name: /^Actions/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /^Groups/ })).toBeVisible();
    await expect(page.getByRole('button', { name: '旧定义与 Session 绑定' })).toHaveCount(0);
    evidence.checks.push('single directory: Actions and Groups share one navigation, no page-level legacy toggle');

    // Per-row adoption state from both authorities.
    await expect(page.getByText('统一目录').first()).toBeVisible();
    // The adopted alias merges through the explicit projection only: exactly
    // one row for the logical Action, badged as adopted.
    const bindings = await page.request.get(`${base}/api/action-groups/legacy-action-bindings`);
    assert.equal(bindings.status(), 200);
    const bindingBody = await bindings.json();
    const adopted = bindingBody.items.find((item) => item.legacyId === 'old_alias');
    assert.ok(adopted, 'the adoption must publish an explicit legacy binding');
    assert.equal(adopted.deleted, false);
    assert.equal(Object.keys(adopted).sort().join(','), 'actionId,deleted,legacyId,sourceRoot');
    assert.equal(
      await page.getByRole('button', { name: 'Open Action Owned old series' }).count(),
      1,
      'the adopted alias must merge into one logical Action row',
    );
    await expect(page.getByText('已接管旧定义').first()).toBeVisible();
    evidence.checks.push('explicit binding projection merges the adopted alias into one row with no inferred pairing');
    await shot('actions-1280');
    await fits('action list 1280');

    // Creating a canonical Action draft stays reachable from the same list.
    await page.getByRole('button', { name: /新建 Action/ }).click();
    const newAction = page.getByRole('region', { name: 'New Action' });
    await expect(newAction.getByRole('button', { name: '新建统一 Action' })).toBeVisible();
    await newAction.getByRole('button', { name: '取消' }).click();
    await expect(page.getByRole('button', { name: 'Open Action Unified Echo' })).toBeVisible();
    evidence.checks.push('creating a canonical Action draft is still reachable from the unified list');

    // One logical Action entry with all fixed versions.
    await page.getByRole('button', { name: 'Open Action Unified Echo' }).click();
    const versionSelect = page.getByLabel('Action version');
    await expect(versionSelect).toBeVisible();
    const options = await versionSelect.locator('option').allInnerTexts();
    assert.equal(options.length, 2, `expected two aggregated versions, saw ${options.length}`);
    assert.ok(options[0].includes('v1') && options[1].includes('v2'));
    evidence.checks.push('one logical Action entry aggregates both published versions in the version selector');
    const postsBefore = requests.filter((item) => item.method !== 'GET').length;
    await versionSelect.selectOption('1');
    assert.equal(
      requests.filter((item) => item.method !== 'GET').length,
      postsBefore,
      'switching the viewing version must not write',
    );
    await expect(page.getByText(seed.fixed.digest).first()).toBeVisible();
    evidence.checks.push('switching the viewing version is read-only and shows the fixed ActionRef digest');
    await shot('action-1280');

    // Groups: fixed version, release, references and binding in one entry.
    await page.getByRole('button', { name: /^Groups/ }).click();
    await expect(page.getByRole('button', { name: 'Open Group Unified Tools' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open Group Adopted Group' })).toBeVisible();
    await expect(page.getByText('已接管旧定义').first()).toBeVisible();
    evidence.checks.push('canonical and adopted Groups are listed together with per-row adoption state');
    await page.getByRole('button', { name: 'Open Group Unified Tools' }).click();
    await expect(page.getByLabel('Group fixed version')).toHaveValue('1');
    await expect(page.getByText(seed.release).first()).toBeVisible();
    await expect(page.getByText('固定引用与显式升级')).toBeVisible();
    const history = page.getByRole('region', { name: 'Release history' });
    await expect(history).toBeVisible();
    await expect(history).toContainText(seed.release);
    await expect(history).toContainText(seed.firstRelease);
    await history.getByRole('button', { name: `查看 ${seed.firstRelease}` }).click();
    await expect(page.getByText(seed.firstDigest).first()).toBeVisible();
    evidence.checks.push('Group release history lists committed releases and opens an older immutable one');
    await expect(page.getByRole('button', { name: '预览此 Group 全部升级' })).toBeVisible();
    evidence.checks.push('Group entry exposes the fixed Action version, immutable release and the reference upgrade scope');
    await shot('group-1280');
    await fits('group detail 1280');

    // Session binding in one of the three MCP modes, with the observed CAS revision.
    await page.getByRole('button', { name: /在 Session 中使用/ }).click();
    await page.getByRole('radio', { name: /Alpha Session/ }).click();
    await expect(page.getByText('Plugin binding')).toBeVisible();
    await expect(page.getByText('Loading Session settings…')).toHaveCount(0);
    const modeCards = page.locator('[aria-label="Plugin mode"] label');
    assert.equal(await modeCards.count(), 3, 'the binding dialog must offer exactly three modes');
    const dialogBox = await page.getByRole('dialog').boundingBox();
    for (const mode of ['Standalone MCP', 'Pan native', 'Discover / call']) {
      const radio = page.getByRole('radio', { name: new RegExp(mode.replace('/', '\/')) });
      await expect(radio).toBeEnabled();
      await expect(radio).toBeVisible();
      const box = await radio.boundingBox();
      assert.ok(box && dialogBox, `${mode} must be laid out inside the dialog`);
      assert.ok(
        box.x + box.width <= dialogBox.x + dialogBox.width + 1 &&
          box.y + box.height <= dialogBox.y + dialogBox.height + 1,
        `${mode} is clipped outside the dialog`,
      );
    }
    evidence.modeCards = (await modeCards.allInnerTexts()).map((value) => value.split(/\r?\n/)[0]);
    evidence.checks.push('all three MCP modes are offered, visible and laid out inside the binding dialog');
    await page.getByRole('radio', { name: /Discover \/ call/ }).click();
    await dialogShot('session-dialog-1280');
    const patch = page.waitForResponse(
      (response) => response.url().includes('/api/sessions/') && response.request().method() === 'PATCH',
    );
    await page.getByRole('button', { name: 'Apply to Session' }).click();
    const applied = await patch;
    const appliedBody = applied.request().postDataJSON();
    const binding = (appliedBody.pluginBindings ?? []).find(
      (item) => item.plugin_id === `action-group-${seed.groupId.replaceAll('_', '-')}`,
    );
    assert.equal(binding?.mode, 'gateway');
    assert.equal(binding?.enabled, true);
    assert.equal(typeof appliedBody.expectedPluginRevision, 'number');
    evidence.checks.push('Session binding sends the plugin id, the chosen mode and the observed plugin CAS revision');
    await shot('session-1280');

    // Failure negative control: an outside write moves the revision, so the
    // browser's save must fail with a conflict instead of reporting success.
    assert.ok(automationToken, 'the browser must have sent an automation bearer token');
    const outside = await fetch(`${base}/api/action-groups/${seed.groupId}`, {
      method: 'PUT',
      headers: { Authorization: `Bearer ${automationToken}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        group: { id: seed.groupId, name: 'Unified Tools', tools: [{ alias: 'echo', actionRef: seed.fixed }] },
        expectedRevision: seed.groupRevision,
        requestKey: 'outside-write',
      }),
    });
    assert.ok(outside.ok, `outside write failed: ${outside.status}`);
    await page.getByLabel('Group 名称').fill('Renamed by browser');
    const putsBefore = requests.filter((item) => item.method === 'PUT').length;
    await page.getByRole('button', { name: '保存草稿' }).click();
    await expect(
      page.getByRole('alert').filter({ hasText: 'revision_conflict' }).first(),
    ).toBeVisible();
    assert.equal(requests.filter((item) => item.method === 'PUT').length, putsBefore + 1);
    await expect(page.getByText('回执 completed')).toHaveCount(0);
    evidence.checks.push('stale Group save fails with revision_conflict and never reports a completed receipt');

    // Narrow viewports: same flows, no horizontal overflow.
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(base + '/react/actions');
      // Wait for the real rows: a screenshot of the loading skeleton is not
      // evidence of the layout.
      await expect(page.getByRole('button', { name: 'Open Action Unified Echo' })).toBeVisible();
      await fits(`action list ${width}`);
      await shot(`actions-${width}`);
      await page.getByRole('button', { name: /^Groups/ }).click();
      await page.getByRole('button', { name: 'Open Group Unified Tools' }).click();
      await expect(page.getByLabel('Group 名称')).toBeVisible();
      await fits(`group detail ${width}`);
      await shot(`group-${width}`);
      await page.getByRole('button', { name: /在 Session 中使用/ }).click();
      await page.getByRole('radio', { name: /Alpha Session/ }).click();
      await fits(`binding dialog ${width}`);
      await dialogShot(`session-dialog-${width}`);
      await page.keyboard.press('Escape');
    }
    evidence.checks.push('production viewports 1280/390/320 render the same flows without horizontal overflow');

    assert.deepEqual(await fs.readFile(sourcePath), sourceBytes, 'the adopted legacy source must stay byte-identical');
    evidence.checks.push('adopted legacy source bytes are unchanged by the whole browser flow');
    assert.deepEqual(evidence.errors, []);
    evidence.status = 'passed';
  }
} catch (error) {
  evidence.status = 'failed';
  evidence.failure = String(error.stack ?? error);
  if (page) {
    evidence.pageText = await page.locator('body').innerText();
    await page.screenshot({ path: path.join(runtime, `${phase}-failure.png`) });
  }
  throw error;
} finally {
  await fs.writeFile(path.join(runtime, 'server.log'), log);
  await fs.writeFile(path.join(runtime, 'requests.json'), JSON.stringify(requests, null, 2));
  await fs.writeFile(path.join(runtime, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({ runtime, ...evidence }));
  await browser?.close();
  child.kill();
}
