// Real React/ConPTY tail + nonzero shell exit; no provider executable.
import { createRequire } from 'node:module';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const directory = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(directory, '../../../..');
const label = process.argv[3];
if (!label || !/^[a-z0-9-]{1,60}$/.test(label)) throw new Error('new evidence label required');
const output = path.join(directory, label);
await mkdir(output);
const require = createRequire(path.join(repo, 'packages/web/package.json'));
const { chromium } = require('@playwright/test');
const reserve = net.createServer();
await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
const port = reserve.address().port;
await new Promise(resolve => reserve.close(resolve));
assert.notEqual(port, 8768);
const root = await mkdtemp(path.join(tmpdir(), 'pan-natural-exit-'));
const child = spawn(process.argv[2] || 'python', [path.join(directory, 'harness.py'), '--root', root, '--port', String(port)],
  { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
let logs = '';
child.stdout.on('data', chunk => { logs += chunk; });
child.stderr.on('data', chunk => { logs += chunk; });
const exit = new Promise(resolve => child.once('exit', resolve));
const origin = `http://127.0.0.1:${port}`;
const report = { passed: false, browser: 'chromium', realConPTY: true, frames: [], websocketConnections: 0 };
let browser, page;
try {
  for (let attempt = 0; attempt < 150; attempt++) {
    try { if ((await fetch(`${origin}/react/terminals`)).ok) break; } catch { /* starting */ }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  await context.route(url => url.pathname.startsWith('/api/') && !url.pathname.startsWith('/api/terminals'), route => {
    const url = new URL(route.request().url());
    return route.fulfill({ json: url.pathname === '/api/sessions' ? { sessions: [] } :
      url.pathname === '/api/workspaces' ? { workspaces: [] } :
      url.pathname === '/api/cli/status' ? { adapters: [], hasAvailable: true } : {} });
  });
  page = await context.newPage();
  page.on('websocket', socket => {
    if (!socket.url().includes('/ws/terminal/')) return;
    report.websocketConnections++;
    socket.on('framereceived', frame => report.frames.push(JSON.parse(String(frame.payload))));
  });
  await page.goto(`${origin}/react/terminals`);
  await page.getByRole('button', { name: '新建终端', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[aria-label="选择终端"]')?.value.startsWith('term_'), null, { timeout: 30000 });
  const id = await page.getByLabel('选择终端').inputValue();
  await page.waitForFunction(() => document.querySelector('[role="status"]')?.textContent.includes('部分屏幕恢复'));
  await page.getByRole('button', { name: '取得输入控制权', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[role="status"]')?.textContent.startsWith('控制模式'));
  await page.locator('.xterm-helper-textarea').focus();
  await page.keyboard.insertText('echo NATIVE_FINAL_中文&exit 7');
  await page.keyboard.press('Enter');
  for (let attempt = 0; attempt < 200; attempt++) {
    if (report.frames.some(frame => frame.type === 'terminal-state')) break;
    await page.waitForTimeout(100);
  }
  report.statusText = await page.getByRole('status').textContent();
  report.screenText = await page.locator('.xterm-rows').innerText();
  report.record = await page.evaluate(async id => (await (await fetch(`/api/terminals/${id}`)).json()).result, id);
  if (report.record.status === 'running') {
    const script = "import json,sys\nfrom packages.core.terminal.runner_client import RunnerClient\nc=RunnerClient(sys.argv[1],data_root=sys.argv[2],client_id='owned-natural-diagnostic',connect_timeout=3,request_timeout_ms=2000)\ntry:\n c.attach();d=c.describe();print(json.dumps({k:d.get(k) for k in ('pid','process_created_at_filetime','exit','runner_state')}))\nfinally: c.release_connection()";
    const result = await promisify(execFile)(process.argv[2] || 'python', ['-c', script, id, root],
      { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, windowsHide: true });
    report.runnerFacts = JSON.parse(result.stdout);
  }
  await page.screenshot({ path: path.join(output, 'natural-exit.png') });
  assert(report.frames.some(frame => frame.type === 'output' && Buffer.from(frame.data_b64, 'base64').includes('NATIVE_FINAL_')));
  assert(report.screenText.includes('NATIVE_FINAL_中文'));
  assert.equal(report.record.status, 'exited');
  assert.equal(report.record.exit.code, 7);
  await page.getByRole('option', { name: `${id} · exited`, exact: true }).waitFor({ state: 'attached' });
  report.selectedLabel = await page.getByRole('option', { name: `${id} · exited`, exact: true }).textContent();
  const frontier = report.frames.filter(frame => frame.type === 'output').at(-1).next_seq;
  report.finalRead = await page.evaluate(async ({ id, frontier }) =>
    (await (await fetch(`/api/terminals/${id}/read?cursor=${frontier}`)).json()).result, { id, frontier });
  assert.equal(report.finalRead.status, 'exited');
  assert.equal(report.finalRead.exit_code, 7);
  assert.equal(report.finalRead.output_complete, false);
  assert(report.frames.some(frame => frame.type === 'terminal-state' && frame.status === 'exited'));
  assert(report.statusText.includes('终端状态：exited'), report.statusText);
  assert(await page.getByRole('button', { name: '取得输入控制权', exact: true }).isDisabled());
  const priorConnections = report.websocketConnections;
  await page.getByLabel('选择终端').selectOption('');
  await page.getByLabel('选择终端').selectOption(id);
  await page.waitForFunction(() => document.querySelector('[role="status"]')?.textContent.includes('历史屏幕未持久化'));
  report.reselectedStatus = await page.getByRole('status').textContent();
  assert.equal(report.websocketConnections, priorConnections);
  assert(await page.getByRole('button', { name: '重连', exact: true }).isDisabled());
  await page.screenshot({ path: path.join(output, 'ended-record.png') });
  report.passed = true;
} catch (error) {
  report.error = String(error);
  process.exitCode = 1;
} finally {
  await browser?.close();
  child.stdin.end('\n');
  let timer;
  report.harnessExit = await Promise.race([exit, new Promise(resolve => { timer = setTimeout(() => resolve('timeout'), 30000); })]);
  clearTimeout(timer);
  if (report.harnessExit !== 0) { report.passed = false; report.retainedRoot = root; process.exitCode = 1; }
  report.logs = logs;
  await writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2));
  if (report.harnessExit === 0) await rm(root, { recursive: true });
  console.log(JSON.stringify({ passed: report.passed, harnessExit: report.harnessExit, error: report.error }));
}
