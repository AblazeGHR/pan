import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const directory = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(directory, '../../../..');
const require = createRequire(path.join(repo, 'packages/web/package.json'));
const { chromium } = require('@playwright/test');
const output = path.join(directory, 'archive-scope');
await mkdir(output, { recursive: true });
const reserve = net.createServer();
await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
const port = reserve.address().port;
await new Promise(resolve => reserve.close(resolve));
const root = await mkdtemp(path.join(tmpdir(), 'pan-terminal-archive-browser-'));
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith('PAN_')));
const child = spawn(process.argv[2] || 'python', [path.join(directory, 'harness.py'), '--root', root, '--port', String(port)],
  { cwd: repo, env: { ...env, PYTHONPATH: repo }, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
let logs = '', exited = false;
child.stdout.on('data', chunk => { logs += chunk; });
child.stderr.on('data', chunk => { logs += chunk; });
const exit = new Promise(resolve => child.on('exit', code => { exited = true; resolve(code); }));
let browser;
const report = { realTerminal: true, errors: [] };
try {
  for (let i = 0; i < 100; i++) {
    if (exited) throw new Error(`harness exited: ${logs}`);
    try { if ((await fetch(`http://127.0.0.1:${port}/react/terminals`)).ok) break; } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 820 } });
  await context.route(url => url.pathname.startsWith('/api/') && !url.pathname.startsWith('/api/terminals'), route => {
    const url = new URL(route.request().url());
    return route.fulfill({ json: url.pathname === '/api/sessions' ? { sessions: [] } :
      url.pathname === '/api/workspaces' ? { workspaces: [] } :
      url.pathname === '/api/cli/status' ? { adapters: [], hasAvailable: true } : {} });
  });
  const page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  await page.goto(`http://127.0.0.1:${port}/react/terminals`);
  await page.getByLabel('关联工作区 ID').fill('ws_browser');
  await page.getByRole('button', { name: '新建终端', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[aria-label="选择终端"]')?.value.startsWith('term_'), null, { timeout: 30000 });
  const id = await page.getByLabel('选择终端').inputValue();
  await page.getByRole('button', { name: '归档终端', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[aria-label="选择终端"]')?.value === '');
  const view = await page.evaluate(async id => (await fetch(`/api/terminals/${id}`)).json(), id);
  assert.equal(view.result.status, 'running');
  assert.equal(view.result.archived, true);
  await page.getByLabel('已归档').check();
  await page.getByLabel('选择终端').selectOption(id);
  await page.waitForFunction(() => document.querySelector('[role="status"]')?.textContent.includes('部分屏幕恢复'), null, { timeout: 30000 });
  await page.screenshot({ path: path.join(output, 'screen.png') });
  await page.getByRole('button', { name: '恢复到列表', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[aria-label="选择终端"]')?.value === '');
  await page.getByLabel('已归档').uncheck();
  await page.getByLabel('选择终端').selectOption(id);
  await page.getByLabel('关联工作区 ID').fill('ws_manual');
  await page.getByRole('button', { name: '应用绑定', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[aria-label="选择终端"]')?.selectedOptions[0]?.textContent.includes('ws_manual'));
  const rebound = await page.evaluate(async id => (await fetch(`/api/terminals/${id}`)).json(), id);
  assert.equal(rebound.result.scope.workspace_id, 'ws_manual');
  assert.equal(rebound.result.scope.session_id, null);
  for (let attempt = 0; attempt < 10; attempt++) {
    await page.getByRole('button', { name: '删除终端', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('[aria-label="选择终端"]')?.value ||
      (document.querySelector('[role="alert"]') && [...document.querySelectorAll('button')].some(button => button.textContent === '删除终端' && !button.disabled)), null, { timeout: 15000 });
    if (!await page.getByLabel('选择终端').inputValue()) break;
    await page.waitForTimeout(300);
  }
  assert.equal(await page.getByLabel('选择终端').inputValue(), '');
  const remaining = await page.evaluate(async () => (await fetch('/api/terminals')).json());
  assert.deepEqual(remaining.result, []);
  assert.deepEqual(report.errors, []);
  Object.assign(report, { archiveDoesNotStop: true, restore: true, manualWorkspace: true, closeAndRemove: true });
  await writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally {
  await browser?.close();
  if (!exited) child.stdin.write('\n');
  const code = await Promise.race([exit, new Promise(resolve => setTimeout(() => resolve('timeout'), 30000))]);
  if (code !== 0) throw new Error(`harness did not shut down cleanly: ${code}`);
  await rm(root, { recursive: true });
}
