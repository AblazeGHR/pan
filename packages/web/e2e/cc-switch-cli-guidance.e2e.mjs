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
const failure = { isError: true, content: [{ type: 'text', text: JSON.stringify({ ok: false,
  error: { code: 'cli_not_found', message: '未找到 GUI Fork 的 cc-switch 入口；请安装或在插件 settings.json 配置 executable' } }) }] };

const actions = [];
const externalRequests = [];
const errors = [];
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 960 } });
  const page = await context.newPage();
  page.on('pageerror', (error) => errors.push(error.message));
  await context.route('https://**/*', (route) => {
    externalRequests.push(route.request().url());
    return route.abort();
  });
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/plugins/panels') {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ panels: [panel] }) });
      return;
    }
    const action = url.pathname.split('/').at(-1);
    const request = route.request().postDataJSON();
    actions.push({ action, app: request.arguments.app ?? null, id: request.arguments.id ?? null });
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(failure) });
  });

  await page.goto(`http://127.0.0.1:${port}/cli-guidance-fixture`, { timeout: 120_000 });
  await expect(page.getByRole('heading', { name: '未找到 cc-switch CLI' })).toBeVisible();
  await expect(page.getByText(/Pan 不捆绑 CLI，也不会自动下载或安装/)).toBeVisible();
  await expect(page.getByText(/读取列表失败/)).toHaveCount(0);
  const attributes = await page.getByRole('link', { name: /AblazeGHR\/cc-switch-pan/ }).evaluate((link) => ({
    href: link.getAttribute('href'), target: link.getAttribute('target'), rel: link.getAttribute('rel') }));
  assert.deepEqual(attributes, { href: repo, target: '_blank', rel: 'noopener noreferrer' });
  await page.screenshot({ path: path.join(evidence, 'missing-cli.png'), fullPage: true });

  const popupPromise = page.waitForEvent('popup');
  await page.getByRole('link', { name: /AblazeGHR\/cc-switch-pan/ }).click();
  const popup = await popupPromise;
  await expect.poll(() => externalRequests.includes(repo)).toBe(true);
  await popup.close();
  assert.equal(actions.filter((action) => action.action === 'switch').length, 0);
  assert.equal(actions.filter((action) => action.action === 'quota').length, 0);

  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  const section = page.locator('section[aria-label="CC Switch"]');
  assert.ok(await section.evaluate((element) => element.scrollWidth <= element.clientWidth));
  await page.screenshot({ path: path.join(evidence, 'missing-cli-mobile.png'), fullPage: true });

  assert.deepEqual(errors, []);
  const result = { passed: true, actions, externalRequests, errors, realCliExecuted: false,
    backend: 'intercepted isError fixture with cli_not_found', linkAttributes: attributes,
    linkClickedAndIntercepted: true, mobile: true, port };
  await writeFile(path.join(evidence, 'browser-result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ passed: true, actions, externalRequests, evidence }));
} finally {
  await browser?.close();
  await server.close();
}
