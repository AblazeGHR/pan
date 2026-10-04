import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const directory = path.dirname(fileURLToPath(import.meta.url));
const label = process.argv[3];
if (label && !/^[a-z0-9-]{1,60}$/.test(label)) throw new Error('invalid evidence label');
const output = label ? path.join(directory, label) : directory;
await mkdir(output, { recursive: true });
const repo = path.resolve(directory, '../../../..');
const require = createRequire(path.join(repo, 'packages/web/package.json'));
const { chromium } = require('@playwright/test');
const reserve = net.createServer();
await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
const port = reserve.address().port;
await new Promise(resolve => reserve.close(resolve));
const root = await mkdtemp(path.join(tmpdir(), 'pan-terminal-browser-'));
const child = spawn(process.argv[2] || 'python', [path.join(directory, 'harness.py'), '--root', root, '--port', String(port)],
  { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
let logs = '', exited = false;
child.stdout.on('data', chunk => { logs += chunk; });
child.stderr.on('data', chunk => { logs += chunk; });
const exit = new Promise(resolve => child.on('exit', code => { exited = true; resolve(code); }));
let browser;
let page;
const report = { browser: 'chromium', realConPTY: true, events: [], errors: [] };
try {
  for (let i = 0; i < 100; i++) {
    if (exited) throw new Error(`harness exited: ${logs}`);
    try { if ((await fetch(`http://127.0.0.1:${port}/react/terminals`)).ok) break; } catch { /* startup */ }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 820 } });
  page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  // Only unrelated Dashboard endpoints are mocked; product traffic is untouched.
  await context.route(url => url.pathname.startsWith('/api/') && !url.pathname.startsWith('/api/terminals'), route => {
    const url = new URL(route.request().url());
    return route.fulfill({ json: url.pathname === '/api/sessions' ? { sessions: [] } :
      url.pathname === '/api/workspaces' ? { workspaces: [] } :
      url.pathname === '/api/cli/status' ? { adapters: [], hasAvailable: true } : {} });
  });
  page.on('websocket', socket => {
    if (!socket.url().includes('/ws/terminal/')) return;
    socket.on('framereceived', frame => {
      try { report.events.push(JSON.parse(String(frame.payload))); } catch { /* not JSON */ }
    });
  });
  await page.goto(`http://127.0.0.1:${port}/react/terminals`);
  await page.getByLabel('关联工作区 ID').fill('browser-workspace-metadata');
  await page.getByLabel('关联 Session ID').fill('browser-session-metadata');
  await page.getByRole('button', { name: '新建终端', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[aria-label="选择终端"]')?.value.startsWith('term_'), null, { timeout: 30000 });
  const id = await page.getByLabel('选择终端').inputValue();
  await page.waitForFunction(() => document.querySelector('[role="status"]')?.textContent.includes('部分屏幕恢复'), null, { timeout: 30000 });
  await page.getByRole('button', { name: '取得输入控制权', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[role="status"]')?.textContent.startsWith('控制模式'));
  await page.locator('.xterm-helper-textarea').focus();
  await page.keyboard.insertText('echo 中文_PAN_BROWSER_REAL');
  await page.keyboard.press('Enter');
  for (let i = 0; i < 100 && !report.events.some(event => event.type === 'output' && Buffer.from(event.data_b64, 'base64').includes('PAN_BROWSER_REAL')); i++) {
    await page.waitForTimeout(100);
  }
  assert(report.events.some(event => event.type === 'input-result' && event.accepted === true));
  assert(report.events.some(event => event.type === 'output' && Buffer.from(event.data_b64, 'base64').includes('PAN_BROWSER_REAL')));
  // True Windows control event, not an echoed byte or a released console read.
  // The foreground child deliberately does not reset its inherited ignore flag.
  const foreground = path.join(root, 'foreground.py');
  const ready = path.join(root, 'foreground.ready');
  const signal = path.join(root, 'foreground.signal');
  const shellAfter = path.join(root, 'shell-after.txt');
  await writeFile(foreground, `import ctypes,sys,time\nfrom pathlib import Path\nk=ctypes.WinDLL('kernel32',use_last_error=True)\nH=ctypes.WINFUNCTYPE(ctypes.c_int,ctypes.c_uint)\nstopped=False\ndef handler(event):\n global stopped\n Path(sys.argv[2]).write_text(str(event))\n stopped=True\n return 1\nh=H(handler)\nassert k.SetConsoleCtrlHandler(h,True)\nPath(sys.argv[1]).write_text('ready')\nwhile not stopped: time.sleep(0.05)\n`);
  const command = [process.argv[2] || 'python', foreground, ready, signal].map(value => `"${value}"`).join(' ');
  await page.keyboard.insertText(command);
  await page.keyboard.press('Enter');
  async function waitFile(filename, expected) {
    for (let attempt = 0; attempt < 80; attempt++) {
      try { if ((await readFile(filename, 'utf8')).trim() === expected) return; } catch { /* not yet */ }
      await page.waitForTimeout(50);
    }
    throw new Error(`missing foreground witness: ${path.basename(filename)}`);
  }
  await waitFile(ready, 'ready');
  await page.keyboard.press('Control+c');
  await waitFile(signal, '0');
  await page.keyboard.insertText(`echo SHELL_STILL_ALIVE>"${shellAfter}"`);
  await page.keyboard.press('Enter');
  await waitFile(shellAfter, 'SHELL_STILL_ALIVE');
  report.osCtrlCEventVerified = true;
  report.foregroundInterruptedShellPreserved = true;
  await page.setViewportSize({ width: 1100, height: 760 });
  await page.waitForTimeout(300);
  assert(report.events.some(event => event.type === 'resize-result'));
  // A second real browser connection can take control. The old connection
  // must lose its local control state when attempting a stale write.
  const second = await page.context().newPage();
  await second.goto(`http://127.0.0.1:${port}/react/terminals`);
  await second.getByLabel('选择终端').selectOption(id);
  await second.waitForFunction(() => document.querySelector('[role="status"]')?.textContent.includes('部分屏幕恢复'));
  await second.getByRole('button', { name: '取得输入控制权', exact: true }).click();
  await second.waitForFunction(() => document.querySelector('[role="status"]')?.textContent.startsWith('控制模式'));
  await page.locator('.xterm-helper-textarea').focus();
  await page.keyboard.insertText('OLD_OWNER_MUST_NOT_WRITE');
  await page.waitForFunction(() => document.querySelector('[role="status"]')?.textContent.startsWith('只观察'));
  assert(report.events.some(event => event.type === 'error' && event.code === 'stale-generation'));
  await second.close(); // disconnect releases the connection, not the PTY
  const view = await page.evaluate(async terminalId => {
    const response = await fetch(`/api/terminals/${terminalId}`);
    return response.json();
  }, id);
  assert.equal(view.ok, true);
  assert.equal(view.result.status, 'running');
  assert.equal(view.result.scope.workspace_id, 'browser-workspace-metadata');
  assert.equal(view.result.scope.session_id, 'browser-session-metadata');
  report.optionalMetadataVerified = true;
  report.twoConnectionTakeover = true;
  report.disconnectPreservedRuntime = true;
  report.resizeConfirmed = true;
  await page.screenshot({ path: path.join(output, 'screen.png') });
  await page.reload();
  await page.getByLabel('选择终端').selectOption(id);
  await page.waitForFunction(() => document.querySelector('[role="status"]')?.textContent.includes('部分屏幕恢复'), null, { timeout: 30000 });
  assert((await page.getByRole('status').textContent()).startsWith('只观察'));
  for (let attempt = 0; attempt < 8; attempt++) {
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: '终止终端', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[aria-label="选择终端"]')?.value === '' ||
      (document.querySelector('[role="alert"]')?.textContent.includes('cleanup-unconfirmed') &&
       [...document.querySelectorAll('button')].some(button => button.textContent === '终止终端' && !button.disabled)),
      null, { timeout: 15000 });
    if ((await page.getByLabel('选择终端').inputValue()) === '') break;
    await page.waitForTimeout(300);
  }
  await page.waitForFunction(() => document.querySelector('[aria-label="选择终端"]')?.value === '', null, { timeout: 30000 });
  assert.deepEqual(report.errors, []);
  report.terminalId = id;
  report.passed = true;
} finally {
  if (!report.passed && page) {
    report.pageText = await page.locator('body').innerText();
    await page.screenshot({ path: path.join(output, 'failure.png') });
  }
  await browser?.close();
  child.stdin.write('stop\n');
  let exitTimer;
  const code = await Promise.race([exit, new Promise(resolve => { exitTimer = setTimeout(() => resolve('timeout'), 30000); })]);
  clearTimeout(exitTimer);
  report.harnessExit = code;
  report.logs = logs;
  await writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2));
  if (code !== 'timeout') await rm(root, { recursive: true, force: true });
  else throw new Error(`harness retained for diagnosis: ${root}`);
}
console.log(JSON.stringify({ passed: report.passed, harnessExit: report.harnessExit, events: report.events.length }));
