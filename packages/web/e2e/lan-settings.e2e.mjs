/* global document, navigator, URL, console */
import assert from 'node:assert/strict';
import { writeFileSync, unlinkSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { chromium } from '@playwright/test';

const root = fileURLToPath(new URL('../src/', import.meta.url));
const name = '.lan-settings-harness';
const html = `${root}${name}.html`;
const tsx = `${root}${name}.tsx`;
writeFileSync(html, `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head><body><div id="root"></div><script type="module" src="/${name}.tsx"></script></body></html>`);
writeFileSync(tsx, `import { createRoot } from 'react-dom/client'; import { LanSettings } from './components/layout/LanSettings'; import './index.css'; createRoot(document.getElementById('root')!).render(<main style={{padding:16,maxWidth:640}}><LanSettings /></main>);`);
const vite = await createServer({ configFile: false, root, plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': root } }, optimizeDeps: { entries: [html] },
  server: { host: '127.0.0.1', port: 0, strictPort: true, proxy: {} } });
let browser;
try {
  await vite.listen();
  const port = vite.httpServer.address().port;
  assert.ok(![8767, 8768].includes(port));
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 320, height: 844 } });
  const errors = [], protectedRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (/:(8767|8768)(\/|$)/.test(request.url())) protectedRequests.push(request.url()); });
  let config = { startupEnabled: false, host: '', port: 8780 };
  let activeConfig = null, running = false, error = '';
  let failLoad = true, failSave = true, failStart = true, puts = 0;
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    assert.ok(path.startsWith('/api/remote/lan'));
    if (request.method() === 'GET' && failLoad) {
      failLoad = false;
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'LAN 状态暂不可用' }) });
    }
    if (request.method() === 'PUT') {
      puts++;
      if (failSave) error = '配置写入失败，原配置保留';
      else { config = request.postDataJSON(); error = ''; }
    } else if (path.endsWith('/start')) {
      if (failStart) error = '端口冲突，LAN 开启失败';
      else { running = true; activeConfig = { ...config }; error = ''; }
    } else if (path.endsWith('/stop')) { running = false; activeConfig = null; error = ''; }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      config, activeConfig, running, addresses: ['192.168.1.5', '100.64.1.2'],
      urls: running ? [`http://${activeConfig.host}:${activeConfig.port}`] : [], error,
    }) });
  });
  await page.goto(`http://127.0.0.1:${port}/${name}.html`);
  await page.getByRole('alert').filter({ hasText: 'LAN 状态暂不可用' }).waitFor();
  await page.getByRole('button', { name: '刷新 LAN 状态' }).click();
  const start = page.getByRole('button', { name: '开启 LAN 访问' });
  const save = page.getByRole('button', { name: '保存 LAN 配置' });
  const host = page.getByLabel('LAN 本机监听地址');
  const inputPort = page.getByLabel('LAN 入口端口');
  await host.waitFor();
  assert.ok(await start.isDisabled());
  await host.selectOption('192.168.1.5');
  await inputPort.fill('0'); assert.ok(await save.isDisabled());
  await inputPort.fill('65536'); assert.ok(await save.isDisabled());
  await inputPort.fill('18780');
  await page.getByRole('checkbox').check();
  await save.click();
  await page.getByRole('alert').filter({ hasText: '配置写入失败' }).waitFor();
  assert.equal(await inputPort.inputValue(), '18780');
  assert.equal(await host.inputValue(), '192.168.1.5');
  assert.ok(await start.isDisabled());
  failSave = false; await save.click();
  await page.waitForFunction(() => !Array.from(document.querySelectorAll('button')).find(button => button.textContent === '开启 LAN 访问').disabled);
  assert.equal(puts, 2);
  assert.equal(config.startupEnabled, true);
  await start.click();
  await page.getByRole('alert').filter({ hasText: '端口冲突' }).waitFor();
  assert.equal(await page.locator('code').count(), 0);
  failStart = false; await start.click();
  await page.getByText('http://192.168.1.5:18780', { exact: true }).waitFor();
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined }));
  await page.getByRole('button', { name: '复制 LAN 地址' }).click();
  await page.getByRole('alert').filter({ hasText: '复制失败' }).waitFor();
  for (const width of [320, 390, 1280]) {
    await page.setViewportSize({ width, height: 844 });
    const size = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth, content: document.documentElement.scrollWidth }));
    assert.ok(size.content <= size.viewport, JSON.stringify(size));
    assert.ok(await page.getByRole('button', { name: '关闭 LAN 访问' }).isVisible());
  }
  await page.getByRole('button', { name: '关闭 LAN 访问' }).click();
  await page.waitForFunction(() => !document.querySelector('code'));
  await host.selectOption('0.0.0.0');
  assert.ok(await host.locator('option:checked').textContent().then(value => value.includes('所有接口')));
  assert.deepEqual(errors, []);
  assert.deepEqual(protectedRequests, []);
  console.log('LAN Chromium: save failure retained, invalid ports blocked, start failure honest, active URL/stop/copy failure, 320/390/1280px no overflow; no protected requests');
} finally {
  await browser?.close();
  await vite.close();
  unlinkSync(html); unlinkSync(tsx);
}
