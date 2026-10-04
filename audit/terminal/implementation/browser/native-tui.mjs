// Real browser -> xterm -> WS -> ConPTY -> native console input witness.
// No provider, external service, or borrowed console settings are touched.
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
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
await mkdir(output); // Do not overwrite a previous run.
const require = createRequire(path.join(repo, 'packages/web/package.json'));
const { chromium } = require('@playwright/test');
const reserve = net.createServer();
await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
const port = reserve.address().port;
await new Promise(resolve => reserve.close(resolve));
const root = await mkdtemp(path.join(tmpdir(), 'pan-native-tui-'));
const python = process.argv[2] || 'python';
const child = spawn(python, [path.join(directory, 'harness.py'), '--root', root, '--port', String(port)],
  { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
let logs = '';
child.stdout.on('data', chunk => { logs += chunk; });
child.stderr.on('data', chunk => { logs += chunk; });
const exit = new Promise(resolve => child.once('exit', resolve));
const report = { realConPTY: true, browser: 'chromium', passed: false, errors: [] };
let browser, page;
try {
  for (let attempt = 0; attempt < 150; attempt++) {
    try { if ((await fetch(`http://127.0.0.1:${port}/react/terminals`)).ok) break; } catch { /* starting */ }
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
  page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  await page.goto(`http://127.0.0.1:${port}/react/terminals`);
  await page.getByRole('button', { name: '新建终端', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[aria-label="选择终端"]')?.value.startsWith('term_'), null, { timeout: 30000 });
  const id = await page.getByLabel('选择终端').inputValue();
  await page.waitForFunction(() => document.querySelector('[role="status"]')?.textContent.includes('部分屏幕恢复'), null, { timeout: 30000 });
  await page.getByRole('button', { name: '取得输入控制权', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[role="status"]')?.textContent.startsWith('控制模式'));
  const script = path.join(root, 'native.py');
  const witness = path.join(root, 'keys.json');
  await writeFile(script, `import ctypes,json,msvcrt,os,sys,time\nfrom pathlib import Path\nk=ctypes.WinDLL('kernel32',use_last_error=True)\nk.GetStdHandle.restype=ctypes.c_void_p\nout=k.GetStdHandle(-11)\nmode=ctypes.c_uint()\nassert k.GetConsoleMode(ctypes.c_void_p(out),ctypes.byref(mode))\nassert k.SetConsoleMode(ctypes.c_void_p(out),mode.value|4)\nkeys=[]\ninitial=list(os.get_terminal_size())\ntry:\n sys.stdout.write('\\x1b[?1049h\\x1b[2J\\x1b[H\\x1b[32mNATIVE_TUI_READY\\x1b[0m');sys.stdout.flush()\n deadline=time.monotonic()+30\n while time.monotonic()<deadline:\n  if not msvcrt.kbhit(): time.sleep(0.01);continue\n  value=msvcrt.getwch();keys.append(ord(value))\n  if value=='\\r': break\n final=list(os.get_terminal_size())\n Path(sys.argv[1]).write_text(json.dumps({'keys':keys,'initial_size':initial,'final_size':final}))\nfinally:\n sys.stdout.write('\\x1b[?1049l\\r\\nNATIVE_TUI_RETURNED\\r\\n');sys.stdout.flush()\n k.SetConsoleMode(ctypes.c_void_p(out),mode.value)\n`);
  await page.locator('.xterm-helper-textarea').focus();
  await page.keyboard.insertText([python, script, witness].map(value => `"${value}"`).join(' '));
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('.xterm-rows')?.textContent.includes('NATIVE_TUI_READY'), null, { timeout: 15000 });
  await page.screenshot({ path: path.join(output, 'alternate-screen.png') });
  await page.setViewportSize({ width: 960, height: 680 });
  await page.waitForTimeout(500);
  await page.locator('.xterm-helper-textarea').focus();
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('Tab');
  await page.keyboard.insertText('中文');
  await page.keyboard.press('Enter');
  for (let attempt = 0; attempt < 200; attempt++) {
    try { report.native = JSON.parse(await readFile(witness, 'utf8')); break; } catch { /* child still consuming */ }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.deepEqual(report.native?.keys, [224, 75, 9, 20013, 25991, 13]);
  assert.notDeepEqual(report.native.initial_size, report.native.final_size);
  await page.waitForFunction(() => document.querySelector('.xterm-rows')?.textContent.includes('NATIVE_TUI_RETURNED'), null, { timeout: 15000 });
  await page.screenshot({ path: path.join(output, 'primary-screen-return.png') });
  report.nativeConsoleKeyboardVerified = true;
  report.alternateScreenAndReturnVerified = true;
  report.resizeReachedNativeConsole = true;
  // Exercise the installed real pager, separately from our native key witness.
  // Its path is supplied explicitly; absence is not silently treated as a pass.
  const pager = process.argv[4];
  if (!pager) throw new Error('explicit less.exe path required');
  const text = path.join(root, 'pager.txt');
  await writeFile(text, Array.from({ length: 500 }, (_, i) => `LESS_REAL_LINE_${String(i).padStart(4, '0')}`).join('\n') + '\n');
  // Explicit environment control: distinguish pager TERM detection from PTY IO.
  await page.keyboard.insertText(`set TERM=xterm-256color&&"${pager}" "${text}"`);
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('.xterm-rows')?.textContent.includes('LESS_REAL_LINE_0000'), null, { timeout: 15000 });
  await page.keyboard.press('G');
  await page.waitForFunction(() => document.querySelector('.xterm-rows')?.textContent.includes('LESS_REAL_LINE_0499'), null, { timeout: 15000 });
  await page.screenshot({ path: path.join(output, 'real-less-bottom.png') });
  await page.keyboard.press('q');
  const pagerReturned = path.join(root, 'pager-returned.txt');
  await page.keyboard.insertText(`echo LESS_SHELL_PRESERVED>"${pagerReturned}"`);
  await page.keyboard.press('Enter');
  for (let attempt = 0; attempt < 150; attempt++) {
    try { if ((await readFile(pagerReturned, 'utf8')).trim() === 'LESS_SHELL_PRESERVED') { report.realLessExitedToSameShell = true; break; } } catch { /* command pending */ }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.equal(report.realLessExitedToSameShell, true);
  report.realLessNavigationVerified = true;
  for (let attempt = 0; attempt < 12; attempt++) {
    const result = await page.evaluate(async terminalId => {
      const response = await fetch(`/api/terminals/${terminalId}/close`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      return { status: response.status, body: await response.json() };
    }, id);
    report.close = result;
    if (result.status === 200) break;
    assert.equal(result.status, 409);
    await page.waitForTimeout(200);
  }
  assert.equal(report.close.status, 200);
  assert.deepEqual(report.errors, []);
  report.passed = true;
} catch (error) {
  report.failure = String(error);
  report.failureStack = error.stack;
  if (page) await page.screenshot({ path: path.join(output, 'failure.png') });
} finally {
  await browser?.close();
  child.stdin.write('stop\n');
  let timer;
  report.harnessExit = await Promise.race([exit, new Promise(resolve => { timer = setTimeout(() => resolve('timeout'), 30000); })]);
  clearTimeout(timer);
  report.passed = report.passed && report.harnessExit === 0;
  report.logs = logs;
  await writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2));
  if (report.harnessExit !== 'timeout') await rm(root, { recursive: true, force: true });
  else report.retainedRoot = root;
}
console.log(JSON.stringify({ passed: report.passed, native: report.native, harnessExit: report.harnessExit, failure: report.failure }));
if (!report.passed) process.exitCode = 1;
