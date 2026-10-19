/* global process, URL, setTimeout, console, document, innerWidth */
/* Real production components in real Chromium. Every API is a stateful mock;
 * no cc-switch CLI or user data is touched. */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium, expect } from '@playwright/test';
import { createServer, transformWithEsbuild } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';

const web = fileURLToPath(new URL('..', import.meta.url));
const evidence = path.resolve(web, '../../audit/cc-switch-panel-cache-20261019');
await mkdir(path.join(evidence, 'tmp'), { recursive: true });
process.env.TEMP = process.env.TMP = path.join(evidence, 'tmp');

const source = `import React,{StrictMode} from 'react';import {createRoot} from 'react-dom/client';
import {MemoryRouter,Link,Routes,Route} from 'react-router-dom';
import PluginPanelView from '@/views/PluginPanelView';import {ViewEntryRail} from '@/components/layout/ViewEntryRail';
import '@/index.css';document.documentElement.dataset.theme='dark';
createRoot(document.getElementById('root')).render(<StrictMode><MemoryRouter initialEntries={['/plugins/cc-switch-bridge/accounts']}>
<div style={{height:'100dvh',display:'grid',gridTemplateColumns:'48px minmax(0,1fr)'}} className="bg-bg-primary text-text-primary">
<nav className="flex flex-col items-center gap-4 border-r border-border py-5"><ViewEntryRail/><Link to="/other" className="text-xs text-text-tertiary">Other</Link></nav>
<Routes><Route path="/plugins/:pluginId/:panelId" element={<PluginPanelView/>}/>
<Route path="/other" element={<div className="p-6"><Link to="/plugins/cc-switch-bridge/accounts">Back to panel</Link></div>}/></Routes></div></MemoryRouter></StrictMode>);`;

const server = await createServer({ configFile: false, root: path.join(web, 'src'),
  cacheDir: path.join(evidence, 'vite-cache'), resolve: { alias: { '@': path.join(web, 'src') } },
  plugins: [react(), tailwind(), { name: 'cc-switch-panel-cache-fixture',
    resolveId(id) { if (id === 'virtual:cache-fixture') return '\0cache-fixture'; },
    async load(id) { if (id === '\0cache-fixture') return await transformWithEsbuild(source, 'cache-fixture.tsx', { loader: 'tsx', jsx: 'automatic' }); },
    configureServer(vite) { vite.middlewares.use('/cache-fixture', async (_req, res) => {
      res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/cache-fixture',
        '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@id/virtual:cache-fixture"></script></body></html>'));
    }); },
  }], server: { host: '127.0.0.1', port: 0, proxy: {} },
});
await server.listen();
const port = server.httpServer.address().port;

const MINUTE = 60_000;
const quotas = {
  codex: {
    'p-a': { status: 'ok', available: true, queriedAt: Date.now() - MINUTE, message: null, balances: [],
      tiers: [{ name: 'five_hour', usedPercent: 70, resetsAt: new Date(Date.now() + 3 * 3600_000).toISOString() }] },
    'p-b': { status: 'ok', available: true, queriedAt: Date.now() - 10 * MINUTE, message: null, balances: [],
      tiers: [{ name: 'five_hour', usedPercent: 30, resetsAt: new Date(Date.now() + 4 * 3600_000).toISOString() }] },
    'p-c': null,
  },
  claude: { 'c-a': null },
  gemini: {},
};
const providers = {
  codex: [{ id: 'p-a', name: 'Main' }, { id: 'p-b', name: 'Secondary' }, { id: 'p-c', name: 'Third' }],
  claude: [{ id: 'c-a', name: 'Claude Main' }],
  gemini: [],
};
const current = { codex: 'p-a', claude: 'c-a', gemini: null };
const snapshot = (app) => ({ app, currentProviderId: current[app], observedAt: Date.now(),
  providers: (providers[app] || []).map((provider) => ({ ...provider,
    origin: 'https://api.example.test', isCurrent: provider.id === current[app], quota: quotas[app]?.[provider.id] ?? null })),
  accounts: app === 'codex'
    ? [{ id: 'acc-a', login: 'main@example.test', isDefault: true }, { id: 'acc-b', login: 'secondary@example.test', isDefault: false }]
    : [], accountError: null });
const panel = { id: 'accounts', pluginId: 'cc-switch-bridge', title: 'CC Switch', renderer: 'account-switcher/v1',
  path: '/plugins/cc-switch-bridge/accounts',
  actions: Object.fromEntries(['list', 'quota', 'switch', 'default_account'].map((action) =>
    [action, { capability: 'cc-switch-bridge.' + action, effect: ['switch', 'default_account'].includes(action) ? 'write_external' : 'read' }])) };

const actions = [];
const errors = [];
let listCount = 0;
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('https://**/*', (route) => route.abort());
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/plugins/panels') {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ panels: [panel] }) });
      return;
    }
    const action = url.pathname.split('/').at(-1);
    const request = route.request().postDataJSON();
    const app = request.arguments.app;
    actions.push({ action, app, id: request.arguments.id ?? null,
      expectedCurrentId: request.arguments.expectedCurrentId ?? null, requestId: request.requestId ?? null, at: Date.now() });
    if (action === 'list') {
      listCount += 1;
      // The first paint is served immediately; later list reads are held so a
      // re-entry can prove the cached snapshot is visible while it is pending.
      if (listCount > 1) await new Promise((resolve) => setTimeout(resolve, 450));
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ structuredContent: snapshot(app) }) });
      return;
    }
    if (action === 'quota') {
      const id = request.arguments.id;
      const previous = quotas[app]?.[id];
      const used = previous?.tiers?.[0]?.usedPercent ?? 45;
      quotas[app] = { ...quotas[app], [id]: { status: 'ok', available: true, queriedAt: Date.now(), message: null, balances: [],
        tiers: [{ name: 'five_hour', usedPercent: used, resetsAt: new Date(Date.now() + 3 * 3600_000).toISOString() }] } };
      await route.fulfill({ contentType: 'application/json',
        body: JSON.stringify({ structuredContent: { id, app, quota: quotas[app][id] } }) });
      return;
    }
    if (action === 'switch') {
      assert.equal(request.arguments.expectedCurrentId, current[app]);
      assert.ok(request.requestId);
      current[app] = request.arguments.id;
    }
    if (action === 'default_account') {
      const body = snapshot(app);
      body.accounts = body.accounts.map((account) => ({ ...account, isDefault: account.id === request.arguments.id }));
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ structuredContent: body }) });
      return;
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ structuredContent: snapshot(app) }) });
  });

  await page.goto(`http://127.0.0.1:${port}/cache-fixture`, { timeout: 120_000 });
  await expect(page.getByText('main@example.test')).toBeVisible();
  // StrictMode replays the entry effect; the shared in-flight list still runs once.
  assert.equal(listCount, 1);
  // Only the stale and never-queried configs are read automatically.
  const autoIds = actions.filter((action) => action.action === 'quota').map((action) => action.id).sort();
  assert.deepEqual(autoIds, ['p-b', 'p-c']);
  await expect(page.getByText('70%')).toBeVisible();
  await expect(page.getByText('45%')).toBeVisible();
  await expect(page.getByText('30%')).toBeVisible();
  assert.equal(actions.filter((action) => action.action === 'switch').length, 0);
  // No continuous polling: the display clock may tick, request count may not.
  const beforeWait = actions.length;
  await page.waitForTimeout(1200);
  assert.equal(actions.length, beforeWait);
  await page.screenshot({ path: path.join(evidence, 'first-load.png'), fullPage: true });

  // Re-entry: the unmounted panel is remounted while its list is deliberately
  // slow. The cached snapshot must be on screen before that response lands.
  await page.getByRole('link', { name: 'Other' }).click();
  const quotaBeforeReentry = actions.filter((action) => action.action === 'quota').length;
  await page.getByRole('link', { name: 'Back to panel' }).click();
  await page.waitForTimeout(120);
  await expect(page.getByRole('heading', { name: 'Secondary' })).toBeVisible();
  await expect(page.getByText('正在后台更新列表…')).toBeVisible();
  await page.screenshot({ path: path.join(evidence, 'reentry-cached.png'), fullPage: true });
  await expect(page.getByText('正在后台更新列表…')).toBeHidden({ timeout: 5000 });
  assert.equal(actions.filter((action) => action.action === 'quota').length, quotaBeforeReentry);

  // App switch: isolated cache, only the new app's unknown quota is read.
  await page.getByRole('button', { name: 'Claude' }).click();
  await expect(page.getByRole('heading', { name: 'Claude Main' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Main', exact: true })).toHaveCount(0);
  await expect(page.getByText('45%')).toBeVisible();
  assert.equal(actions.filter((action) => action.action === 'quota' && action.app === 'claude').length, 1);
  await page.screenshot({ path: path.join(evidence, 'app-switch.png'), fullPage: true });

  // Back to codex: cached data renders immediately, fresh quotas are not re-read.
  const beforeBack = actions.filter((action) => action.action === 'quota').length;
  await page.getByRole('button', { name: 'Codex' }).click();
  await page.waitForTimeout(120);
  await expect(page.getByRole('heading', { name: 'Secondary' })).toBeVisible();
  await expect(page.getByText('70%')).toBeVisible();
  assert.equal(actions.filter((action) => action.action === 'quota').length, beforeBack);

  // Manual quota always queries, even a fresh one.
  const beforeManualQuota = actions.filter((action) => action.action === 'quota').length;
  await page.getByRole('button', { name: '刷新 Main 额度' }).click();
  await expect.poll(() => actions.filter((action) => action.action === 'quota').length).toBe(beforeManualQuota + 1);
  // Manual list refresh never triggers an automatic quota pass.
  const beforeManualList = actions.filter((action) => action.action === 'quota').length;
  await page.getByRole('button', { name: '刷新列表' }).click();
  await expect(page.getByRole('button', { name: '刷新列表' })).toBeEnabled({ timeout: 5000 });
  assert.equal(actions.filter((action) => action.action === 'quota').length, beforeManualList);
  const lists = () => actions.filter((action) => action.action === 'list' && action.app === 'codex').length;
  const listsBefore = lists();
  assert.ok(listsBefore >= 3);

  // Manual switch keeps the observed current ID and one request ID.
  await page.getByRole('button', { name: '启用配置' }).first().click();
  await expect(page.getByText('当前：Secondary')).toBeVisible();
  const switches = actions.filter((action) => action.action === 'switch');
  assert.equal(switches.length, 1);
  assert.equal(switches[0].id, 'p-b');
  assert.equal(switches[0].expectedCurrentId, 'p-a');
  assert.ok(switches[0].requestId);

  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.locator('section[aria-label="CC Switch"]').evaluate((element) => { element.scrollTop = 0; });
  assert.ok(await page.locator('section[aria-label="CC Switch"]').evaluate((element) => element.scrollWidth <= element.clientWidth));
  await page.screenshot({ path: path.join(evidence, 'mobile.png'), fullPage: true });

  assert.deepEqual(errors, []);
  const result = { passed: true, actions, errors, realCliExecuted: false, backend: 'stateful intercepted API fixture',
    strictModeDedupe: true, firstEntryOnlyStaleQueried: true, reentryCachedBeforeList: true,
    reentryNoQuotaRequests: true, appIsolation: true, appSwitchOnlyNewAppQueried: true,
    manualQuotaAlwaysQueries: true, manualListNoAutoQuota: true, noAutoSwitch: true,
    noPolling: true, manualSwitchExpectedCurrentId: true, mobile: true, port };
  await writeFile(path.join(evidence, 'browser-result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ passed: true, quotaRequests: actions.filter((action) => action.action === 'quota').length,
    switchRequests: switches.length, realCliExecuted: false, evidence }));
} finally {
  await browser?.close();
  await server.close();
}
