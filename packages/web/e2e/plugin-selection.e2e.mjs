/* Real Chromium interaction with the production Manage panel; isolated API fixture. */
/* global document, innerWidth */
import process from 'node:process';
import console from 'node:console';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath, URL } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium, expect } from '@playwright/test';
import { createServer, transformWithEsbuild } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';

const web = fileURLToPath(new URL('..', import.meta.url));
const evidence = path.resolve(web, '../../audit/plugin-platform-20261018/browser');
await mkdir(evidence, { recursive: true });
const temporary = path.join(evidence, 'tmp');
await mkdir(temporary, { recursive: true });
process.env.TEMP = temporary;
process.env.TMP = temporary;
const entry = '\0plugin-acceptance';
const source = `import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ManageSessionsPanel} from '@/components/session/ManageModal';
import {useSessionStore} from '@/stores/sessionStore';
import '@/index.css';
const rows=['ses_a','ses_b'].map(id=>({id,name:id,history:[],mcpServers:['qq'],managed:[]}));
useSessionStore.setState({sessions:rows,loadSessions:async()=>{}});
function App(){const [id,setId]=useState('ses_a');return <main style={{maxWidth:900,margin:'auto'}}>
<button onClick={()=>setId('ses_a')}>Session A</button><button onClick={()=>setId('ses_b')}>Session B</button>
<ManageSessionsPanel open sessionId={id}/></main>};
createRoot(document.getElementById('root')).render(<App/>);`;
const server = await createServer({
  configFile: false, root: path.join(web, 'src'), cacheDir: path.join(evidence, 'vite-cache'),
  resolve: { alias: { '@': path.join(web, 'src') } },
  plugins: [react(), tailwind(), {
    name: 'plugin-acceptance-harness',
    resolveId(id) { if (id === 'virtual:plugin-acceptance') return entry; },
    async load(id) { if (id === entry) return await transformWithEsbuild(source, 'plugin-acceptance.tsx', { loader: 'tsx', jsx: 'automatic' }); },
    configureServer(vite) {
      vite.middlewares.use('/plugin-acceptance', async (_req, res) => {
        res.setHeader('Content-Type', 'text/html');
        res.end(await vite.transformIndexHtml('/plugin-acceptance', '<html><body><div id="root"></div><script type="module" src="/@id/virtual:plugin-acceptance"></script></body></html>'));
      });
    },
  }], server: { host: '127.0.0.1', port: 0, proxy: {} },
});
await server.listen();
const port = server.httpServer.address().port;
let browser;
const writes = [], errors = [];
const states = Object.fromEntries(['ses_a', 'ses_b'].map(id => [id, {
  id, name: id, history: [], managed: [], mcpServers: ['qq'], mcpLocked: false,
  pluginSelectionRevision: 0, pluginBindings: [{ plugin_id: 'qq', enabled: true, mode: 'standalone' }],
}]));
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  await page.route('https://**/*', route => route.abort());
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    let body = {};
    if (url.pathname === '/api/mcp/servers') body = { loaded: true, servers: [
      { name: 'pan', supportedModes: ['standalone'] },
      { name: 'qq', aliases: ['pan-qq'], supportedModes: ['standalone', 'pan_native', 'gateway'], defaultMode: 'standalone' },
    ] };
    else if (url.pathname.startsWith('/api/sessions/')) {
      const id = url.pathname.split('/')[3];
      body = states[id] ?? {};
      if (req.method() === 'PATCH') {
        const patch = req.postDataJSON();
        assert.equal(patch.expectedPluginRevision, body.pluginSelectionRevision);
        assert.ok(!('mcpServers' in patch));
        writes.push({ id, patch });
        Object.assign(body, { pluginBindings: patch.pluginBindings,
          pluginSelectionRevision: body.pluginSelectionRevision + 1,
          mcpServers: patch.pluginBindings.filter(b => b.enabled).map(b => b.plugin_id),
          pluginApplyStatus: patch.pluginBindings.some(b => b.enabled && b.mode === 'pan_native') ? 'pending' : 'applied' });
      }
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto(`http://127.0.0.1:${port}/plugin-acceptance`);
  await page.getByRole('tab', { name: 'MCP and Plugins' }).click();
  const save = async action => {
    const response = page.waitForResponse(r => r.request().method() === 'PATCH');
    await action();
    await response;
    await expect(page.getByLabel('qq mode')).toBeEnabled();
  };
  await save(() => page.getByLabel('qq mode').selectOption('pan_native'));
  assert.equal(states.ses_a.pluginBindings[0].mode, 'pan_native');
  assert.equal(await page.getByRole('checkbox', { name: 'pan', exact: true }).isChecked(), false);
  await save(() => page.getByRole('checkbox', { name: 'qq', exact: true }).click());
  await expect(page.getByRole('checkbox', { name: 'qq', exact: true })).not.toBeChecked();
  assert.equal(await page.getByLabel('qq mode').inputValue(), 'pan_native');
  await save(() => page.getByRole('checkbox', { name: 'qq', exact: true }).click());
  await expect(page.getByRole('checkbox', { name: 'qq', exact: true })).toBeChecked();
  await page.getByRole('button', { name: 'Session B' }).click();
  await page.getByRole('tab', { name: 'MCP and Plugins' }).click();
  assert.equal(await page.getByLabel('qq mode').inputValue(), 'standalone');
  await save(() => page.getByLabel('qq mode').selectOption('gateway'));
  assert.equal(states.ses_a.pluginBindings[0].mode, 'pan_native');
  assert.equal(states.ses_b.pluginBindings[0].mode, 'gateway');
  await page.screenshot({ path: path.join(evidence, 'desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: path.join(evidence, 'mobile.png'), fullPage: true });
  assert.deepEqual(errors, []);
  await writeFile(path.join(evidence, 'result.json'), JSON.stringify({ port, writes, errors, desktop: true, mobile: true,
    productionComponent: 'ManageSessionsPanel', backend: 'isolated stateful API fixture' }, null, 2));
  console.log(JSON.stringify({ passed: true, writes: writes.length, port, evidence }));
} finally {
  await browser?.close();
  await server.close();
}
