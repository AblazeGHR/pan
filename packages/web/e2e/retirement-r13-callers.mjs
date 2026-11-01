// Actual React callers in Chromium; all API replies are owned fixtures, no provider effects.
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { chromium } from '@playwright/test';
import { createServer as createPortReservation } from 'node:net';

const root = fileURLToPath(new URL('..', import.meta.url));
const reservation = createPortReservation();
await new Promise((resolve, reject) => { reservation.once('error', reject); reservation.listen(0, '127.0.0.1', resolve); });
const ownedPort = reservation.address().port;
await new Promise((resolve, reject) => reservation.close((error) => error ? reject(error) : resolve()));
const server = await createServer({ configFile: false, root: path.join(root, 'src'),
  cacheDir: path.join(root, 'test-results/retirement-r13/vite-cache'),
  plugins: [react(), tailwindcss()], resolve: { alias: { '@': path.join(root, 'src') } },
  server: { host: '127.0.0.1', port: ownedPort, strictPort: true, proxy: {} } });
let browser;
try {
  await server.listen();
  const { port } = server.httpServer.address();
  assert.ok(port !== 8767 && port !== 8768);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = [], writes = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  const text = { type: 'string', minLength: 1 };
  const actions = [
    { id: 'product_assign_v2', name: 'assign', inputSchema: { properties: { sessionId: text, text }, required: ['sessionId', 'text'] }, defaultCompletion: 'durable-queue-accepted' },
    { id: 'product_process_logs_v1', name: 'process', inputSchema: { properties: { argv: { type: 'array' }, cwd: text, hideWindow: { type: 'boolean' } }, required: ['argv', 'cwd'] }, defaultCompletion: 'process-exit' },
  ].map((a) => ({ ...a, enabled: true, state: 'published', revision: 1, completionContracts: [{ id: a.defaultCompletion }] }));
  const references = Object.fromEntries(actions.map((a) => [a.id, { id: a.id, revision: 1, digest: `fixture-${a.id}` }]));
  await page.route('**/api/**', async (route) => {
    const request = route.request(), url = new URL(request.url());
    assert.equal(url.origin, `http://127.0.0.1:${port}`);
    let body = { items: [], nextCursor: null }, status = 200;
    if (request.method() !== 'GET') {
      writes.push({ path: url.pathname, body: request.postDataJSON() });
      body = { operationId: 'owned-browser-op', requestKey: writes.at(-1).body.requestKey, status: 'completed', objectRefs: [], links: {} };
      if (url.pathname.endsWith('/instantiate')) { status = 422; body = { error: { code: 'capability_unsupported' } }; }
    } else if (url.pathname === '/api/jobs/actions') body = { items: actions, references, nextCursor: null };
    else if (url.pathname === '/api/job-templates') body = { items: [{ id: 'passive', name: 'No Creator', state: 'published', revision: 2, stateRevision: 1 }], nextCursor: null };
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto(`http://127.0.0.1:${port}/retirement-r13-harness.html`);
  const button = (name) => page.getByRole('button', { name, exact: true });
  await button('创建 Jobs').click(); await page.getByLabel('名称', { exact: true }).fill('Owned assign');
  await page.getByLabel('Action for step 1').selectOption('product_assign_v2');
  await page.getByLabel('sessionId value', { exact: true }).fill('ses_owned');
  const raw = '  原文\nsecond line  ';
  await page.getByLabel('text value', { exact: true }).fill(raw);
  assert.equal(await page.getByLabel('text value', { exact: true }).inputValue(), raw);
  assert.equal(writes.length, 0);
  await button('保存定义').click(); await page.getByRole('form').waitFor({ state: 'detached' });
  assert.equal(writes[0].path, '/api/jobs');
  assert.deepEqual(writes[0].body.definition.steps[0].arguments.text, { from: 'literal', value: raw });
  assert.deepEqual(writes[0].body.definition.steps[0].actionRef, references.product_assign_v2);
  await button('创建 Jobs').click(); await page.getByLabel('名称', { exact: true }).fill('Owned process');
  await page.getByLabel('Action for step 1').selectOption('product_process_logs_v1');
  const argv = ['owned executable', 'a b', '"quoted"', '$literal'];
  await page.getByLabel('argv value (JSON)', { exact: true }).fill(JSON.stringify(argv));
  await page.getByLabel('cwd value', { exact: true }).fill('owned directory');
  await button('保存定义').click(); await page.getByRole('form').waitFor({ state: 'detached' });
  assert.equal(writes[1].path, '/api/jobs');
  assert.deepEqual(writes[1].body.definition.steps[0].arguments.argv, { from: 'literal', value: argv });
  assert.equal(writes.length, 2);
  await button('JobTemplates').click(); await button('Instantiate No Creator').click();
  assert.equal(await page.getByLabel('Activate future time events').isChecked(), false);
  await button('Create from time Template').click();
  await page.getByText(/\[capability_unsupported\]/).waitFor();
  assert.equal(writes.length, 3);
  assert.equal(writes[2].path, '/api/job-templates/passive/instantiate');
  assert.equal(writes[2].body.activate, false);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks: ['assign multiline raw text/reference/passive save', 'process argv boundaries/passive save', 'no-Creator unsupported/default disabled/no fallback'], passed: 3, writes: writes.map((w) => w.path), pageErrors: errors, port, chromium: browser.version(), layer: 'Chromium + Vite + fixture HTTP; no provider execution' }));
} finally { await browser?.close(); await server.close(); }
