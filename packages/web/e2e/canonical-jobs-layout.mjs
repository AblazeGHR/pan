/* global console, URL, document */
// Builds an isolated UI fixture and serves every browser request via Playwright route.fulfill.
// No HTTP listener, Pan process, real profile, or protected port is used.
import { build } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { chromium } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.resolve(web, '../../artifacts/wp07-ui');
await mkdir(out, { recursive: true });
const fixture = path.join(out, 'fixture');
const src = path.join(web, 'src').replaceAll('\\', '/');
await build({
  configFile: false,
  root: web,
  logLevel: 'warn',
  resolve: { alias: { '@': src } },
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'canonical-fixture',
      resolveId(id) {
        if (id === 'canonical-fixture') return '\0canonical-fixture';
      },
      load(id) {
        if (id === '\0canonical-fixture')
          return `import React from 'react'; import {createRoot} from 'react-dom/client'; import {MemoryRouter} from 'react-router-dom'; import View from '${src}/views/CanonicalJobsView.tsx'; import {useAutomationAuth} from '${src}/stores/automationStore.ts'; import '${src}/index.css'; useAutomationAuth.setState({token:'fixture',expiresAt:Date.now()+600000}); createRoot(document.getElementById('root')).render(<MemoryRouter><View/></MemoryRouter>);`;
      },
      transform(code, id) {
        if (id === '\0canonical-fixture')
          return {
            code: code.replace(
              "createRoot(document.getElementById('root')).render(<MemoryRouter><View/></MemoryRouter>);",
              "createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter,null,React.createElement(View)));",
            ),
          };
      },
    },
  ],
  build: {
    outDir: fixture,
    emptyOutDir: true,
    rollupOptions: {
      input: 'canonical-fixture',
      output: { entryFileNames: 'harness.js', assetFileNames: '[name][extname]' },
    },
  },
});
const browser = await chromium.launch({ headless: true });
const results = [];
const item = {
  id: 'job_' + 'long'.repeat(40),
  name: 'VeryLongJobName'.repeat(10),
  state: 'draft',
  configRevision: 4,
  stateRevision: 2,
  steps: [],
  retention: { policyRef: 'default' },
};
try {
  for (const width of [390, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 1000 } });
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.route('https://canonical.test/**', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/api/')) {
        const body = url.pathname.includes('/runs')
          ? {
              items: [
                {
                  runId: 'run_' + 'r'.repeat(140),
                  jobId: item.id,
                  state: 'failed',
                  stateRevision: 2,
                  configRevision: 4,
                },
              ],
              nextCursor: null,
            }
          : { items: [item], nextCursor: null };
        return route.fulfill({ json: body });
      }
      if (url.pathname === '/')
        return route.fulfill({
          contentType: 'text/html',
          body: '<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/_canonical-fixture.css"><div id="root" style="height:100vh;display:flex;flex-direction:column;background:var(--color-bg-primary)"></div><script type="module" src="/harness.js"></script>',
        });
      const filename = path.resolve(fixture, '.' + url.pathname);
      assert.ok(filename.startsWith(fixture + path.sep));
      return route.fulfill({
        body: await readFile(filename),
        contentType: filename.endsWith('.css') ? 'text/css' : 'text/javascript',
      });
    });
    await page.goto('https://canonical.test/');
    await page.getByRole('heading', { name: 'Canonical Jobs', exact: true, level: 1 }).waitFor();
    await page.getByRole('button', { name: `编辑 ${item.name}`, exact: true }).waitFor();
    const measure = async (state) => {
      const overflow = await page.evaluate(() =>
        [...document.querySelectorAll('body,section,article,form,nav,header')]
          .filter((e) => e.scrollWidth > e.clientWidth + 1)
          .map((e) => ({ tag: e.tagName, width: e.clientWidth, scroll: e.scrollWidth })),
      );
      assert.deepEqual(overflow, [], `${width} ${state} overflow`);
      results.push({ width, state, overflow });
    };
    await measure('list');
    await page.getByRole('button', { name: `编辑 ${item.name}`, exact: true }).click();
    await page.getByText('定义字段与约束', { exact: true }).click();
    await measure('editor-schema');
    await page.screenshot({ path: path.join(out, `${width}-editor.png`), fullPage: true });
    await page.getByRole('button', { name: '关闭编辑', exact: true }).click();
    await page.getByRole('button', { name: `Runs ${item.name}`, exact: true }).click();
    await page.getByRole('button', { name: 'clone-and-run', exact: true }).click();
    await measure('runs-operation');
    await page.screenshot({ path: path.join(out, `${width}-runs.png`), fullPage: true });
    assert.deepEqual(errors, []);
    await page.close();
  }
  await writeFile(
    path.join(out, 'browser.json'),
    JSON.stringify({ mode: 'mocked canonical API, route.fulfill, no listener', results }, null, 2),
  );
  console.log(JSON.stringify(results));
} finally {
  await browser.close();
}
