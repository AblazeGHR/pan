import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { chromium, expect } from '@playwright/test';
import { setTimeout as sleep } from 'node:timers/promises';
const root = path.resolve(import.meta.dirname, '../../..');
const runtime = path.join(root, '.tmp', `p6-http-${Date.now()}`);
await fs.mkdir(runtime, { recursive: true });
const port = await new Promise(resolve => {
  const server = net.createServer();
  server.listen(0, '127.0.0.1', () => { const value = server.address().port; server.close(() => resolve(value)); });
});
assert(![8767, 8768].includes(port));
const base = `http://127.0.0.1:${port}`;
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith('PAN_')));
Object.assign(env, { PAN_E2E_RUNTIME: runtime, PAN_PORT: String(port), PYTHONPATH: root });
const child = spawn(process.env.PAN_E2E_PYTHON, [path.join(import.meta.dirname, 'action-p6-server.py')],
  { cwd: root, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
let log = '', browser, page;
child.stdout.on('data', chunk => { log += chunk; });
child.stderr.on('data', chunk => { log += chunk; });
const evidence = { port, checks: [], errors: [] };
try {
  for (let index = 0; index < 90; index++) {
    if (child.exitCode !== null) throw Error('owned server exited');
    try { if ((await fetch(base + '/__e2e/identity')).ok) break; } catch {}
    await sleep(500);
  }
  const identity = await (await fetch(base + '/__e2e/identity')).json();
  assert.equal(identity.pid, child.pid);
  assert.equal(path.resolve(identity.runtime), runtime);
  assert.equal(path.resolve(identity.checkout), root);
  evidence.checks.push('owned service PID, checkout and runtime verified');
  const sourcePath = path.join(runtime, 'cooperative-actions/registry.json');
  const sourceBytes = await fs.readFile(sourcePath);
  const seed = JSON.parse(await fs.readFile(path.join(runtime, 'p6-seed.json'), 'utf8'));
  const key = (await fs.readFile(path.join(runtime, 'automation/user.key'), 'utf8')).trim();
  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => evidence.errors.push(error.message));
  await page.goto(base + '/react/actions');
  await page.getByLabel('Local user key').fill(key);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await page.getByRole('button', { name: '旧定义与 Session 绑定' }).click();
  await page.getByRole('button', { name: 'Edit Owned old series', exact: true }).click();
  await page.getByRole('button', { name: '编辑为新草稿版本' }).click();
  const text = page.getByRole('textbox', { name: 'Action definition' });
  const definition = JSON.parse(await text.inputValue());
  assert.equal(definition.execution.config.commands.length, 2);
  assert.equal(definition.execution.config.commands[1].timeout, 19);
  evidence.checks.push('old Edit opens exact adopted canonical serial definition');
  definition.name = 'Owned edited series';
  await text.fill(JSON.stringify(definition, null, 2));
  await page.getByRole('button', { name: '返回旧目录' }).click();
  await expect(page.getByRole('dialog')).toContainText('Discard unsaved changes?');
  await page.getByRole('button', { name: 'Keep editing' }).click();
  const response = page.waitForResponse(r => r.url().endsWith('/api/jobs/actions/p6_original') && r.request().method() === 'PUT');
  const refreshed = page.waitForResponse(r => r.url().endsWith('/api/actions') && r.request().method() === 'GET');
  await page.getByRole('button', { name: '保存草稿版本' }).click();
  const saved = await response;
  assert.equal(saved.status(), 200);
  assert.equal(saved.request().postDataJSON().expectedRevision, 2);
  assert.deepEqual(saved.request().postDataJSON().definition, definition);
  const registry = await (await refreshed).json();
  assert.equal(registry.canonicalActions.old_alias.object.state, 'draft');
  assert.equal(registry.canonicalActions.old_alias.object.revision, 3);
  assert.equal(registry.actions[0].enabled, false);
  assert.equal(registry.groups[0].build.version, seed.groupRelease);
  assert.deepEqual(await fs.readFile(sourcePath), sourceBytes);
  evidence.checks.push('old source bytes and Group release unchanged; current draft revision/lifecycle projected accurately');
  await expect(page.getByRole('button', { name: 'Edit Owned edited series', exact: true })).toBeVisible();
  evidence.checks.push('production save uses original logical id/CAS and preserves both descriptors; no legacy write');
  assert.equal(await fs.access(path.join(runtime, 'effects')).then(() => true, () => false), false);
  evidence.checks.push('edit/save caused no command effect');
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.getByRole('button', { name: 'Owned edited series', exact: true }).click();
    await page.getByRole('button', { name: '编辑为新草稿版本' }).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: path.join(runtime, `mapped-${width}.png`), fullPage: true });
    await page.getByRole('button', { name: '返回旧目录' }).click();
    await page.getByRole('button', { name: 'Discard draft' }).click();
    evidence.checks.push(`production viewport ${width}: full editor, no horizontal overflow`);
  }
  assert.deepEqual(evidence.errors, []);
  evidence.status = 'passed';
} catch (error) {
  evidence.status = 'failed'; evidence.failure = String(error.stack ?? error);
  if (page) { evidence.pageText = await page.locator('body').innerText(); await page.screenshot({ path: path.join(runtime, 'failure.png') }); }
  throw error;
} finally {
  await fs.writeFile(path.join(runtime, 'server.log'), log);
  await fs.writeFile(path.join(runtime, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({ runtime, ...evidence }));
  await browser?.close(); child.kill();
}
