/* global URL, Response, fetch, document, innerWidth, getComputedStyle, requestAnimationFrame, process */
import assert from 'node:assert/strict';
import { createServer as createHttpServer } from 'node:http';
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, unlinkSync, writeFileSync } from 'node:fs';
import { extname, join, normalize } from 'node:path';
import net from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';
import { build as viteBuild } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// Owned fixture only: Vite serves the real React source, a local static server
// serves the freshly built dist, and every /api request is fulfilled
// in-process. This script never contacts Pan's practical services (8767/8768),
// Tailscale, cloudflared, or any real Remote endpoint.
const webRoot = fileURLToPath(new URL('..', import.meta.url));
const repoRoot = fileURLToPath(new URL('../../..', import.meta.url));
const artifacts = join(repoRoot, 'artifacts', 'remote-settings-ui');
mkdirSync(artifacts, { recursive: true });

const harnessName = 'remote-settings-ui-harness';
const htmlPath = join(webRoot, 'src', `${harnessName}.html`);
const tsxPath = join(webRoot, 'src', `${harnessName}.tsx`);

async function freePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

const LONG_LAN_URL =
  'http://192.168.123.123:8780/react/?session=remote-settings-ui-fixture-0123456789abcdef0123456789abcdef';
const LONG_PUBLIC_URL =
  'https://mcp-remote-fixture-0123456789abcdef0123456789abcdef.example.com/mcp/v1/stream';

const state = {
  lanError: '',
  mcpFailure: null,
  lan: {
    config: { startupEnabled: false, host: '', port: 8780 },
    activeConfig: null,
    running: false,
    addresses: ['192.168.1.10', '10.0.0.7'],
    urls: [LONG_LAN_URL],
    error: '',
  },
  tailscale: {
    config: { startupEnabled: false, protocol: 'http', port: 18080 },
    running: false,
    online: false,
    url: 'http://100.64.0.9:18080/react/',
    ip: '100.64.0.9',
    dns: '',
    target: '127.0.0.1:8768',
    error: '',
    pendingCleanup: false,
  },
  mcp: {
    config: {
      enabled: false,
      port: 8769,
      public_hostname: 'mcp-remote-fixture.example.com',
      access_issuer: 'https://team-fixture.cloudflareaccess.com',
      access_audience: 'fixture-audience-tag',
      config_path: 'C:\\fixture\\pan\\mcp-tunnel.yml',
      binary_path: '',
    },
    publicUrl: LONG_PUBLIC_URL,
    gateway: { listening: true },
    tunnel: { running: true },
  },
};

const requests = [];
function record(request) {
  const url = new URL(request.url());
  requests.push({ method: request.method(), path: url.pathname, body: request.postData() });
}

function json(route, body, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: { 'Access-Control-Allow-Origin': '*' },
    body: JSON.stringify(body),
  });
}

async function apiRoute(route) {
  const request = route.request();
  record(request);
  const path = new URL(request.url()).pathname;
  const method = request.method();
  if (path === '/api/settings/ui') return json(route, {});
  if (path === '/api/settings/session-lifecycle') {
    return json(route, { exitStrategy: 'ask', startupPreference: 'ask' });
  }
  if (path === '/api/settings/login-startup') {
    return json(route, {
      enabled: false, registered: false, available: true, conflict: false, blocked: false,
      reason: '', targetRoot: 'fixture', dataDirectory: 'fixture', entry: 'fixture',
      registration: null, revision: 'fixture',
    });
  }
  if (path === '/api/main/restart/status') {
    return json(route, { available: true, pending: false, platform: 'nt' });
  }
  if (path === '/api/main/exit/status') {
    return json(route, { available: true, pending: false, platform: 'nt', stage: 'idle' });
  }
  if (path === '/api/remote/status') {
    return json(route, {
      available: true, enabled: true, running: true, protocol: 'http', port: 8787,
    });
  }
  if (path === '/api/remote/restart') {
    return json(route, { ok: true, restarted: true, killed: [] });
  }
  if (path === '/api/remote/lan') {
    if (method === 'PUT') {
      state.lan = { ...state.lan, config: JSON.parse(request.postData() || '{}'), error: '' };
    }
    return json(route, { ...state.lan, error: state.lanError });
  }
  if (path === '/api/remote/lan/start') {
    state.lan = {
      ...state.lan,
      running: true,
      activeConfig: state.lan.config,
      error: state.lanError,
    };
    return json(route, state.lan);
  }
  if (path === '/api/remote/lan/stop') {
    state.lan = { ...state.lan, running: false, activeConfig: null, error: state.lanError };
    return json(route, state.lan);
  }
  if (path === '/api/remote/tailscale') {
    if (method === 'PUT') {
      state.tailscale = {
        ...state.tailscale,
        config: JSON.parse(request.postData() || '{}'),
        error: '',
      };
    }
    return json(route, state.tailscale);
  }
  if (path === '/api/remote/tailscale/start') {
    state.tailscale = { ...state.tailscale, running: true, online: true };
    return json(route, state.tailscale);
  }
  if (path === '/api/remote/tailscale/stop') {
    state.tailscale = {
      ...state.tailscale,
      running: false,
      online: true,
      pendingCleanup: false,
    };
    return json(route, state.tailscale);
  }
  if (path === '/api/remote/mcp') {
    if (state.mcpFailure) return json(route, { detail: state.mcpFailure }, 500);
    if (method === 'PUT') {
      state.mcp = { ...state.mcp, config: JSON.parse(request.postData() || '{}') };
    }
    return json(route, state.mcp);
  }
  if (
    path === '/api/remote/mcp/start' ||
    path === '/api/remote/mcp/restart' ||
    path === '/api/remote/mcp/stop' ||
    path === '/api/remote/mcp/save'
  ) {
    return json(route, { ...state.mcp, ok: true });
  }
  if (path === '/api/codex/models') return json(route, { models: [], defaultModel: '' });
  if (path === '/api/data/catalog') return json(route, { catalog: [] });
  if (path === '/api/settings/data-retention') {
    return json(route, { policies: [], lastSeen: null, lastScans: [] });
  }
  if (path === '/api/jobs/completed-retention') {
    return json(route, { rules: {}, configValid: true, configValidity: {}, lastRun: null, lastRuns: {} });
  }
  if (path === '/api/notifications/error-types') return json(route, { types: [] });
  if (path === '/api/notifications/plugin-message-sources') return json(route, { sources: [] });
  if (path === '/api/reminding-cards/settings') return json(route, { minutes: 10 });
  if (path === '/api/automation/session') return json(route, { authenticated: false, expiresAt: null, sessionDays: 0 });
  if (path === '/api/health') return json(route, { ok: true, status: 'ok' });
  if (path === '/api/platform') return json(route, { platform: 'win32', capabilities: {} });
  if (path === '/api/sessions') return json(route, { sessions: [], currentSessionId: null });
  if (path === '/api/workspaces') return json(route, { workspaces: [] });
  if (path === '/api/jobs') return json(route, { jobs: [] });
  if (path === '/api/views') return json(route, { views: [] });
  return json(route, {});
}

function writeHarness() {
  writeFileSync(
    htmlPath,
    `<!doctype html><html><head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" /><title>Remote settings harness</title></head><body><div id="root"></div><script type="module" src="/${harnessName}.tsx"></script></body></html>`,
  );
  writeFileSync(
    tsxPath,
    `import { createRoot } from 'react-dom/client';\nimport { AppSettingsModal } from './components/layout/AppSettingsModal';\nimport { useAppSettingsStore, DEFAULT_SETTINGS } from './stores/appSettingsStore';\nimport { usePlatformStore } from './stores/platformStore';\nimport './index.css';\nuseAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });\nusePlatformStore.setState({ platform: 'win32', capabilities: {}, loaded: true });\ncreateRoot(document.getElementById('root')!).render(<AppSettingsModal open onClose={() => {}} />);`,
  );
}

async function waitForServer(url) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return true;
    } catch {
      /* still starting */
    }
    await delay(200);
  }
  return false;
}

const sourcePort = await freePort();
const distPort = await freePort();
const sourceURL = `http://127.0.0.1:${sourcePort}/${harnessName}.html`;
const distURL = `http://127.0.0.1:${distPort}/${harnessName}.html`;
const harnessDist = join(webRoot, 'test-results/remote-settings-ui/dist');

writeHarness();
const sourceServer = await createServer({
  configFile: false,
  root: join(webRoot, 'src'),
  cacheDir: join(webRoot, 'test-results/remote-settings-ui/vite-cache'),
  resolve: { alias: { '@': join(webRoot, 'src') } },
  plugins: [react(), tailwindcss()],
  server: { host: '127.0.0.1', port: sourcePort, strictPort: true, proxy: {} },
});
await sourceServer.listen();

// Fresh production build of the same source that the dev server serves. The
// real app dist needs a backend to boot, so the production artifact for this
// UI check is built from the identical harness entry with the same plugins.
await viteBuild({
  configFile: false,
  root: join(webRoot, 'src'),
  cacheDir: join(webRoot, 'test-results/remote-settings-ui/vite-build-cache'),
  resolve: { alias: { '@': join(webRoot, 'src') } },
  plugins: [react(), tailwindcss()],
  logLevel: 'warn',
  build: {
    outDir: harnessDist,
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: { input: htmlPath },
  },
});
assert.ok(
  existsSync(join(harnessDist, `${harnessName}.html`)),
  `production harness build is missing ${harnessName}.html`,
);

const mime = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
  '.map': 'application/json; charset=utf-8',
};
const distServer = createHttpServer(async (request, response) => {
  const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
  const relative = pathname === '/' ? `${harnessName}.html` : pathname.replace(/^\//, '');
  const file = normalize(join(harnessDist, relative));
  if (!file.startsWith(normalize(harnessDist)) || !existsSync(file)) {
    response.writeHead(404);
    response.end('not found');
    return;
  }
  response.writeHead(200, { 'Content-Type': mime[extname(file)] ?? 'application/octet-stream' });
  response.end(readFileSync(file));
});
await new Promise((resolve) => distServer.listen(distPort, '127.0.0.1', resolve));

// The app-level dist must also carry the new Remote markup (built by pnpm
// build before this script runs).
const appDist = join(webRoot, 'dist', 'assets');
const appSettingsChunk = existsSync(appDist)
  ? readdirSync(appDist).find((file) => file.startsWith('AppSettingsModal-') && file.endsWith('.js'))
  : undefined;
assert.ok(appSettingsChunk, 'dist AppSettingsModal chunk is missing; run pnpm build first');
const appChunkText = readFileSync(join(appDist, appSettingsChunk), 'utf8');
for (const marker of ['remote-tunnel-section', 'remote-mcp-section', 'remote-lan-section', 'MCP 远程连接']) {
  assert.ok(appChunkText.includes(marker), `dist chunk is missing ${marker}`);
}

let browser;
const evidence = {
  source: { url: sourceURL, viewport: {}, assertions: {} },
  dist: { url: distURL, assertions: {} },
  requests,
  screenshots: [],
};

try {
  assert.ok(await waitForServer(sourceURL), `Vite did not become ready at ${sourceURL}`);
  assert.ok(
    await waitForServer(sourceURL.replace(/\.html$/, '.tsx')),
    'Vite did not transform the harness entry',
  );
  assert.ok(await waitForServer(distURL), `dist server did not become ready at ${distURL}`);

  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    deviceScaleFactor: 1,
    permissions: ['clipboard-read', 'clipboard-write'],
  });
  const page = await context.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  const protectedRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('request', (request) => {
    const url = request.url();
    if (/:(8767|8768)(\/|$)/.test(url)) protectedRequests.push(url);
  });
  await page.route('**/api/**', apiRoute);

  const openRemoteTab = async () => {
    await page.getByRole('tab', { name: 'Lifecycle' }).waitFor({ state: 'visible' });
    await page.getByRole('tab', { name: 'Remote' }).click();
    await page.waitForSelector('[data-testid="remote-lan-section"]');
  };
  const section = (testId) => page.locator(`[data-testid="${testId}"]`);
  const header = (testId) => section(testId).locator('button[aria-expanded]').first();
  const bodyId = async (testId) => await header(testId).getAttribute('aria-controls');
  const isBodyHidden = async (testId) =>
    await page.evaluate((id) => {
      const element = document.getElementById(id);
      return !element || getComputedStyle(element).display === 'none';
    }, await bodyId(testId));
  const settle = () =>
    page.evaluate(
      () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
    );
  const shot = async (name) => {
    const file = join(artifacts, name);
    await page.screenshot({ path: file });
    evidence.screenshots.push(file);
  };

  await page.goto(sourceURL, { waitUntil: 'networkidle' });
  await openRemoteTab();
  await page.waitForFunction(() =>
    document.body.textContent?.includes('网关：就绪 · 隧道进程：运行中'),
  );
  await settle();

  const ids = ['remote-tunnel-section', 'remote-mcp-section', 'remote-tailscale-section', 'remote-lan-section'];
  assert.deepEqual(
    await page.locator('[role="tabpanel"] [data-testid$="-section"]').evaluateAll((elements) =>
      elements.map((element) => element.getAttribute('data-testid')),
    ),
    ids,
    'Remote capabilities must stay in their capability order',
  );
  evidence.source.assertions.sectionOrder = ids;

  // Headers are real buttons with expanded state, and every body is visible.
  for (const id of ids) {
    assert.equal(await header(id).evaluate((element) => element.tagName), 'BUTTON');
    assert.equal(await header(id).getAttribute('aria-expanded'), 'true');
    assert.equal(await isBodyHidden(id), false, `${id} body should start expanded`);
  }

  // Desktop expanded + collapsed screenshots.
  await shot('desktop-1280-expanded.png');
  for (const id of ids) {
    await header(id).click();
    assert.equal(await header(id).getAttribute('aria-expanded'), 'false');
    assert.equal(await isBodyHidden(id), true, `${id} body should hide when collapsed`);
  }
  await shot('desktop-1280-collapsed.png');
  for (const id of ids) await header(id).click();

  // Keyboard: focus lands on a section header with a visible ring, and Enter
  // and Space toggle it.
  await page.getByRole('tab', { name: 'Remote' }).focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  const focusInfo = await page.evaluate(() => {
    const element = document.activeElement;
    const style = element ? getComputedStyle(element) : null;
    return {
      tag: element?.tagName ?? null,
      expanded: element?.getAttribute('aria-expanded') ?? null,
      outlineStyle: style?.outlineStyle ?? null,
      outlineWidth: style?.outlineWidth ?? null,
    };
  });
  assert.equal(focusInfo.tag, 'BUTTON', `keyboard focus missed the section header: ${JSON.stringify(focusInfo)}`);
  assert.equal(focusInfo.expanded, 'true');
  assert.notEqual(focusInfo.outlineStyle, 'none', 'section header must paint a focus ring');
  assert.ok(parseFloat(focusInfo.outlineWidth ?? '0') >= 1);
  await page.keyboard.press('Enter');
  assert.equal(
    await page.locator(':focus').getAttribute('aria-expanded'),
    'false',
    'Enter must collapse the focused section',
  );
  await page.keyboard.press('Space');
  assert.equal(
    await page.locator(':focus').getAttribute('aria-expanded'),
    'true',
    'Space must expand the focused section',
  );
  evidence.source.assertions.keyboardFocusRing = focusInfo;

  await header('remote-lan-section').focus();
  await page.keyboard.press('Enter');
  assert.equal(await header('remote-lan-section').getAttribute('aria-expanded'), 'false');
  await page.keyboard.press('Space');
  assert.equal(await header('remote-lan-section').getAttribute('aria-expanded'), 'true');
  evidence.source.assertions.keyboardToggle = true;

  // Unsaved draft survives collapse/expand, and toggling performs no GET.
  const mcpGetsBefore = requests.filter(
    (request) => request.path === '/api/remote/mcp' && request.method === 'GET',
  ).length;
  await page.locator('#mcp-public_hostname').fill('edited.example.com');
  await header('remote-mcp-section').click();
  await header('remote-mcp-section').click();
  assert.equal(await page.locator('#mcp-public_hostname').inputValue(), 'edited.example.com');
  assert.equal(
    requests.filter((request) => request.path === '/api/remote/mcp' && request.method === 'GET')
      .length,
    mcpGetsBefore,
    'collapsing/expanding must not refetch the MCP status',
  );
  evidence.source.assertions.draftPreservedWithoutRefetch = true;

  // Field alignment: labels sit above their controls, everything stays inside
  // its section card, and no control overflows the card horizontally.
  const geometry = await page.evaluate((sectionIds) => {
    const rows = [];
    for (const id of sectionIds) {
      const card = document.querySelector(`[data-testid="${id}"]`);
      if (!card) continue;
      const cardRect = card.getBoundingClientRect();
      const controls = [...card.querySelectorAll('input, select, button[role="switch"]')];
      for (const control of controls) {
        const rect = control.getBoundingClientRect();
        rows.push({
          section: id,
          tag: control.tagName,
          id: control.id,
          insideX: rect.left >= cardRect.left - 1 && rect.right <= cardRect.right + 1,
          insideY: rect.top >= cardRect.top - 1 && rect.bottom <= cardRect.bottom + 1,
          width: rect.width,
        });
      }
      for (const label of card.querySelectorAll('label[for]')) {
        const control = document.getElementById(label.getAttribute('for'));
        if (!control) continue;
        const labelRect = label.getBoundingClientRect();
        const controlRect = control.getBoundingClientRect();
        rows.push({
          section: id,
          label: label.textContent,
          above: labelRect.bottom <= controlRect.top + 2,
          leftAligned: Math.abs(labelRect.left - controlRect.left) <= 2,
        });
      }
    }
    return rows;
  }, ids);
  assert.deepEqual(
    geometry.filter((row) => row.insideX === false || row.insideY === false),
    [],
    `controls escaped their section: ${JSON.stringify(geometry)}`,
  );
  assert.deepEqual(
    geometry.filter((row) => row.above === false || row.leftAligned === false),
    [],
    `labels are not aligned above their controls: ${JSON.stringify(geometry)}`,
  );
  evidence.source.assertions.geometry = geometry;

  // Real interactions still hit the original endpoints.
  await page.locator('#lan-host').selectOption('192.168.1.10');
  await page.locator('#lan-port').fill('8899');
  await page.getByRole('button', { name: '保存 LAN 配置' }).click();
  await page.waitForFunction(() => {
    const button = [...document.querySelectorAll('button')].find(
      (candidate) => candidate.textContent === '开启 LAN 访问',
    );
    return Boolean(button && !button.disabled);
  });
  await page.getByRole('button', { name: '开启 LAN 访问' }).click();
  await page.waitForFunction(() =>
    document.body.textContent?.includes('已开启 192.168.1.10:8899'),
  );
  const lanSave = requests.find(
    (request) => request.path === '/api/remote/lan' && request.method === 'PUT',
  );
  assert.ok(lanSave, 'LAN save never issued PUT /api/remote/lan');
  assert.deepEqual(JSON.parse(lanSave.body), {
    startupEnabled: false,
    host: '192.168.1.10',
    port: 8899,
  });
  assert.ok(
    requests.some((request) => request.path === '/api/remote/lan/start' && request.method === 'POST'),
    'LAN start never issued POST /api/remote/lan/start',
  );

  await page.locator('#tailscale-port').fill('18081');
  await section('remote-tailscale-section').getByRole('button', { name: '保存配置' }).click();
  const tailscaleSave = requests.find(
    (request) => request.path === '/api/remote/tailscale' && request.method === 'PUT',
  );
  assert.deepEqual(JSON.parse(tailscaleSave.body), {
    startupEnabled: false,
    protocol: 'http',
    port: 18081,
  });
  await section('remote-tailscale-section').getByRole('button', { name: '开启当前访问' }).click();
  assert.ok(
    requests.some(
      (request) => request.path === '/api/remote/tailscale/start' && request.method === 'POST',
    ),
    'Tailscale start never issued POST /api/remote/tailscale/start',
  );

  await section('remote-mcp-section').getByRole('button', { name: '启动', exact: true }).click();
  assert.ok(
    requests.some(
      (request) => request.path === '/api/remote/mcp/start' && request.method === 'POST',
    ),
    'MCP start never issued POST /api/remote/mcp/start',
  );

  // Stop the two entry points and restart the tunnel from the UI; each action
  // must keep its original endpoint.
  await section('remote-lan-section').getByRole('button', { name: '关闭 LAN 访问' }).click();
  await page.waitForFunction(() =>
    document
      .querySelector('[data-testid="remote-lan-section"] button[aria-expanded]')
      ?.textContent?.includes('已关闭'),
  );
  assert.ok(
    requests.some(
      (request) => request.path === '/api/remote/lan/stop' && request.method === 'POST',
    ),
    'LAN stop never issued POST /api/remote/lan/stop',
  );
  await section('remote-tailscale-section')
    .getByRole('button', { name: '关闭当前访问' })
    .click();
  await page.waitForFunction(() =>
    document.body.textContent?.includes('已关闭 · Tailscale 在线'),
  );
  assert.ok(
    requests.some(
      (request) => request.path === '/api/remote/tailscale/stop' && request.method === 'POST',
    ),
    'Tailscale stop never issued POST /api/remote/tailscale/stop',
  );
  await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === '/api/remote/restart'),
    page.getByRole('button', { name: 'Restart tunnel' }).click(),
  ]);
  assert.ok(
    requests.some(
      (request) => request.path === '/api/remote/restart' && request.method === 'POST',
    ),
    'tunnel restart never issued POST /api/remote/restart',
  );
  evidence.source.assertions.requests = {
    lanSave: JSON.parse(lanSave.body),
    tailscaleSave: JSON.parse(tailscaleSave.body),
    endpoints: requests.map((request) => `${request.method} ${request.path}`),
  };

  // Long LAN address wraps and copies without widening the panel.
  const urlGeometry = await page.evaluate(() => {
    const code = [...document.querySelectorAll('code')].find((element) =>
      element.textContent?.startsWith('http://192.168.123.123'),
    );
    const card = code?.closest('[data-testid="remote-lan-section"]');
    const style = code ? getComputedStyle(code) : null;
    return {
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: innerWidth,
      codeRight: code?.getBoundingClientRect().right ?? 0,
      cardRight: card?.getBoundingClientRect().right ?? 0,
      codeWidth: code?.getBoundingClientRect().width ?? 0,
      codeScrollWidth: code?.scrollWidth ?? 0,
      codeClientWidth: code?.clientWidth ?? 0,
      wordBreak: style?.wordBreak,
      whiteSpace: style?.whiteSpace,
      overflowWrap: style?.overflowWrap,
      display: style?.display,
    };
  });
  console.log('long URL geometry', JSON.stringify(urlGeometry));
  assert.equal(urlGeometry.documentWidth, urlGeometry.viewportWidth, 'long URL overflowed the page');
  assert.ok(urlGeometry.codeRight <= urlGeometry.cardRight + 1, 'long URL escaped its section');
  assert.ok(
    urlGeometry.codeScrollWidth <= urlGeometry.codeClientWidth + 1,
    `long URL text overflows its box: ${JSON.stringify(urlGeometry)}`,
  );
  await page.getByRole('button', { name: '复制 LAN 地址' }).click();
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), LONG_LAN_URL);
  evidence.source.assertions.longUrl = urlGeometry;

  // Status error stays visible inside its (still expanded) section.
  state.lanError = 'LAN 端口已被占用';
  await page.getByRole('button', { name: '刷新 LAN 状态' }).click();
  await page.waitForFunction(() => document.body.textContent?.includes('LAN 端口已被占用'));
  assert.equal(await isBodyHidden('remote-lan-section'), false);
  assert.equal(
    await section('remote-lan-section').getByRole('alert').textContent(),
    'LAN 端口已被占用',
  );
  await shot('desktop-1280-status-error.png');
  state.lanError = '';

  // Load failure state for MCP after a reload.
  state.mcpFailure = 'MCP 配置读取失败';
  await page.reload({ waitUntil: 'networkidle' });
  await openRemoteTab();
  await page.waitForFunction(() => document.body.textContent?.includes('MCP 配置读取失败'));
  assert.equal(
    await section('remote-mcp-section').getByRole('alert').textContent(),
    'MCP 配置读取失败',
  );
  assert.ok(
    (await section('remote-mcp-section').textContent()).includes('状态不可用'),
    'MCP section must summarize the unavailable state',
  );
  await shot('desktop-1280-load-error.png');
  state.mcpFailure = null;

  // Representative running state, then 390px narrow layout.
  state.lan = { ...state.lan, running: true, activeConfig: { startupEnabled: false, host: '192.168.1.10', port: 8899 } };
  state.tailscale = { ...state.tailscale, running: true, online: true };
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload({ waitUntil: 'networkidle' });
  await openRemoteTab();
  await page.waitForFunction(() =>
    document.body.textContent?.includes('已开启 192.168.1.10:8899'),
  );
  const mobile = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: innerWidth,
    panelWidth: document.querySelector('[role="tabpanel"]')?.getBoundingClientRect().width ?? 0,
    sectionWidths: [...document.querySelectorAll('[data-testid$="-section"]')].map(
      (element) => element.getBoundingClientRect().width,
    ),
  }));
  assert.ok(
    mobile.documentWidth <= mobile.viewportWidth,
    `390px layout overflowed: ${JSON.stringify(mobile)}`,
  );
  assert.ok(
    mobile.sectionWidths.every((width) => width <= mobile.panelWidth + 1),
    `390px sections escaped the panel: ${JSON.stringify(mobile)}`,
  );
  await shot('mobile-390-expanded.png');
  for (const id of ids) await header(id).click();
  assert.ok(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    '390px collapsed layout overflowed',
  );
  await shot('mobile-390-collapsed.png');
  evidence.source.assertions.mobile = mobile;

  assert.deepEqual(protectedRequests, [], `protected services were contacted: ${protectedRequests}`);
  assert.deepEqual(pageErrors, [], `page errors: ${pageErrors.join('; ')}`);
  // The MCP load-failure step intentionally returns one 500; every other
  // console error would be a regression.
  const expectedFailureConsole =
    'Failed to load resource: the server responded with a status of 500 (Internal Server Error)';
  assert.deepEqual(
    consoleErrors.filter((message) => message !== expectedFailureConsole),
    [],
    `console errors: ${consoleErrors.join('; ')}`,
  );
  assert.equal(
    consoleErrors.filter((message) => message === expectedFailureConsole).length,
    1,
    'the intentional MCP 500 must be the only resource error',
  );

  // ── Fresh dist (built from the same source) ─────────────────────────────
  const distPage = await context.newPage();
  const distErrors = [];
  const distConsoleErrors = [];
  distPage.on('pageerror', (error) => distErrors.push(error.message));
  distPage.on('console', (message) => {
    if (message.type() === 'error' && !/WebSocket|websocket|\/ws\b/.test(message.text())) {
      distConsoleErrors.push(message.text());
    }
  });
  await distPage.route('**/api/**', apiRoute);
  distPage.on('request', (request) => {
    if (/:(8767|8768)(\/|$)/.test(request.url())) protectedRequests.push(request.url());
  });
  await distPage.goto(distURL, { waitUntil: 'networkidle' });
  await distPage.getByRole('tab', { name: 'Remote' }).click();
  await distPage.waitForSelector('[data-testid="remote-lan-section"]');
  await distPage.waitForFunction(() =>
    document.body.textContent?.includes('网关：就绪 · 隧道进程：运行中'),
  );
  assert.deepEqual(
    await distPage
      .locator('[role="tabpanel"] [data-testid$="-section"]')
      .evaluateAll((elements) =>
        elements.map((element) => element.getAttribute('data-testid')),
      ),
    ids,
    'fresh dist must render the same four Remote sections',
  );
  const distLanHeader = distPage
    .locator('[data-testid="remote-lan-section"] button[aria-expanded]')
    .first();
  assert.equal(await distLanHeader.getAttribute('aria-expanded'), 'true');
  await distLanHeader.click();
  assert.equal(await distLanHeader.getAttribute('aria-expanded'), 'false');
  assert.ok(
    await distPage.evaluate(() => {
      const body = document.querySelector('[data-testid="remote-lan-section"] [id$="-body"]');
      return !body || getComputedStyle(body).display === 'none';
    }),
    'fresh dist LAN body must hide when collapsed',
  );
  await distLanHeader.click();
  await distPage.screenshot({ path: join(artifacts, 'dist-1280-remote.png') });
  evidence.screenshots.push(join(artifacts, 'dist-1280-remote.png'));
  evidence.dist.assertions.sections = ids;
  evidence.dist.assertions.productionHarness = `${harnessName}.html + assets`;
  evidence.dist.assertions.appChunk = appSettingsChunk;
  assert.deepEqual(distErrors, [], `dist page errors: ${distErrors.join('; ')}`);
  assert.deepEqual(
    distConsoleErrors,
    [],
    `dist console errors: ${distConsoleErrors.join('; ')}`,
  );

  evidence.protectedRequests = protectedRequests;
  evidence.pageErrors = pageErrors;
  evidence.consoleErrors = {
    intentionalMcpFailure: consoleErrors.filter((message) => message === expectedFailureConsole),
    unexpected: consoleErrors.filter((message) => message !== expectedFailureConsole),
  };
  evidence.dist.pageErrors = distErrors;
  evidence.dist.consoleErrors = distConsoleErrors;
  writeFileSync(join(artifacts, 'browser-verification.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify(evidence, null, 2));
} finally {
  await browser?.close();
  await sourceServer.close();
  await new Promise((resolve) => distServer.close(resolve));
  for (const path of [htmlPath, tsxPath]) if (existsSync(path)) unlinkSync(path);
  rmSync(join(webRoot, 'test-results/remote-settings-ui'), { recursive: true, force: true });
}
