/* global process, URL, console, setTimeout, clearTimeout, fetch */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { chromium, expect } from '@playwright/test';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const output = `${root}/.validation/handoff-original-prompt`;
const runtime = `${output}/runtime-${Date.now()}`;
mkdirSync(runtime, { recursive: true });
const child = spawn(process.env.PAN_TEST_PYTHON || 'D:/project/Pan-main/.venv/Scripts/python.exe',
  [`${root}/packages/web/e2e/handoff-original-prompt.server.py`], {
    cwd: root, env: { ...process.env, PAN_E2E_RUNTIME: runtime }, windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
let logs = '';
let browser;
const report = { runtime, pid: child.pid, results: [], errors: [] };
try {
  const base = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('owned server startup timeout')), 20000);
    const read = (chunk) => {
      logs += chunk.toString();
      const match = logs.match(/Uvicorn running on (http:\/\/127\.0\.0\.1:\d+)/);
      if (match) { clearTimeout(timeout); resolve(match[1]); }
    };
    child.stdout.on('data', read);
    child.stderr.on('data', read);
    child.once('exit', (code) => { clearTimeout(timeout); reject(new Error(`server exited ${code}: ${logs}`)); });
  });
  assert(![8767, 8768].includes(Number(new URL(base).port)));
  report.base = base;
  const fixtures = JSON.parse(readFileSync(`${runtime}/fixtures.json`, 'utf8'));
  const api = async (path, body) => {
    const response = await fetch(`${base}/api/${path}`, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    assert.equal(response.status, 200);
    return response.json();
  };
  let n = 0;
  for (const copySettings of [true, false]) {
    for (const [options, expected] of [[{}, 'source rules'],
      [{ overrideOriginalPrompt: false, originalPrompt: 'ignored' }, 'source rules'],
      [{ overrideOriginalPrompt: true, originalPrompt: 'new rules' }, 'new rules'],
      [{ overrideOriginalPrompt: true, originalPrompt: '' }, '']]) {
      const id = fixtures[`HTTP ${n++}`];
      for (const invalid of [{ overrideOriginalPrompt: 'true' },
        { overrideOriginalPrompt: true, originalPrompt: null }]) {
        assert((await api(`sessions/${id}/handoff`, { handoffPrompt: 'brief', ...invalid })).error);
        assert.equal((await api(`sessions/${id}`)).name, `HTTP ${n - 1}`);
      }
      const result = await api(`sessions/${id}/handoff`, {
        handoffPrompt: 'new brief', copySettings, adapter: 'codex', ...options,
      });
      assert.equal(result.ok, true);
      assert.equal(result.session.originalPrompt, expected);
      assert.equal(result.session.handoffPrompt, 'new brief');
      assert(!result.session.systemPrompt.includes('OLD BRIEF'));
      assert.equal((await api(`sessions/${id}`)).originalPrompt, 'source rules');
    }
  }
  report.results.push('real HTTP: eight settings/override combinations and sixteen invalid requests');
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on('pageerror', (error) => report.errors.push(error.message));
  await page.route('**/*', (route) => {
    if (new URL(route.request().url()).origin !== base) return route.abort();
    return route.continue();
  });
  await page.goto(`${base}/react/`);
  const open = async (name) => {
    const card = page.locator('[data-session-card-id]').filter({ has: page.getByText(name, { exact: true }) });
    await card.getByTestId('session-actions-menu').click();
    await page.getByRole('button', { name: '替身交接', exact: true }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
  };
  const brief = page.getByRole('textbox', { name: '交接简报（必填）' });
  const original = page.getByRole('textbox', { name: '新的原始提示' });
  await open('UI Alpha');
  await expect(page.getByRole('checkbox')).not.toBeChecked();
  await brief.fill('draft a');
  await page.getByRole('checkbox').check();
  await original.fill('private draft');
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await open('UI Bravo');
  await expect(brief).toHaveValue('');
  await expect(original).toHaveValue('');
  await expect(page.getByRole('checkbox')).not.toBeChecked();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await open('UI Alpha');
  await expect(brief).toHaveValue('');
  await brief.fill('browser brief');
  await page.getByRole('checkbox').check();
  await expect(original).toHaveValue('');
  await page.screenshot({ path: `${output}/override-empty.png` });
  await page.getByRole('button', { name: '确认交接', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const sessions = await api('sessions');
  const list = Array.isArray(sessions) ? sessions : sessions.sessions;
  const successor = list.find((s) => s.name === 'UI Alpha');
  assert(successor && successor.id !== fixtures['UI Alpha']);
  const persisted = await api(`sessions/${successor.id}`);
  assert.equal(persisted.originalPrompt, '');
  assert.equal(persisted.systemPrompt, 'browser brief');
  report.results.push('Chromium production Sidebar/menu/dialog: default off, close/reopen, A/B/A reset, empty override committed');
  assert.deepEqual(report.errors, []);
  console.log(JSON.stringify(report, null, 2));
} finally {
  if (browser) await browser.close();
  const exited = new Promise((resolve) => child.once('exit', resolve));
  child.kill();
  await exited;
  writeFileSync(`${output}/server.log`, logs);
  writeFileSync(`${output}/results.json`, JSON.stringify(report, null, 2));
}
