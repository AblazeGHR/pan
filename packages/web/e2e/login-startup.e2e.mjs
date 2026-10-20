/* Real Chromium + isolated actual API routes + owned native registration.
 * Failure injection is test-only and never exposed by the production server.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';

const web = fileURLToPath(new URL('..', import.meta.url));
const out = path.join(web, 'test-results/login-startup');
await fs.mkdir(out, { recursive: true });
const childEnv = Object.fromEntries(Object.entries(process.env).filter(([name]) => !name.startsWith('PAN_') && name !== 'PYTHONPATH'));
const api = spawn(process.env.LOGIN_TEST_PYTHON || 'python', [path.join(web, 'e2e/login-startup-api.py')], {
  cwd: web, env: childEnv, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
});
let apiLog = '';
api.stderr.on('data', (data) => { apiLog += data.toString(); });
let browser, vite, metadata;
try {
  metadata = await Promise.race([
    new Promise((resolve, reject) => {
      let buffer = '';
      api.stdout.on('data', (data) => {
        buffer += data.toString();
        const line = buffer.split('\n').find((value) => value.startsWith('{'));
        if (line) { try { resolve(JSON.parse(line)); } catch (error) { reject(error); } }
      });
      api.once('exit', (code) => reject(new Error(`API exit ${code}: ${apiLog}`)));
    }),
    new Promise((_, reject) => setTimeout(() => reject(new Error('API startup timeout')), 20000).unref()),
  ]);
  assert.ok(![8767, 8768].includes(metadata.port));
  const apiURL = `http://127.0.0.1:${metadata.port}`;
  vite = await createServer({ configFile: false, root: path.join(web, 'src'),
    cacheDir: path.join(out, 'vite-cache'), plugins: [react()],
    resolve: { alias: { '@': path.join(web, 'src') } },
    server: { host: '127.0.0.1', port: 0, proxy: { '/api': apiURL } },
  });
  await vite.listen();
  const port = vite.httpServer.address().port;
  assert.ok(![8767, 8768].includes(port));
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const url = `http://127.0.0.1:${port}/login-startup-harness.html`;
  const get = async () => (await fetch(`${apiURL}/api/settings/login-startup`)).json();
  const control = async (value) => {
    const response = await fetch(`${apiURL}/__test/control`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
    assert.equal(response.status, 200);
  };
  await page.goto(url);
  const toggle = page.getByRole('switch', { name: 'Start Pan after login' });
  await toggle.waitFor();
  await page.waitForFunction(() => !document.querySelector('[role=switch]').disabled);
  assert.equal((await get()).registered, false, 'render must never register');
  assert.equal(await toggle.getAttribute('aria-checked'), 'false');
  await toggle.click();
  await page.waitForFunction(() => document.querySelector('[role=switch]').getAttribute('aria-checked') === 'true');
  assert.equal((await get()).enabled, true, 'native read-back after enable');
  await page.reload();
  await page.waitForFunction(() => document.querySelector('[role=switch]')?.getAttribute('aria-checked') === 'true');
  await toggle.click();
  await page.waitForFunction(() => !document.querySelector('[role=switch]').disabled);
  assert.equal((await get()).registered, false);
  await control({ denied: true });
  await toggle.click();
  await page.getByRole('alert').waitFor();
  assert.match(await page.getByRole('alert').innerText(), /permission denial/);
  assert.equal(await toggle.getAttribute('aria-checked'), 'false');
  assert.equal((await get()).registered, false, 'denied write cannot report success');
  await page.screenshot({ path: path.join(out, 'permission-failure.png'), fullPage: true });
  await control({ denied: false, foreign: true });
  await page.getByRole('button', { name: 'Refresh login startup status' }).click();
  await page.getByText('foreign checkout sentinel', { exact: true }).waitFor();
  assert.equal(await toggle.isDisabled(), true);
  assert.equal((await get()).registration, 'foreign checkout sentinel');
  await control({ foreign: false });
  await page.getByRole('button', { name: 'Refresh login startup status' }).click();
  await page.waitForFunction(() => !document.querySelector('[role=switch]').disabled);
  assert.equal((await get()).registered, false);
  assert.deepEqual(errors, []);
  await page.screenshot({ path: path.join(out, 'off-clean.png'), fullPage: true });
  await fs.writeFile(path.join(out, 'result.json'), JSON.stringify({ ok: true, apiPort: metadata.port,
    browserPort: port, nativeNamespace: metadata.key, checks: ['default-off', 'enable-readback', 'reload-truth', 'disable-readback', 'permission-rollback', 'foreign-protection', 'cleanup'], browserErrors: errors }, null, 2));
  console.log('PASS real Chromium/API/native store: 7 checks; owned ports', metadata.port, port);
} finally {
  if (browser) await browser.close();
  if (vite) await vite.close();
  if (api.exitCode === null && metadata) {
    const exited = once(api, 'exit');
    await fetch(`http://127.0.0.1:${metadata.port}/__test/shutdown`, { method: 'POST' }).catch(() => {});
    await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 5000).unref())]);
    if (api.exitCode === null) { api.kill(); await exited; }
  } else if (api.exitCode === null) { api.kill(); await once(api, 'exit'); }
  if (process.platform === 'win32' && metadata) {
    const code = 'import sys,winreg\ntry:\n winreg.OpenKey(winreg.HKEY_CURRENT_USER,sys.argv[1]); sys.exit(1)\nexcept FileNotFoundError: pass';
    const cleanup = spawn(process.env.LOGIN_TEST_PYTHON || 'python', ['-c', code, metadata.key], { windowsHide: true, stdio: 'pipe' });
    const [exit] = await once(cleanup, 'exit');
    assert.equal(exit, 0, 'owned registry namespace must be absent after API shutdown');
  }
  await fs.writeFile(path.join(out, 'api-stderr.log'), apiLog);
}
