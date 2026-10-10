/* Real Chromium, real components/store/API; private Vite and mocked HTTP responses. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { chromium } from '@playwright/test';

const evidence = path.resolve('../../audit/codex-reimport-confirmation/browser');
await fs.mkdir(evidence, { recursive: true });
const port = await new Promise((resolve, reject) => {
  const server = net.createServer();
  server.on('error', reject);
  server.listen(0, '127.0.0.1', () => {
    const selected = server.address().port;
    server.close(() => resolve(selected));
  });
});
assert(![8767, 8768].includes(port));
const vite = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
  { cwd: process.cwd(), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
let viteLog = '';
vite.stdout.on('data', (data) => { viteLog += data; });
vite.stderr.on('data', (data) => { viteLog += data; });
let browser;
const results = [];
const html = `<!doctype html><html><body><div id="root"></div><script type="module">
import React from 'react';
import { createRoot } from 'react-dom/client';
import { SessionMenu } from '/components/session/SessionMenu.tsx';
import { ReimportConfirmationModal } from '/components/session/ReimportConfirmationModal.tsx';
import { useSessionStore } from '/stores/sessionStore.ts';
import { useUIStore } from '/stores/uiStore.ts';
const target={id:'ses-browser',name:'Browser target',adapter:'codex',cliSessionId:'native-browser',workdir:'C:/private',alwaysThinkingEnabled:false,effort:'',workspaceIds:[],history:[{role:'user',content:'old protected history'}]};
useSessionStore.setState({sessions:[target],currentSessionId:target.id});
useUIStore.setState({activeWorkspaceId:'all'});
window.readHistory=()=>useSessionStore.getState().sessions[0].history;
function Harness(){const [menu,setMenu]=React.useState(true);return React.createElement(React.Fragment,null,
menu?React.createElement(SessionMenu,{session:target,position:{x:10,y:10},onClose:()=>setMenu(false)}):null,
React.createElement(ReimportConfirmationModal));}
createRoot(document.getElementById('root')).render(React.createElement(Harness));
</script></body></html>`;
const harnessPath = path.resolve('src/.reimport-confirmation-harness.tsx');
await fs.writeFile(harnessPath, html.match(/<script type="module">([\s\S]*)<\/script>/)[1]);
const harnessHtml = `<!doctype html><html><head><link rel="stylesheet" href="/index.css"></head><body><div id="root"></div>
<script type="module">import RefreshRuntime from '/@react-refresh';
RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;
window.__vite_plugin_react_preamble_installed__=true;</script>
<script type="module" src="/@vite/client"></script>
<script type="module" src="/.reimport-confirmation-harness.tsx"></script></body></html>`;

try {
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Private Vite did not become ready: ' + viteLog)), 15000);
    const ready = () => { if (viteLog.includes('Local:')) { clearTimeout(timeout); resolve(); } };
    vite.stdout.on('data', ready);
    vite.once('exit', (code) => { clearTimeout(timeout); reject(new Error('Vite exited: ' + code)); });
    ready();
  });
  browser = await chromium.launch({ headless: true });
  for (const scenario of ['cancel', 'confirm', 'stale', 'failure']) {
    const context = await browser.newContext({ viewport: { width: 1100, height: 850 } });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const errors = [];
    const requests = [];
    const protectedRequests = [];
    page.on('pageerror', (error) => { errors.push(error.message); console.error(error.message); });
    page.on('request', (request) => { if (/:(8767|8768)\//.test(request.url())) protectedRequests.push(request.url()); });
    await page.route('**/__reimport-harness', (route) => route.fulfill({ contentType: 'text/html', body: harnessHtml }));
    await page.route('**/api/**', async (route) => {
      if (!route.request().url().endsWith('/api/adapters/codex/sessions/import')) {
        await route.fulfill({ json: { ok: true } });
        return;
      }
      const body = route.request().postDataJSON();
      requests.push(body);
      const stale = scenario === 'stale' && requests.length === 2;
      if (!body.force || stale) {
        await route.fulfill({ json: {
          ok: false, error: 'origin conflict', errorCode: stale ? 'reimport_stale' : 'reimport_origin_conflict',
          conflict: { sessionId: 'ses-browser', historyRevision: stale ? 8 : 7, count: stale ? 2 : 1,
            confirmationToken: stale ? 'fresh-token' : 'first-token', stale, omittedCount: 0,
            items: [{ position: 12, source: 'agent', reason: 'missing_or_changed', preview: 'Missing message preview' },
              ...(stale ? [{ position: 14, source: 'user', reason: 'ambiguous_match', preview: 'Updated conflict preview' }] : [])] },
        } });
      } else if (scenario === 'failure') {
        await route.fulfill({ status: 500, body: 'Internal Server Error' });
      } else {
        await route.fulfill({ json: { id: 'ses-browser', name: 'Browser target', adapter: 'codex', workspaceIds: [],
          history: [{ role: 'user', content: 'native replacement', messageId: 'pan:matched', source: 'agent' }] } });
      }
    });
    await page.goto(`http://127.0.0.1:${port}/__reimport-harness`);
    await page.getByRole('button', { name: 'Reimport', exact: true }).click();
    await page.getByRole('dialog').waitFor();
    assert.equal(await page.getByText('Missing message preview', { exact: true }).count(), 1);
    assert.equal(await page.getByText(/无法匹配的 Pan 消息/).count(), 1);
    assert.equal(requests.length, 1);
    if (scenario === 'cancel') {
      await page.screenshot({ path: path.join(evidence, 'summary.png') });
      await page.getByRole('button', { name: '取消', exact: true }).click();
      await page.getByRole('dialog').waitFor({ state: 'hidden' });
      assert.equal(requests.length, 1);
      assert.equal((await page.evaluate(() => window.readHistory()))[0].content, 'old protected history');
    } else {
      await page.getByRole('button', { name: '确认强行覆盖', exact: true }).click();
      if (scenario === 'stale') {
        await page.getByText('Updated conflict preview', { exact: true }).waitFor();
        assert.equal(requests.length, 2);
        assert.equal((await page.evaluate(() => window.readHistory()))[0].content, 'old protected history');
        await page.screenshot({ path: path.join(evidence, 'stale.png') });
        await page.getByRole('button', { name: '确认强行覆盖', exact: true }).click();
        await page.getByRole('dialog').waitFor({ state: 'hidden' });
        assert.equal(requests[2].confirmationToken, 'fresh-token');
      } else if (scenario === 'failure') {
        await page.getByRole('alert').waitFor();
        assert.equal((await page.evaluate(() => window.readHistory()))[0].content, 'old protected history');
        await page.screenshot({ path: path.join(evidence, 'failure.png') });
      } else {
        await page.getByRole('dialog').waitFor({ state: 'hidden' });
        assert.equal((await page.evaluate(() => window.readHistory()))[0].content, 'native replacement');
      }
      assert.equal(requests[1].panSessionId, 'ses-browser');
      assert.equal(requests[1].confirmationToken, 'first-token');
      assert.equal(requests[1].force, true);
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(protectedRequests, []);
    results.push({ scenario, requests: requests.length, passed: true });
    await context.close();
  }
  await fs.writeFile(path.join(evidence, 'result.json'), JSON.stringify({ browser: browser.version(), port, results, layer: 'real Chromium components and store; mocked import HTTP' }, null, 2));
  console.log(JSON.stringify(results));
} finally {
  await browser?.close();
  vite.kill();
  if (vite.exitCode === null) await new Promise((resolve) => vite.once('exit', resolve));
  await fs.unlink(harnessPath);
  await fs.writeFile(path.join(evidence, 'vite.log'), viteLog);
}
