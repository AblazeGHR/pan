import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';

const cwd = fileURLToPath(new URL('..', import.meta.url));
const root = await mkdtemp(path.join(tmpdir(), 'pan-job-pin-'));
const evidence = path.resolve(cwd, '../../audit/job-pin/repair');
await mkdir(evidence, { recursive: true });
async function freePort() { return await new Promise((resolve) => {
  const socket = net.createServer();
  socket.listen(0, '127.0.0.1', () => { const p = socket.address().port; socket.close(() => resolve(p)); });
}); }
const port = await freePort();
const requestedUiPort = await freePort();
assert.ok(![8767, 8768].includes(port));
assert.ok(![8767, 8768].includes(requestedUiPort));
let backend, vite, browser;
const log = [];
async function startBackend() {
  backend = spawn('python', [fileURLToPath(new URL('./job-pin-server.py', import.meta.url)), String(port)], {
    cwd, windowsHide: true, env: { ...process.env, PAN_SCHEDULER_DIR: root, PAN_BACKGROUND_JOBS_DIR: root },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  backend.stdout.on('data', (data) => log.push(String(data)));
  backend.stderr.on('data', (data) => log.push(String(data)));
  for (let i = 0; i < 80; i++) {
    if (backend.exitCode !== null) throw new Error(log.join(''));
    try { if ((await fetch(`http://127.0.0.1:${port}/api/jobs`)).ok) return; } catch {}
    await delay(100);
  }
  throw new Error('isolated backend not ready');
}
async function stopBackend() {
  if (backend && backend.exitCode === null) {
    const exited = new Promise((resolve) => backend.once('exit', resolve));
    backend.kill(); await exited;
  }
}
try {
  await startBackend();
  vite = await createServer({ configFile: path.join(cwd, 'vite.config.ts'),
    cacheDir: path.join(root, 'vite-cache'),
    server: { host: '127.0.0.1', port: requestedUiPort, strictPort: true, proxy: { '/api': `http://127.0.0.1:${port}`, '/ws': `http://127.0.0.1:${port}` } } });
  await vite.listen();
  const uiPort = vite.httpServer.address().port;
  browser = await chromium.launch({ headless: true });
  const results = [];
  for (const mobile of [false, true]) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 900 }, isMobile: mobile, hasTouch: mobile });
    const page = await context.newPage();
    const errors = [], forbidden = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await context.route(/.*:(8767|8768)(\/|$).*/, (route) => { forbidden.push(route.request().url()); return route.abort(); });
    const rows = () => page.locator('div.cursor-pointer').filter({ has: page.getByRole('button', { name: 'Job actions' }) });
    const row = (index) => rows().filter({ has: page.getByText(`Pin fixture ${index}`, { exact: true }) });
    const order = () => rows().locator('div[title^="Pin fixture"]').allTextContents();
    async function toggle(index, pinned) {
      const action = row(index).getByRole('button', { name: 'Job actions' });
      if (mobile) await action.tap(); else await action.click();
      const response = page.waitForResponse((r) => r.url().endsWith(`/job_pin_${index}/pin`));
      const item = page.getByRole('menuitem', { name: pinned ? 'Pin' : 'Unpin', exact: true });
      if (mobile) await item.tap(); else await item.click();
      return (await response).json();
    }
    await page.goto(`http://127.0.0.1:${uiPort}/job-pin-harness.html`);
    await row(0).waitFor();
    assert.deepEqual(await order(), ['Pin fixture 3', 'Pin fixture 0', 'Pin fixture 2', 'Pin fixture 4', 'Pin fixture 1']);
    assert.equal((await toggle(1, true)).ok, true);
    assert.equal((await toggle(2, true)).ok, true);
    await page.waitForFunction(() => document.querySelectorAll('[aria-label="Pinned"]').length === 2);
    assert.deepEqual(await order(), ['Pin fixture 2', 'Pin fixture 1', 'Pin fixture 3', 'Pin fixture 0', 'Pin fixture 4']);
    await page.getByRole('button', { name: 'Completed', exact: true }).click();
    assert.deepEqual(await order(), ['Pin fixture 1', 'Pin fixture 4']);
    await page.getByRole('button', { name: 'All', exact: true }).click();
    await page.getByRole('combobox', { name: 'Kind' }).selectOption('session-broadcast');
    assert.deepEqual(await order(), ['Pin fixture 1']);
    await page.getByRole('combobox', { name: 'Kind' }).selectOption('all');
    await page.getByRole('searchbox', { name: 'Search jobs' }).fill('fixture 0');
    assert.deepEqual(await order(), ['Pin fixture 0']);
    await page.getByRole('searchbox', { name: 'Search jobs' }).fill('');
    await page.reload(); await row(0).waitFor();
    assert.equal(await page.getByLabel('Pinned', { exact: true }).count(), 2);
    await stopBackend(); await startBackend();
    await page.reload(); await row(0).waitFor();
    assert.equal(await page.getByLabel('Pinned', { exact: true }).count(), 2);
    await fetch(`http://127.0.0.1:${port}/test/write-failure/1`, { method: 'PUT' });
    assert.equal((await toggle(1, false)).error.code, 'persistence_failed');
    await page.reload(); await row(0).waitFor();
    assert.equal(await row(1).getByLabel('Pinned', { exact: true }).count(), 1);
    await fetch(`http://127.0.0.1:${port}/test/write-failure/0`, { method: 'PUT' });
    await page.screenshot({ path: path.join(evidence, `${mobile ? 'mobile' : 'desktop'}.png`) });
    assert.equal((await toggle(1, false)).ok, true);
    assert.equal((await toggle(2, false)).ok, true);
    await page.waitForFunction(() => document.querySelectorAll('[aria-label="Pinned"]').length === 0);
    assert.deepEqual(await order(), ['Pin fixture 3', 'Pin fixture 0', 'Pin fixture 2', 'Pin fixture 4', 'Pin fixture 1']);
    await fetch(`http://127.0.0.1:${port}/test/pin-corrupt/1`, { method: 'PUT' });
    await page.reload(); await row(0).waitFor();
    const warning = page.getByRole('status');
    assert.match(await warning.textContent(), /Pin states are unknown/);
    assert.equal(await page.getByLabel('Pinned', { exact: true }).count(), 0);
    const action = row(1).getByRole('button', { name: 'Job actions' });
    if (mobile) await action.tap(); else await action.click();
    assert.equal(await page.getByRole('menuitem', { name: 'Pin unavailable' }).isDisabled(), true);
    assert.equal(await page.getByRole('menuitem', { name: 'Delete', exact: true }).isEnabled(), true);
    await page.screenshot({ path: path.join(evidence, `${mobile ? 'mobile' : 'desktop'}-unavailable.png`) });
    const rejected = await fetch(`http://127.0.0.1:${port}/api/jobs/job_pin_1/pin`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pinned: true }),
    });
    assert.equal((await rejected.json()).error.code, 'pin_metadata_unavailable');
    assert.equal((await (await fetch(`http://127.0.0.1:${port}/test/pin-bytes`)).json()).hex, Buffer.from('{broken json').toString('hex'));
    const backdrop = page.locator('div.fixed.inset-0.z-20');
    if (mobile) await backdrop.tap({ position: { x: 2, y: 2 } }); else await backdrop.click({ position: { x: 2, y: 2 } });
    await page.getByRole('button', { name: 'Completed', exact: true }).click();
    assert.deepEqual(await order(), ['Pin fixture 4', 'Pin fixture 1']);
    await page.getByRole('button', { name: 'All', exact: true }).click();
    await page.getByRole('searchbox', { name: 'Search jobs' }).fill('fixture 0');
    assert.deepEqual(await order(), ['Pin fixture 0']);
    await fetch(`http://127.0.0.1:${port}/test/pin-corrupt/0`, { method: 'PUT' });
    await page.reload(); await row(0).waitFor();
    assert.equal(await warning.count(), 0);
    assert.equal((await toggle(1, true)).ok, true);
    assert.equal((await toggle(1, false)).ok, true);
        assert.deepEqual(errors, []); assert.deepEqual(forbidden, []);
    results.push({ steps: ['pin', 'multiple pins', 'status filter', 'kind filter', 'search', 'refresh', 'backend restart', 'write failure and reread', 'unpin restores sorting', 'corrupt metadata warning and disabled pin', 'corruption byte preservation', 'filters during metadata outage', 'restore and reread'], mobile, viewport: page.viewportSize(), passed: true, errors, forbidden });
    await context.close();
  }
  await writeFile(path.join(evidence, 'chromium.json'), JSON.stringify({ backendPort: port, results, log }, null, 2));
  console.log(JSON.stringify({ backendPort: port, results }));
} finally {
  await browser?.close(); await vite?.close(); await stopBackend();
  await rm(root, { recursive: true, force: true });
}
