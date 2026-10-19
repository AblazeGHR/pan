/* global process, URL, console, document, innerWidth */
/* Real production components in real Chromium. Every API is an intercepted
 * fixture and external navigation is aborted; no cc-switch CLI or user data is
 * touched. */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium, expect } from '@playwright/test';
import { createServer, transformWithEsbuild } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';

const web = fileURLToPath(new URL('..', import.meta.url));
const evidence = path.resolve(web, '../../audit/cc-switch-cli-guidance-20261019');
const repo = 'https://github.com/AblazeGHR/cc-switch-pan';
await mkdir(path.join(evidence, 'tmp'), { recursive: true });
process.env.TEMP = process.env.TMP = path.join(evidence, 'tmp');

const source = `import React from 'react';import {createRoot} from 'react-dom/client';
import {MemoryRouter,Routes,Route} from 'react-router-dom';
import PluginPanelView from '@/views/PluginPanelView';
import '@/index.css';document.documentElement.dataset.theme='dark';
createRoot(document.getElementById('root')).render(<MemoryRouter initialEntries={['/plugins/cc-switch-bridge/accounts']}>
<Routes><Route path="/plugins/:pluginId/:panelId" element={<PluginPanelView/>}/></Routes></MemoryRouter>);`;

const server = await createServer({ configFile: false, root: path.join(web, 'src'),
  cacheDir: path.join(evidence, 'vite-cache'), resolve: { alias: { '@': path.join(web, 'src') } },
  plugins: [react(), tailwind(), { name: 'cc-switch-cli-guidance-fixture',
    resolveId(id) { if (id === 'virtual:cli-guidance-fixture') return '\0cli-guidance-fixture'; },
    async load(id) { if (id === '\0cli-guidance-fixture') return await transformWithEsbuild(source, 'cli-guidance-fixture.tsx', { loader: 'tsx', jsx: 'automatic' }); },
    configureServer(vite) { vite.middlewares.use('/cli-guidance-fixture', async (_req, res) => {
      res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml('/cli-guidance-fixture',
        '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@id/virtual:cli-guidance-fixture"></script></body></html>'));
    }); },
  }], server: { host: '127.0.0.1', port: 0, proxy: {} },
});
await server.listen();
const port = server.httpServer.address().port;

const panel = { id: 'accounts', pluginId: 'cc-switch-bridge', title: 'CC Switch', renderer: 'account-switcher/v1',
  path: '/plugins/cc-switch-bridge/accounts',
  actions: Object.fromEntries(['list', 'quota', 'switch', 'default_account'].map((action) =>
    [action, { capability: 'cc-switch-bridge.' + action, effect: ['switch', 'default_account'].includes(action) ? 'write_external' : 'read' }])) };
const freshQuota = { status: 'ok', available: true, queriedAt: Date.now(), message: null, tiers: [], balances: [] };
const snapshot = () => ({ app: 'codex', currentProviderId: 'p-a', observedAt: Date.now(),
  providers: [{ id: 'p-a', name: 'Main', origin: 'https://api.example.test', isCurrent: true, quota: freshQuota },
    { id: 'p-b', name: 'Secondary', origin: 'https://api.example.test', isCurrent: false, quota: freshQuota }],
  accounts: [{ id: 'acc-a', login: 'main@example.test', isDefault: true },
    { id: 'acc-b', login: 'secondary@example.test', isDefault: false }], accountError: null });
const failed = (code, message) => ({ isError: true, content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { code, message } }) }] });

const externalRequests = [];
const errors = [];
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 960 } });
  await context.route('https://**/*', (route) => {
    externalRequests.push(route.request().url());
    return route.abort();
  });

  async function openScenario(name, respond) {
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(error.message));
    const actions = [];
    await page.route('**/api/**', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/plugins/panels') {
        await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ panels: [panel] }) });
        return;
      }
      const action = url.pathname.split('/').at(-1);
      const request = route.request().postDataJSON();
      actions.push({ action, app: request.arguments.app ?? null, id: request.arguments.id ?? null,
        expectedCurrentId: request.arguments.expectedCurrentId ?? null, requestId: request.requestId ?? null });
      const result = await respond(action, request, actions);
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
    });
    await page.goto(`http://127.0.0.1:${port}/cli-guidance-fixture?scenario=${name}`, { timeout: 120_000 });
    return { page, actions };
  }

  const missing = await openScenario('list-missing', (action) => action === 'list' || action === 'quota'
    ? failed('cli_not_found', '未找到 GUI Fork 的 cc-switch 入口；请安装或在插件 settings.json 配置 executable')
    : { structuredContent: snapshot() });
  await expect(missing.page.getByRole('heading', { name: '未找到 cc-switch CLI' })).toBeVisible();
  await expect(missing.page.getByText(/Pan 不捆绑 CLI，也不会自动下载或安装/)).toBeVisible();
  await expect(missing.page.getByText(/读取列表失败/)).toHaveCount(0);
  const attributes = await missing.page.getByRole('link', { name: /AblazeGHR\/cc-switch-pan/ }).evaluate((link) => ({
    href: link.getAttribute('href'), target: link.getAttribute('target'), rel: link.getAttribute('rel') }));
  assert.deepEqual(attributes, { href: repo, target: '_blank', rel: 'noopener noreferrer' });
  await missing.page.screenshot({ path: path.join(evidence, 'missing-cli.png'), fullPage: true });

  const popupPromise = missing.page.waitForEvent('popup');
  await missing.page.getByRole('link', { name: /AblazeGHR\/cc-switch-pan/ }).click();
  const popup = await popupPromise;
  await expect.poll(() => externalRequests.includes(repo)).toBe(true);
  await popup.close();
  assert.equal(missing.actions.filter((action) => action.action === 'switch').length, 0);
  assert.equal(missing.actions.filter((action) => action.action === 'quota').length, 0);

  await missing.page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await missing.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  const section = missing.page.locator('section[aria-label="CC Switch"]');
  assert.ok(await section.evaluate((element) => element.scrollWidth <= element.clientWidth));
  await missing.page.screenshot({ path: path.join(evidence, 'missing-cli-mobile.png'), fullPage: true });
  await missing.page.close();

  const quota = await openScenario('quota-unavailable', (action) => action === 'quota'
    ? failed('cli_unavailable', '无法启动已配置的 cc-switch CLI') : { structuredContent: snapshot() });
  await expect(quota.page.getByText('main@example.test')).toBeVisible();
  await quota.page.getByRole('button', { name: '刷新 Main 额度' }).click();
  await expect(quota.page.getByRole('heading', { name: '无法启动 cc-switch CLI' })).toBeVisible();
  await expect(quota.page.getByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toBeVisible();
  await quota.page.screenshot({ path: path.join(evidence, 'quota-unavailable.png'), fullPage: true });
  await quota.page.getByRole('button', { name: '刷新列表' }).click();
  await expect(quota.page.getByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toHaveCount(0);
  await expect(quota.page.getByText(/额度刷新失败：无法启动已配置的 cc-switch CLI/)).toBeVisible();
  await quota.page.close();

  const write = await openScenario('write-unavailable', (action) => action === 'switch'
    ? failed('cli_unavailable', '无法启动已配置的 cc-switch CLI') : { structuredContent: snapshot() });
  await expect(write.page.getByText('main@example.test')).toBeVisible();
  await write.page.getByRole('button', { name: '启用配置' }).first().click();
  await expect(write.page.getByRole('heading', { name: '无法启动 cc-switch CLI' })).toBeVisible();
  await expect(write.page.getByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toBeVisible();
  await expect(write.page.getByText(/切换结果尚未确认/)).toBeVisible();
  assert.equal(await write.page.getByRole('button', { name: '启用配置' }).first().isDisabled(), true);
  await write.page.screenshot({ path: path.join(evidence, 'write-unavailable.png'), fullPage: true });
  await write.page.getByRole('button', { name: '刷新列表' }).click();
  await expect(write.page.getByRole('link', { name: /AblazeGHR\/cc-switch-pan/ })).toHaveCount(0);
  await expect(write.page.getByRole('button', { name: '启用配置' }).first()).toBeEnabled();
  await write.page.close();

  assert.deepEqual(errors, []);
  const result = { passed: true, externalRequests, errors, realCliExecuted: false,
    backend: 'intercepted isError fixtures for cli_not_found / cli_unavailable',
    linkAttributes: attributes, linkClickedAndIntercepted: true, mobile: true,
    scenarios: {
      listMissing: missing.actions,
      quotaUnavailable: quota.actions,
      writeUnavailable: write.actions,
    }, port };
  await writeFile(path.join(evidence, 'browser-result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ passed: true, scenarios: result.scenarios, evidence }));
} finally {
  await browser?.close();
  await server.close();
}
