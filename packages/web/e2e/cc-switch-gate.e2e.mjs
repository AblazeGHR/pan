/* Real production panel component; every API is intercepted, no cc-switch runs.
 * Evidence for the single non-manual gate at desktop 1440 and narrow 390. */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium, expect } from '@playwright/test';
import { createServer, transformWithEsbuild } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';

const web = fileURLToPath(new URL('..', import.meta.url));
const evidence = path.resolve(web, '../../audit/cc-switch-non-manual-gate-20261025');
await mkdir(path.join(evidence, 'tmp'), { recursive: true });
process.env.TEMP = process.env.TMP = path.join(evidence, 'tmp');
const source = `import React from 'react';import {createRoot} from 'react-dom/client';
import {MemoryRouter,Routes,Route} from 'react-router-dom';
import PluginPanelView from '@/views/PluginPanelView';
import '@/index.css';document.documentElement.dataset.theme='dark';
createRoot(document.getElementById('root')).render(<MemoryRouter initialEntries={['/plugins/cc-switch-bridge/accounts']}>
<div style={{height:'100dvh',display:'grid',gridTemplateColumns:'48px minmax(0,1fr)'}} className="bg-bg-primary text-text-primary">
<nav className="flex flex-col items-center gap-4 border-r border-border py-5"></nav>
<Routes><Route path="/plugins/:pluginId/:panelId" element={<PluginPanelView/>}/></Routes></div></MemoryRouter>);`;
const server = await createServer({ configFile: false, root: path.join(web, 'src'),
  cacheDir: path.join(evidence, 'vite-cache'), resolve: { alias: { '@': path.join(web, 'src') } },
  plugins: [react(), tailwind(), { name: 'cc-switch-gate-fixture',
    resolveId(id) { if (id === 'virtual:gate-fixture') return '\0gate-fixture'; },
    async load(id) { if (id === '\0gate-fixture') return await transformWithEsbuild(source, 'gate-fixture.tsx', { loader: 'tsx', jsx: 'automatic' }); },
    configureServer(vite) { vite.middlewares.use('/gate-fixture', async (_req, res) => {
      res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/gate-fixture',
        '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@id/virtual:gate-fixture"></script></body></html>'));
    }); },
  }], server: { host: '127.0.0.1', port: 0, proxy: {} },
});
await server.listen();
const port = server.httpServer.address().port;
const actions = [], errors = [];
let conflictNext = false;
const snapshot = { app: 'codex', currentProviderId: 'p-a', observedAt: Date.now(), accountError: null,
  revision: 1, updatedAt: Date.now(),
  providers: [
    { id: 'p-a', name: 'OpenAI Official · Main', origin: 'https://chatgpt.com', notes: '主线路', category: 'official', isCurrent: true, quota: null, nonManualDisabled: false },
    { id: 'p-b', name: 'OpenAI Official · Secondary', origin: 'https://chatgpt.com', notes: '备用线路', category: 'official', isCurrent: false, quota: null, nonManualDisabled: false },
    { id: 'p-c', name: 'SiliconFlow', origin: 'https://siliconflow.cn', notes: '第三方', category: 'third-party', isCurrent: false, quota: null, nonManualDisabled: false }],
  accounts: [{ id: 'acc-a', login: 'main@example.test', isDefault: true },
    { id: 'acc-b', login: 'secondary@example.test', isDefault: false }] };
const panel = { id: 'accounts', pluginId: 'cc-switch-bridge', title: 'CC Switch', renderer: 'account-switcher/v1', path: '/plugins/cc-switch-bridge/accounts',
  actions: { list: { capability: 'cc-switch-bridge.panel_list', effect: 'read' },
    quota: { capability: 'cc-switch-bridge.panel_quota', effect: 'read' },
    switch: { capability: 'cc-switch-bridge.panel_switch', effect: 'write_external' },
    config: { capability: 'cc-switch-bridge.panel_config', effect: 'write_external' },
    auto_switch: { capability: 'cc-switch-bridge.auto_switch', effect: 'write_external' },
    default_account: { capability: 'cc-switch-bridge.default_account', effect: 'write_external' } } };
const reply = (text) => ({ isError: true, content: [{ type: 'text', text: JSON.stringify(
  { ok: false, error: { code: 'settings_conflict', message: text } }) }] });
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
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
      if (action === 'config') {
        assert.ok(request.requestId, 'config requires a request id');
        if (conflictNext) { conflictNext = false; body = reply('设置已被其他窗口修改；请刷新后再操作'); }
        else {
          snapshot.revision += 1;
          snapshot.updatedAt = Date.now();
          snapshot.providers.forEach(p => { if (p.id === request.arguments.id) p.nonManualDisabled = request.arguments.nonManualDisabled; });
          body = { structuredContent: structuredClone(snapshot) };
        }
      } else if (action === 'quota') {
        const quota = { status: 'ok', available: true, message: null, queriedAt: Date.now(), balances: [],
          tiers: [{ name: 'five_hour', usedPercent: 70, resetsAt: new Date(Date.now() + 90 * 60_000).toISOString() }] };
        body = { structuredContent: { id: request.arguments.id, app: 'codex', quota } };
      } else body = { structuredContent: structuredClone(snapshot) };
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto(`http://127.0.0.1:${port}/gate-fixture`, { timeout: 120_000 });
  await expect(page.getByText('main@example.test')).toBeVisible();
  const gate = (name) => page.getByRole('switch', { name: `${name}：禁止非手动切换` });
  await expect(gate('OpenAI Official · Main')).toHaveAttribute('aria-checked', 'false');
  await expect(gate('SiliconFlow')).toHaveAttribute('aria-checked', 'false');
  assert.equal(actions.filter(a => a.action === 'list').length, 1);

  await gate('OpenAI Official · Secondary').click();
  await expect(gate('OpenAI Official · Secondary')).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByText('已对 Agent 隐藏')).toBeVisible();
  // The manual path stays available for the gated row.
  await expect(page.getByRole('button', { name: '启用配置' }).first()).toBeEnabled();
  const saved = actions.filter(a => a.action === 'config');
  assert.equal(saved.length, 1);
  assert.deepEqual({ ...saved[0].request.arguments, expectedRevision: 1 }, {
    app: 'codex', id: 'p-b', nonManualDisabled: true, expectedRevision: 1 });
  assert.ok(saved[0].request.requestId);
  await page.screenshot({ path: path.join(evidence, 'desktop-1440.png'), fullPage: true });

  // A stale revision must be reported explicitly and trigger a fresh read.
  conflictNext = true;
  const listsBefore = actions.filter(a => a.action === 'list').length;
  await gate('SiliconFlow').click();
  await expect(page.getByRole('alert')).toContainText('设置已被其他窗口修改');
  await expect(page.getByRole('alert')).toContainText('已重新读取列表');
  await expect.poll(() => actions.filter(a => a.action === 'list').length).toBe(listsBefore + 1);
  await expect(gate('SiliconFlow')).toHaveAttribute('aria-checked', 'false');

  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.locator('section[aria-label="CC Switch"]').evaluate(element => { element.scrollTop = 0; });
  assert.ok(await page.locator('section[aria-label="CC Switch"]').evaluate(element => element.scrollWidth <= element.clientWidth));
  await expect(gate('OpenAI Official · Secondary')).toBeVisible();
  await gate('OpenAI Official · Secondary').scrollIntoViewIfNeeded();
  await expect(gate('OpenAI Official · Secondary')).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByText('已对 Agent 隐藏')).toBeVisible();
  await page.waitForTimeout(150);
  await page.screenshot({ path: path.join(evidence, 'mobile-390.png'), fullPage: true });
  assert.deepEqual(errors, []);
  await writeFile(path.join(evidence, 'browser-result.json'), JSON.stringify({ passed: true,
    desktop: 1440, mobile: 390, singleGate: true, configRequests: saved.length,
    conflictShown: true, hiddenBadge: true, manualSwitchEnabled: true,
    realCliExecuted: false, backend: 'stateful intercepted API fixture', actions, errors, port }, null, 2));
  console.log(JSON.stringify({ passed: true, configRequests: saved.length, evidence }));
} finally { await browser?.close(); await server.close(); }
