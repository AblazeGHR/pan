import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from '@playwright/test';

const web = fileURLToPath(new URL('..', import.meta.url));
const repo = path.resolve(web, '../..');
const output = path.join(repo, '.artifacts/cpx3-r2');
const runtime = path.join(web, 'test-results/worker-capability', Date.now().toString());
await mkdir(runtime, { recursive: true });
const freePort = () => new Promise(resolve => {
  const probe = net.createServer();
  probe.listen(0, '127.0.0.1', () => { const port = probe.address().port; probe.close(() => resolve(port)); });
});
const [apiPort, browserPort] = await Promise.all([freePort(), freePort()]);
assert.ok(![apiPort, browserPort].some(p => [8767, 8768].includes(p)));
const origin = `http://127.0.0.1:${browserPort}`;
const apiOrigin = `http://127.0.0.1:${apiPort}`;
const key = randomBytes(32).toString('hex');
const python = process.env.CPX3_PYTHON || 'D:/project/Pan-main/.venv/Scripts/python.exe';
const proc = spawn(python, ['scripts/run_tests_isolated.py', '--', python,
  'tests/support/worker_capability_server.py', runtime, String(apiPort), origin],
{ cwd: repo, env: { ...process.env, CPX3_TEST_KEY: key }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
let log = '';
proc.stdout.on('data', data => { log += data; });
proc.stderr.on('data', data => { log += data; });
let browser, vite;
const evidence = { apiPort, browserPort, scenarios: [], fixture: 'injected policy on actual Windows; not POSIX runtime' };
try {
  for (let attempt = 0; ; attempt++) {
    try {
      const response = await fetch(`http://127.0.0.1:${apiPort}/__cpx3/identity`);
      if (response.ok) { evidence.identity = await response.json(); break; }
    } catch {}
    if (attempt >= 80 || proc.exitCode !== null) throw new Error('Owned API failed to start');
    await delay(100);
  }
  assert.equal(path.resolve(evidence.identity.root), path.resolve(runtime));
  assert.equal(evidence.identity.port, apiPort);
  vite = await createServer({ configFile: false, root: path.join(web, 'src'),
    cacheDir: path.join(runtime, 'vite'), plugins: [react(), {
      name: 'cpx3-harness', configureServer(server) {
        server.middlewares.use('/cpx3', async (_req, res) => {
          res.setHeader('Content-Type', 'text/html');
          const entry = '/@fs/' + path.join(web, 'e2e/worker-capability-harness.tsx').replaceAll('\\', '/');
          res.end(await server.transformIndexHtml('/cpx3', `<html><body><div id="root"></div><script type="module" src="${entry}"></script></body></html>`));
        });
      },
    }], resolve: { alias: { '@': path.join(web, 'src') } },
    server: { host: '127.0.0.1', port: browserPort, strictPort: true,
      fs: { allow: [web] }, proxy: { '/api': `http://127.0.0.1:${apiPort}`, '/__cpx3': `http://127.0.0.1:${apiPort}` } },
  });
  await vite.listen();
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(origin + '/cpx3');
  const login = await context.request.post(apiOrigin + '/api/automation/login', { headers: { Origin: apiOrigin }, data: { key } });
  assert.equal(login.status(), 200);
  const auth = await context.request.get(apiOrigin + '/api/automation/session', { headers: { Origin: apiOrigin } });
  assert.equal((await auth.json()).authenticated, true);
  evidence.authenticatedCookie = true;
  for (const mode of ['denied', 'actual', 'unknown']) {
    await context.request.post(origin + '/__cpx3/policy/' + (mode === 'denied' ? 'denied' : 'actual'));
    if (mode === 'unknown') await page.route('**/api/platform', route => route.fulfill({ status: 503, body: 'probe unavailable' }));
    await page.goto(origin + '/cpx3');
    const checkbox = page.getByRole('checkbox', { name: /Enable native Terminal TUI/ });
    await checkbox.waitFor();
    assert.equal(await checkbox.isDisabled(), mode === 'denied');
    assert.equal(await page.getByRole('button', { name: 'Create', exact: true }).isDisabled(), false);
    const info = await context.request.get(apiOrigin + '/api/platform');
    assert.equal((await info.json()).capabilities['terminal.native'], mode !== 'denied');
    if (mode === 'denied') {
      const rejected = await context.request.post(apiOrigin + '/api/sessions', {
        headers: { Origin: apiOrigin },
        data: { name: 'native-denied', adapter: 'codex', nativeControl: true, mcpServers: [] } });
      const rejectedBody = await rejected.json();
      assert.match(String(rejectedBody.error ?? rejectedBody.detail ?? ''), /unsupported_platform/);
      // The same real HTTP API accepts an ordinary stream Session on this host.
      const createdResponse = await context.request.post(apiOrigin + '/api/sessions', {
        headers: { Origin: apiOrigin },
        data: { name: 'stream-allowed', adapter: 'codex', nativeControl: false, mcpServers: [] },
      });
      assert.equal(createdResponse.status(), 200);
      const body = await createdResponse.json();
      assert.doesNotMatch(JSON.stringify(body), /unsupported_platform/);
      evidence.streamCreated = true;
    }
    evidence.scenarios.push({ mode, disabled: await checkbox.isDisabled() });
  }
  await page.screenshot({ path: path.join(output, 'browser.png') });
  await writeFile(path.join(output, 'browser.json'), JSON.stringify(evidence, null, 2));
  console.log('PASS: authenticated owned HTTP + Chromium, denied/actual/unknown gates, stream creation');
} finally {
  await browser?.close();
  await vite?.close();
  // Ask only the retained child wrapper to stop; its interpreter may be a
  // Windows venv launcher. The server fixture handles the explicit owned stop.
  if (evidence.identity) {
    await fetch(`http://127.0.0.1:${apiPort}/__cpx3/stop`, { method: 'POST' }).catch(() => {});
    for (let i = 0; i < 30 && proc.exitCode === null; i++) await delay(100);
  }
  await writeFile(path.join(output, 'browser-server.log'), log);
  if (proc.exitCode === null) throw new Error('Owned API cleanup unconfirmed; inspect retained identity');
}
