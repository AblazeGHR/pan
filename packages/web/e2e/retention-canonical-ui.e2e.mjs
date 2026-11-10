import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { chromium, expect } from '@playwright/test';

const root = path.resolve(import.meta.dirname, '../../..');
const runtime = path.join(root, '.tmp', `retention-ui-${Date.now()}`);
await fs.mkdir(runtime, { recursive: true });
const port = await new Promise(resolve => { const server = net.createServer(); server.listen(0, '127.0.0.1', () => { const value = server.address().port; server.close(() => resolve(value)); }); });
assert(![8767, 8768].includes(port));
const base = `http://127.0.0.1:${port}`;
const configured = process.env.PAN_E2E_PYTHON || 'D:/project/Pan/.venv/Scripts/python.exe';
const cfg = await fs.readFile(path.join(path.dirname(configured), '..', 'pyvenv.cfg'), 'utf8');
const python = cfg.match(/^executable\s*=\s*(.+)$/m)?.[1]?.trim();
assert(python, 'venv interpreter must resolve');
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith('PAN_')));
Object.assign(env, { PAN_E2E_RUNTIME: runtime, PAN_PORT: String(port), PYTHONPATH: root });
const child = spawn(python, [path.join(import.meta.dirname, 'action-p6-server.py')], { cwd: root, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
let log = ''; child.stdout.on('data', c => { log += c; }); child.stderr.on('data', c => { log += c; });
const evidence = { port, checks: [], errors: [] };
let browser, page;
try {
  let identity;
  for (let i = 0; i < 90; i++) { if (child.exitCode !== null) throw Error('owned server exited'); try { const r = await fetch(base + '/__e2e/identity'); if (r.ok) { identity = await r.json(); break; } } catch {} await sleep(500); }
  assert(identity); assert.equal(identity.pid, child.pid); assert.equal(path.resolve(identity.runtime), runtime); assert.equal(path.resolve(identity.checkout), root);
  const key = (await fs.readFile(path.join(runtime, 'automation/user.key'), 'utf8')).trim();
  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', error => evidence.errors.push(error.message));
  await page.goto(base + '/react/jobs');
  assert((await page.evaluate(async value => (await fetch('/api/automation/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: value }) })).ok, key)));
  await page.reload(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const input = page.getByRole('spinbutton', { name: 'Keep Completed Runs for days' });
  await expect(input).toBeVisible();
  const initial = await page.evaluate(async () => { const r = await fetch('/api/job-maintenance/retention-settings'); return { status: r.status, body: await r.json() }; });
  assert.equal(initial.status, 200); assert.match(initial.body.requestScope, /^[0-9a-f]{64}$/);
  await page.getByRole('checkbox', { name: 'Enable Completed Runs cleanup' }).check();
  await input.fill('31');
  const saved = page.waitForResponse(r => r.url().endsWith('/api/job-maintenance/retention-settings') && r.request().method() === 'PUT');
  await page.getByRole('button', { name: 'Save settings' }).click();
  const response = await saved; assert.equal(response.status(), 200);
  const sent = response.request().postDataJSON(); assert.equal(sent.expectedRequestScope, initial.body.requestScope); assert.equal(sent.rules.completed.days, 31);
  const receipt = await response.json(); assert.equal(receipt.requestKey, sent.requestKey); assert.equal(receipt.result.settings.requestScope, sent.expectedRequestScope);
  await expect(page.getByText('Settings saved.')).toBeVisible();
  await page.reload(); await page.getByRole('button', { name: 'Settings', exact: true }).click(); await expect(input).toHaveValue('31');
  evidence.checks.push('real settings save, receipt scope and reload');

  let lostBody, originalStatus, writes = 0;
  page.on('request', request => { if (request.method() === 'PUT' && request.url().endsWith('/api/job-maintenance/retention-settings')) writes++; });
  await page.route('**/api/job-maintenance/retention-settings', async route => {
    if (route.request().method() !== 'PUT') return route.continue();
    lostBody = route.request().postDataJSON();
    const result = await route.fetch(); originalStatus = result.status();
    await route.abort('failed');
  });
  await input.fill('32'); await page.getByRole('button', { name: 'Save settings' }).click();
  const query = page.getByRole('button', { name: 'Query original operation' });
  await expect(query).toBeEnabled(); assert.equal(originalStatus, 200); assert.equal(writes, 1);
  const pending = await page.evaluate(() => JSON.parse(sessionStorage.getItem('pan:job-retention-pending')));
  assert.equal(pending.requestKey, lostBody.requestKey); assert.equal(pending.expectedRequestScope, lostBody.expectedRequestScope); assert.deepEqual(pending.rules, lostBody.rules);
  await page.unroute('**/api/job-maintenance/retention-settings');
  await page.reload(); await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(input).toHaveValue('32');
  const recoveredResponse = page.waitForResponse(r => r.url().includes('/api/operations?') && r.request().method() === 'GET');
  await page.getByRole('button', { name: 'Query original operation' }).click();
  const recovered = await recoveredResponse; assert.equal(recovered.status(), 200);
  const original = await recovered.json(); assert.equal(original.requestKey, lostBody.requestKey); assert.equal(original.result.settings.requestScope, lostBody.expectedRequestScope);
  await expect(page.getByText('Settings saved.')).toBeVisible();
  assert.equal(await page.evaluate(() => sessionStorage.getItem('pan:job-retention-pending')), null);
  assert.equal(writes, 1); evidence.checks.push('lost PUT response recovered by original key after reload without another PUT');
  await page.screenshot({ path: path.join(runtime, 'retention-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: path.join(runtime, 'retention-narrow.png'), fullPage: true });
  assert.deepEqual(evidence.errors, []); evidence.status = 'passed';
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
