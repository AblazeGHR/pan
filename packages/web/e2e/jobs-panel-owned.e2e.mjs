import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn, spawnSync } from 'node:child_process';
import { chromium, expect } from '@playwright/test';
import { setTimeout as sleep } from 'node:timers/promises';

const root = path.resolve(import.meta.dirname, '../../..');
const runtime = path.join(root, '.tmp', `jobs-panel-${Date.now()}`);
const owned = path.join(runtime, 'owned');
await fs.mkdir(owned, { recursive: true });
await fs.writeFile(path.join(owned, '.jobs-owned-harness'), '');
const port = await new Promise(resolve => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => resolve(p)); }); });
assert(![8767, 8768].includes(port));
const base = `http://127.0.0.1:${port}`;
const configured = process.env.PAN_E2E_PYTHON || 'D:/project/Pan/.venv/Scripts/python.exe';
const cfg = await fs.readFile(path.join(path.dirname(configured), '..', 'pyvenv.cfg'), 'utf8');
const python = cfg.match(/^executable\s*=\s*(.+)$/m)?.[1]?.trim();
assert(python, 'venv interpreter must resolve');
const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.toUpperCase().startsWith('PAN_')));
Object.assign(env, { PAN_E2E_RUNTIME: runtime, PAN_OWNED_ROOT: owned, PAN_PORT: String(port), PYTHONPATH: root });
const child = spawn(python, [path.join(import.meta.dirname, 'owned-jobs-ui-server.py')], { cwd: root, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
let log = ''; child.stdout.on('data', c => { log += c; }); child.stderr.on('data', c => { log += c; });
const evidence = { port, checks: [], errors: [] }; let browser, page, longJobId;
try {
  let ready;
  for (let i = 0; i < 120; i++) { if (child.exitCode !== null) throw Error('server exited'); try { const r = await fetch(base + '/test/ready'); if (r.ok) { ready = await r.json(); break; } } catch {} await sleep(500); }
  assert(ready); assert.equal(path.resolve(ready.cwd), owned);
  const key = (await fs.readFile(path.join(owned, 'automation/user.key'), 'utf8')).trim();
  browser = await chromium.launch({ headless: true }); page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', e => evidence.errors.push(e.message));
  await page.goto(base + '/react/automation'); await page.getByLabel('Local user key').fill(key); await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();
  await page.goto(base + '/react/jobs');
  const create = async (name, script) => {
    await page.getByRole('button', { name: 'New Job', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Create a Job' })).toBeVisible();
    await page.getByLabel('Name', { exact: true }).fill(name);
    await page.getByRole('button', { name: 'Custom' }).first().click();
    await page.getByLabel('Createable Job kind').selectOption('background-process');
    await page.getByLabel(/^Executable/).fill(python);
    await page.getByRole('button', { name: 'Add argument' }).click();
    await page.getByLabel(/^Argument 1/).fill(script);
    await page.getByLabel(/^Working directory/).fill(ready.cwd);
    const target = page.getByLabel('Target session');
    const options = await target.locator('option').evaluateAll(xs => xs.map(x => x.value).filter(Boolean));
    assert(options.length); await target.selectOption(options[0]);
    await page.getByRole('button', { name: 'Create job', exact: true }).click();
    await expect(page.getByTestId('jobs-list-scroll').getByText(name, { exact: true })).toBeVisible({ timeout: 15000 });
  };
  await create('Owned panel process', path.join(owned, 'quick.py'));
  await page.reload(); await expect(page.getByTestId('jobs-list-scroll').getByText('Owned panel process', { exact: true })).toBeVisible();
  evidence.checks.push('main Jobs entry creates canonical process and refresh preserves it');
  await page.getByTestId('jobs-list-scroll').getByText('Owned panel process', { exact: true }).click();
  const detail = page.getByRole('region', { name: 'Job detail' }); await expect(detail).toBeVisible();
  await expect(detail.getByText('Overview', { exact: false }).first()).toBeVisible();
  await expect(detail.getByText('Execution', { exact: false }).first()).toBeVisible();
  await expect(detail.getByText('Loading…', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: path.join(runtime, 'detail-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 900 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: path.join(runtime, 'detail-narrow.png'), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await detail.getByRole('button', { name: 'Edit', exact: true }).click();
  await page.getByLabel('Name', { exact: true }).fill('Owned panel edited');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Job detail' }).getByText('Owned panel edited', { exact: true })).toBeVisible();
  await page.getByRole('region', { name: 'Job detail' }).getByRole('button', { name: 'Close' }).click();
  await page.getByTestId('jobs-list-scroll').getByText('Owned panel edited', { exact: true }).click();
  await expect(detail.getByText('Owned panel edited', { exact: true })).toBeVisible();
  evidence.checks.push('detail and edit replace the main panel and saved name reopens');
  await expect(detail.getByRole('button', { name: /^View output for run/ }).first()).toBeVisible({ timeout: 15000 });
  await detail.getByRole('button', { name: /^View output for run/ }).first().click();
  const output = detail.getByLabel(/^Run output for /).first(); await expect(output).toContainText('owned stdout'); await expect(output).toContainText('owned stderr');
  evidence.checks.push('real Run output shows stdout and stderr');
  await detail.getByRole('button', { name: 'Close' }).click();
  await create('Owned long process', path.join(owned, 'long_done.py'));
  const listed = await (await page.request.get(base + '/api/jobs/legacy-list')).json();
  const long = listed.jobs.find(j => j.name === 'Owned long process'); assert(long); longJobId = long.jobId;
  await page.getByTestId('jobs-list-scroll').getByText('Owned long process', { exact: true }).click();
  await expect(detail.getByRole('button', { name: /^View output for run/ }).first()).toBeVisible({ timeout: 15000 });
  let started = false;
  for (let i = 0; i < 60; i++) {
    await detail.getByRole('button', { name: /^View output for run/ }).first().click();
    await sleep(350);
    const out = detail.getByLabel(/^Run output for /).first();
    if (await out.count() && (await out.innerText()).includes('owned stdout')) { started = true; break; }
    await sleep(350);
  }
  assert(started, 'long process must produce its original stdout before cancellation');
  await detail.getByRole('button', { name: 'Cancel job', exact: true }).click();
  let run;
  for (let i=0; i<60; i++) { const data = await (await page.request.get(`${base}/api/jobs/${long.jobId}/runs`)).json(); run = data.items?.[0]; if (['cancelled', 'failed', 'uncertain'].includes(run?.state)) break; await sleep(500); }
  assert.equal(run?.state, 'cancelled'); longJobId = undefined;
  const verified = spawnSync(python, [path.join(import.meta.dirname, 'verify_job_execution.py'), '--root', owned, '--run', run.runId, '--expect', 'cancelled', '--require', 'owned stdout', '--require', 'owned stderr', '--forbid', 'owned completion marker'], { cwd: root, env, encoding: 'utf8' });
  assert.equal(verified.status, 0, verified.stderr || verified.stdout); evidence.execution = JSON.parse(verified.stdout);
  evidence.checks.push('cancelled original Run and owned process tree are verified');
  assert.deepEqual(evidence.errors, []); evidence.status = 'passed';
} catch (error) { evidence.status = 'failed'; evidence.failure = String(error.stack ?? error); if (page) { evidence.pageText = await page.locator('body').innerText(); await page.screenshot({ path: path.join(runtime, 'failure.png'), fullPage: true }); } process.exitCode = 1; }
finally { if (longJobId && page) { try { await page.request.post(`${base}/api/jobs/${longJobId}/cancel`, { data: {} }); } catch {} } await fs.writeFile(path.join(runtime, 'server.log'), log); await fs.writeFile(path.join(runtime, 'evidence.json'), JSON.stringify(evidence, null, 2)); console.log(JSON.stringify({ runtime, ...evidence })); await browser?.close(); child.kill(); }
