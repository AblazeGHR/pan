/* global URL, console, process */
/* Real Chromium + production ImportModal; synthetic API metadata only. No Pan server. */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { createServer as createPortProbe } from 'node:net';

const root = fileURLToPath(new URL('../src', import.meta.url));
const output = fileURLToPath(new URL('../test-results/import-cli-id', import.meta.url));
const adapters = ['cbc', 'kimi', 'opencode', 'codex'];
const entry = `
import React from 'react';
import '/index.css';
import { createRoot } from 'react-dom/client';
import { ImportModal } from '/components/session/ImportModal.tsx';
import { useAdapterStore } from '/stores/adapterStore.ts';
useAdapterStore.setState({cliStatus: {adapters: ${JSON.stringify(adapters)}.map(name => ({name, label:name, available:true})), available:${JSON.stringify(adapters)}, hasAvailable:true}, cliStatusLoading:false, loadCliStatus:async()=>{}});
createRoot(document.getElementById('root')).render(<ImportModal open onClose={()=>{}} />);
`;
const probe = createPortProbe();
await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
const candidatePort = probe.address().port;
await new Promise(resolve => probe.close(resolve));
assert.ok(candidatePort !== 8767 && candidatePort !== 8768);
const server = await createServer({
  configFile: false, root, cacheDir: path.join(output, 'vite-cache'),
  plugins: [react(), tailwindcss(), {
    enforce: 'pre',
    name: 'import-fixture',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        if (req.url !== '/fixture') return next();
        res.setHeader('Content-Type', 'text/html');
        res.end(await server.transformIndexHtml('/fixture', '<div id="root"></div><script type="module" src="/fixture.tsx"></script>'));
      });
    },
    resolveId(id) { if (id === '/fixture.tsx') return path.join(root, 'fixture.tsx'); },
    load(id) { if (id === path.join(root, 'fixture.tsx').replaceAll('\\', '/') || id === path.join(root, 'fixture.tsx')) return entry; },
  }],
  resolve: { alias: { '@': root } },
  server: {host:'127.0.0.1', port:candidatePort, strictPort:true, proxy:{}},
});
let browser;
const evidence = [];
const requests = [];
try {
  await mkdir(output, {recursive:true});
  await server.listen();
  const {port} = server.httpServer.address();
  assert.ok(port !== 8767 && port !== 8768);
  browser = await chromium.launch({headless:true});
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => { errors.push(error.message); console.error(error.message); });
  page.on('console', msg => { if (msg.type() === 'error') console.error(msg.text()); });
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    requests.push({method:route.request().method(), path:url.pathname});
    assert.equal(route.request().method(), 'GET', 'fixture must never import');
    let body = {};
    if (url.pathname.endsWith('/cbc/projects')) body = {projects:[{project_dir:'fixture-project', short_label:'Fixture project', drive:'D:', session_count:2}]};
    else if (url.pathname.endsWith('/kimi/workspaces')) body = {workspaces:[{workspace_id:'fixture-ws', root:'D:/fixture', name:'Fixture workspace', session_count:2}]};
    else if (url.pathname.endsWith('/sessions')) {
      const adapter = adapters.find(a => url.pathname.includes('/'+a+'/'));
      assert.ok(adapter, url.pathname);
      body = {sessions:['target','other'].map((suffix) => ({
        session_id:`${adapter}-native-${suffix}-1234`, title:`${adapter} ${suffix} title`,
        project_dir:'fixture-project', workDir:'D:/fixture', message_count:1,
        model:'', first_timestamp:'', last_timestamp:'', updatedAt:'', createdAt:'',
      }))};
    }
    return route.fulfill({json:body});
  });
  await page.goto(`http://127.0.0.1:${port}/fixture`);
  for (const adapter of adapters) {
    await page.getByRole('button', {name:adapter, exact:true}).click();
    if (adapter === 'cbc') await page.getByRole('combobox', {name:/Project/}).selectOption('fixture-project');
    if (adapter === 'kimi') await page.getByRole('combobox', {name:/Workspace/}).selectOption('D:/fixture');
    const target = page.getByText(`${adapter} target title`, {exact:true});
    const other = page.getByText(`${adapter} other title`, {exact:true});
    await target.waitFor();
    const search = page.getByRole('searchbox');
    assert.equal(await search.inputValue(), '', 'adapter switch resets search');
    for (const query of [`  ${adapter}-native-target-1234  `, 'TARGET-1234']) {
      await search.fill(query);
      await target.waitFor();
      assert.equal(await other.count(), 0);
    }
    await page.screenshot({path:path.join(output, `${adapter}-id-match.png`)});
    await search.fill('ses_pan-unrelated');
    assert.equal(await target.count(), 0);
    await page.getByText(/No sessions match/).waitFor();
    await search.fill('');
    await target.waitFor();
    await other.waitFor();
    await search.fill('fixture');
    await target.waitFor();
    await search.fill('no-match-before-switch');
    evidence.push({adapter, fullId:true, partialId:true, trim:true, clear:true, noMatch:true, path:true, switchReset:true});
  }
  await page.getByRole('button', {name:'cbc', exact:true}).click();
  await page.getByRole('combobox', {name:/Project/}).selectOption('fixture-project');
  assert.equal(await page.getByRole('searchbox').inputValue(), '');
  assert.deepEqual(errors, []);
  await writeFile(path.join(output,'result.json'), JSON.stringify({port, pid:process.pid, evidence, requests, errors, boundary:'Real Chromium; production ImportModal; fixture metadata; no real CLI discovery or import'}, null,2));
  console.log(JSON.stringify({port, pid:process.pid, evidence, requests:requests.length, errors},null,2));
} finally {
  await browser?.close();
  await server.close();
}
