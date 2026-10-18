/* Real production panel/entry components; every API is intercepted, no cc-switch runs. */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium, expect } from '@playwright/test';
import { createServer, transformWithEsbuild } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';

const web = fileURLToPath(new URL('..', import.meta.url));
const evidence = path.resolve(web, '../../audit/cc-switch-panel-direct-20261018');
await mkdir(path.join(evidence, 'tmp'), { recursive: true });
process.env.TEMP = process.env.TMP = path.join(evidence, 'tmp');
const source = `import React from 'react';import {createRoot} from 'react-dom/client';
import {MemoryRouter,Routes,Route} from 'react-router-dom';
import PluginPanelView from '@/views/PluginPanelView';import {ViewEntryRail} from '@/components/layout/ViewEntryRail';
import '@/index.css';document.documentElement.dataset.theme='dark';
createRoot(document.getElementById('root')).render(<MemoryRouter initialEntries={['/plugins/cc-switch-bridge/accounts']}>
<div style={{height:'100dvh',display:'grid',gridTemplateColumns:'48px minmax(0,1fr)'}} className="bg-bg-primary text-text-primary">
<nav className="flex flex-col items-center gap-4 border-r border-border py-5"><ViewEntryRail/></nav>
<Routes><Route path="/plugins/:pluginId/:panelId" element={<PluginPanelView/>}/></Routes></div></MemoryRouter>);`;
const server = await createServer({ configFile: false, root: path.join(web, 'src'),
  cacheDir: path.join(evidence, 'vite-cache'), resolve: { alias: { '@': path.join(web, 'src') } },
  plugins: [react(), tailwind(), { name: 'cc-switch-bridge-fixture',
    resolveId(id) { if (id === 'virtual:bridge-fixture') return '\0bridge-fixture'; },
    async load(id) { if (id === '\0bridge-fixture') return await transformWithEsbuild(source, 'bridge-fixture.tsx', { loader: 'tsx', jsx: 'automatic' }); },
    configureServer(vite) { vite.middlewares.use('/bridge-fixture', async (_req, res) => {
      res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/bridge-fixture',
        '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@id/virtual:bridge-fixture"></script></body></html>'));
    }); },
  }], server: { host: '127.0.0.1', port: 0, proxy: {} },
});
await server.listen();
const port = server.httpServer.address().port;
const actions = [], errors = [];
const snapshot = { app: 'codex', currentProviderId: 'p-a', observedAt: Date.now(), accountError: null,
  providers: [{ id: 'p-a', name: 'OpenAI Official · Main', origin: 'https://chatgpt.com', isCurrent: true, quota: null },
    { id: 'p-b', name: 'OpenAI Official · Secondary', origin: 'https://chatgpt.com', isCurrent: false, quota: null },
    { id: 'p-c', name: 'SiliconFlow', origin: 'https://siliconflow.cn', isCurrent: false, quota: null }],
  accounts: [{ id: 'acc-a', login: 'main@example.test', isDefault: true }, { id: 'acc-b', login: 'secondary@example.test', isDefault: false }] };
const panel = { id: 'accounts', pluginId: 'cc-switch-bridge', title: 'CC Switch', renderer: 'account-switcher/v1', path: '/plugins/cc-switch-bridge/accounts',
  actions: Object.fromEntries(['list', 'quota', 'switch', 'default_account'].map(action => [action, { capability: 'cc-switch-bridge.' + action, effect: ['switch', 'default_account'].includes(action) ? 'write_external' : 'read' }])) };
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://**/*', route => route.abort());
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    let body;
    if (url.pathname === '/api/plugins/panels') body = { panels: [panel] };
    else {
      const action = url.pathname.split('/').at(-1);
      const request = route.request().postDataJSON();
      actions.push({ action, request, fake: true });
      if (action === 'quota') {
        // A real query taken 90s ago: the panel must age it from queriedAt, not from mount.
        const at = Date.now();
        const quota = { status: 'ok', available: true, message: null, queriedAt: at - 90_000, balances: [],
          tiers: [{ name: 'five_hour', usedPercent: 70, resetsAt: new Date(at + 90 * 60_000).toISOString() },
            { name: 'weekly', usedPercent: 30, resetsAt: new Date(at + 96 * 3600_000).toISOString() }] };
        snapshot.providers.find(p => p.id === request.arguments.id).quota = quota;
        body = { structuredContent: { id: request.arguments.id, app: 'codex', quota } };
      } else {
        if (action === 'switch') {
          // Hold the write in flight long enough for a second click to hit the pending guard.
          await new Promise(resolve => setTimeout(resolve, 300));
          assert.equal(request.arguments.expectedCurrentId, snapshot.currentProviderId);
          assert.ok(request.requestId);
          snapshot.currentProviderId = request.arguments.id;
          snapshot.providers.forEach(p => { p.isCurrent = p.id === snapshot.currentProviderId; });
        }
        if (action === 'default_account') snapshot.accounts.forEach(a => { a.isDefault = a.id === request.arguments.id; });
        body = { structuredContent: snapshot };
      }
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  // Cold first load pays the dev-server transform cost; reload once so the timed
  // flow below starts from a fast warm load and the tick alignment is not jitter.
  await page.goto(`http://127.0.0.1:${port}/bridge-fixture`, { timeout: 120_000 });
  await expect(page.getByText('main@example.test')).toBeVisible();
  await page.reload();
  await expect(page.getByText('main@example.test')).toBeVisible();
  assert.deepEqual(actions.map(a => a.action), ['list', 'list']);
  await expect(page.getByRole('link', { name: 'CC Switch' })).toBeVisible();
  await page.getByRole('button', { name: '刷新 OpenAI Official · Main 额度' }).click();
  await expect(page.getByText('70%')).toBeVisible();
  await expect(page.getByText('30%')).toBeVisible();
  // Reset windows count down from now with the raw reset time still viewable; the
  // query age comes from the quota's own queriedAt (90s ago), not from mount time.
  await expect(page.getByText(/距现在剩余 1h 30min/)).toBeVisible();
  await expect(page.getByText(/距现在剩余 96h 0min/)).toBeVisible();
  await expect(page.getByText(/距现在已过去 1min/)).toBeVisible();
  await expect(page.getByText(/重置于 \d.*距现在剩余 1h 30min/)).toBeVisible();
  await page.screenshot({ path: path.join(evidence, 'desktop.png'), fullPage: true });
  // The relative times advance from the local clock alone: no new request may appear.
  const callsBeforeWait = actions.length;
  await page.waitForTimeout(65_000);
  await expect(page.getByText(/距现在剩余 1h 29min/)).toBeVisible();
  await expect(page.getByText(/距现在已过去 2min/)).toBeVisible();
  assert.equal(actions.length, callsBeforeWait);
  // One click sends exactly one switch, carrying the observed current ID and a request ID.
  await page.getByRole('button', { name: '启用配置' }).first().click();
  await expect(page.getByRole('button', { name: '刷新列表' })).toBeEnabled();
  const switches = () => actions.filter(a => a.action === 'switch');
  assert.equal(switches().length, 1);
  assert.equal(switches()[0].request.arguments.id, 'p-b');
  assert.equal(switches()[0].request.arguments.expectedCurrentId, 'p-a');
  assert.ok(switches()[0].request.requestId);
  await expect(page.getByText('当前：OpenAI Official · Secondary')).toBeVisible();
  // A double click while the write is pending still admits only one more switch,
  // re-deriving expectedCurrentId from the fresh state.
  await page.getByRole('button', { name: '启用配置' }).first().dblclick();
  await expect(page.getByRole('button', { name: '刷新列表' })).toBeEnabled();
  assert.equal(switches().length, 2);
  assert.equal(switches()[1].request.arguments.id, 'p-a');
  assert.equal(switches()[1].request.arguments.expectedCurrentId, 'p-b');
  // Default account switching stays a separate, single-click action.
  await page.getByRole('button', { name: '设为默认账号' }).click();
  await expect(page.getByRole('button', { name: '刷新列表' })).toBeEnabled();
  const defaults = actions.filter(a => a.action === 'default_account');
  assert.equal(defaults.length, 1);
  assert.equal(defaults[0].request.arguments.id, 'acc-b');
  assert.equal(defaults[0].request.arguments.expectedCurrentId, 'acc-a');
  await page.getByRole('textbox', { name: '搜索配置或账号' }).fill('SiliconFlow');
  await expect(page.getByRole('heading', { name: 'SiliconFlow', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'OpenAI Official · Main', exact: true })).toHaveCount(0);
  await page.getByRole('textbox', { name: '搜索配置或账号' }).clear();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.locator('section[aria-label="CC Switch"]').evaluate(element => { element.scrollTop = 0; });
  assert.ok(await page.locator('section[aria-label="CC Switch"]').evaluate(element => element.scrollWidth <= element.clientWidth));
  await page.screenshot({ path: path.join(evidence, 'mobile.png'), fullPage: true });
  await page.locator('section[aria-label="CC Switch"]').evaluate(element => { element.scrollTop = element.scrollHeight; });
  await page.screenshot({ path: path.join(evidence, 'mobile-accounts.png'), fullPage: true });
  assert.deepEqual(errors, []);
  await writeFile(path.join(evidence, 'browser-result.json'), JSON.stringify({ passed: true, actions, errors,
    desktop: true, mobile: true, realCliExecuted: false, backend: 'stateful intercepted API fixture',
    singleClickSwitch: true, pendingGuardDoubleClick: true, relativeTimeCountdown: true,
    relativeTimeAdvanced: true, noQuotaPollingDuringWait: true, port }, null, 2));
  console.log(JSON.stringify({ passed: true, fakeSwitches: switches().length, fakeDefaultAccounts: defaults.length,
    realCliExecuted: false, evidence }));
} finally { await browser?.close(); await server.close(); }
