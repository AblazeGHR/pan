/* Production app + isolated HTTP/WS fixtures; never connects to a Pan service. */
import assert from 'node:assert/strict';
import http from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const dist = new URL('../dist/', import.meta.url);
const evidence = new URL('../../../evidence/jobs-controls-sort/', import.meta.url);
await mkdir(evidence, { recursive: true });
const server = http.createServer(async (req, res) => {
  try {
    const path = req.url.startsWith('/react/assets/') ? req.url.slice('/react/'.length) : 'index.html';
    const data = await readFile(new URL(path, dist));
    res.setHeader('Content-Type', path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(data);
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
assert(![8767, 8768].includes(port));
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
const base = { source: { type: 'user' }, target: { sessionId: null }, paused: false, runCount: 0, description: '', createdAt: 1, updatedAt: 1 };
let jobs = [
  { ...base, jobId: 'b', name: 'Owned running fixture', kind: 'background-process', status: 'running', label: 'Long-label-'.repeat(30), updatedAt: 3 },
  { ...base, jobId: 'a', name: 'Completed fixture', kind: 'session-message', status: 'completed', updatedAt: 2 },
  { ...base, jobId: 'c', name: 'Legacy missing time', kind: 'main-lifecycle', status: 'completed', createdAt: null, updatedAt: null },
];
let socket;
let cancelCalls = 0;
let deleteCalls = 0;
let failCancel = true;
await page.routeWebSocket('**/ws', ws => { socket = ws; ws.onMessage(() => {}); });
await page.route('**/api/**', async route => {
  const request = route.request();
  const path = new URL(request.url()).pathname;
  let data = { ok: true, sessions: [], workspaces: [], groups: [], entries: [], panels: [], adapters: [] };
  if (path === '/api/jobs') data = { ok: true, jobs };
  else if (path === '/api/jobs/kinds') data = { ok: true, kinds: [] };
  else if (path === '/api/background-jobs/b/cancel') {
    cancelCalls++;
    await new Promise(resolve => setTimeout(resolve, 150));
    if (failCancel) data = { ok: false, error: { code: 'cancel_unsafe', message: 'Fixture cancellation refused' } };
    else { jobs[0] = { ...jobs[0], status: 'cancelled', updatedAt: 4 }; data = jobs[0]; }
  } else if (path === '/api/jobs/b') data = { ok: true, job: jobs[0] };
  if (request.method() === 'DELETE') deleteCalls++;
  await route.fulfill({ json: data });
});
try {
  await page.goto(`http://127.0.0.1:${port}/react/jobs`);
  await page.getByText('Owned running fixture', { exact: true }).waitFor();
  await page.setViewportSize({ width: 320, height: 850 });
  await page.waitForFunction(() => document.querySelector('[title="Owned running fixture"]')?.getBoundingClientRect().width > 30);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: fileURLToPath(new URL('running-mobile-320.png', evidence)), fullPage: true });
  await page.getByRole('button', { name: 'Cancel job', exact: true }).click();
  assert(await page.getByRole('button', { name: 'Cancelling…' }).isDisabled());
  await page.getByText('Fixture cancellation refused', { exact: true }).waitFor();
  assert(await page.getByText('Owned running fixture', { exact: true }).isVisible());
  failCancel = false;
  await page.getByRole('button', { name: 'Cancel job', exact: true }).click();
  await page.getByText('cancelled', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Cancel job', exact: true }).count(), 0);
  assert.equal(cancelCalls, 2);
  assert.equal(deleteCalls, 0);
  await page.getByLabel('Sort jobs', { exact: true }).selectOption('updated');
  await page.getByRole('button', { name: 'Descending', exact: true }).click();
  const titles = () => page.locator('[data-testid="jobs-list-scroll"] [title]').evaluateAll(nodes => nodes.filter(n => ['Owned running fixture', 'Completed fixture', 'Legacy missing time'].includes(n.title)).map(n => n.title));
  assert.deepEqual(await titles(), ['Completed fixture', 'Owned running fixture', 'Legacy missing time']);
  jobs[1] = { ...jobs[1], updatedAt: 5 };
  socket.send(JSON.stringify({ type: 'job.updated', job: jobs[1] }));
  await page.waitForFunction(() => document.querySelector('[data-testid="jobs-list-scroll"] [title]')?.title === 'Owned running fixture');
  await page.reload();
  await page.getByText('Owned running fixture', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('Sort jobs', { exact: true }).inputValue(), 'updated');
  assert(await page.getByRole('button', { name: 'Ascending', exact: true }).isVisible());
  for (const width of [1280, 375, 320]) {
    await page.setViewportSize({ width, height: 850 });
    await page.waitForFunction(() => document.querySelector('[title="Owned running fixture"]')?.getBoundingClientRect().width > 30);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const geometry = await page.evaluate(() => {
      const title = document.querySelector('[title="Owned running fixture"]');
      const label = document.querySelector('[title^="Long-label-"]');
      const a = title.getBoundingClientRect(), b = label.getBoundingClientRect();
      return { sameRow: Math.abs(a.y - b.y) < 8, titleWidth: a.width, truncated: label.scrollWidth > label.clientWidth };
    });
    assert(geometry.sameRow && geometry.titleWidth > 30 && geometry.truncated, JSON.stringify(geometry));
    await page.screenshot({ path: fileURLToPath(new URL(`production-${width}.png`, evidence)), fullPage: true });
  }
  assert.deepEqual(errors, []);
  const result = { production: true, fixtureNetwork: true, port, cancelCalls, deleteCalls, widths: [1280, 375, 320], errors };
  await writeFile(new URL('browser-result.json', evidence), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} catch (error) { console.log('PAGE', await page.locator('body').innerText(), errors); await page.screenshot({ path: fileURLToPath(new URL('failure.png', evidence)), fullPage: true }); throw error; } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
