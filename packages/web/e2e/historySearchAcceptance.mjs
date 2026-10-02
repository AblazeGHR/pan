import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import { spawn, spawnSync } from 'node:child_process';
import { chromium } from '@playwright/test';

const root = path.resolve(import.meta.dirname, '../../..');
const port = Number(process.env.PAN_SEARCH_TEST_PORT || 18769);
assert.ok(port !== 8767 && port !== 8768);
const base = `http://127.0.0.1:${port}`;
const runtime = await fs.mkdtemp(path.join(os.tmpdir(), 'pan-search-completion-'));
const evidenceDir = path.join(import.meta.dirname, 'test-results/history-search-content-completion');
await fs.mkdir(evidenceDir, { recursive: true });
const evidence = { root, runtime, port, tests: [], performance: {}, consoleErrors: [], pageErrors: [], requests: [] };
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let server, browser, tab;
let logs = '';
async function api(route, options) {
  const response = await fetch(base+route, options);
  const body = await response.json();
  return { status: response.status, body };
}
async function measure(route, rounds = 5) {
  const samples = [];
  for (let i = 0; i < rounds; i++) {
    const start = performance.now();
    const response = await fetch(base+route);
    const text = await response.text();
    assert.equal(response.status, 200);
    samples.push({ ms: performance.now()-start, bytes: Buffer.byteLength(text), total: JSON.parse(text).totalMatches });
  }
  return samples;
}
try {
  await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', reject);
    probe.listen(port, '127.0.0.1', () => probe.close(resolve));
  });
  server = spawn('C:/Users/14709/AppData/Local/Programs/Python/Python314/python.exe',
    [path.join(import.meta.dirname, 'historySearchServer.py')], { cwd: root, windowsHide: true,
      env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime, PAN_COMPARE_PR: '1' }, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout.on('data', (data) => { logs += data; });
  server.stderr.on('data', (data) => { logs += data; });
  let ready = false;
  for (let i = 0; i < 200; i++) {
    if (server.exitCode !== null) throw new Error(`fixture exited ${server.exitCode}: ${logs}`);
    try { ready = (await api('/api/sessions?summary=1')).status === 200; } catch { /* starting */ }
    if (ready) break;
    await delay(200);
  }
  assert.ok(ready, 'isolated server started');
  const identity = JSON.parse(await fs.readFile(path.join(runtime, 'server-identity.json'), 'utf8'));
  assert.equal(identity.pid, server.pid);
  assert.equal(path.resolve(identity.checkout), root);
  assert.equal(path.resolve(identity.runtime), runtime);
  evidence.identity = identity;
  const ids = JSON.parse(await fs.readFile(path.join(runtime, 'seed-ids.json'), 'utf8'));
  const options = 'countMode=content&roles=user,assistant,tool,thinking';
  const local = `/api/history/search?${options}&sessionId=${ids.primary}&q=DeepNeedle`;
  const first = await api(local+'&limit=1');
  assert.equal(first.body.totalMatches, 7);
  assert.equal(first.body.totalMessages, 4);
  let cursor = first.body.nextCursor, hits = [...first.body.hits];
  while (cursor) {
    const page = await api(local+'&limit=1&cursor='+encodeURIComponent(cursor));
    assert.equal(page.status, 200);
    hits.push(...page.body.hits);
    cursor = page.body.nextCursor;
  }
  assert.deepEqual(hits.map((hit) => hit.role), ['assistant', 'assistant', 'tool', 'thinking']);
  assert.equal((await api(local.replace('user,assistant,tool,thinking', 'user,assistant'))).body.totalMatches, 3);
  assert.equal((await api(`/api/history/search?${options}&sessionId=${ids.primary}&q=${encodeURIComponent('中')}`)).body.totalMatches, 1);
  assert.equal((await api(`/api/history/search?${options}&sessionId=${ids.primary}&q=${encodeURIComponent('+++')}`)).body.totalMatches, 1);
  assert.equal((await api(local+'&matchIndex=5')).body.hits[0].role, 'tool');
  assert.equal((await api(local+'&messageId='+encodeURIComponent(hits[2].messageId))).body.hits[0].messageIndex, 405);
  evidence.tests.push('HTTP full counts, paging, literal CJK/punctuation, roles, ordinal seek and stable ID');
  await api('/__e2e/cold-registry', { method: 'POST' });
  const bench = `/api/history/search?${options}&sessionId=${ids.bench}&q=`;
  evidence.performance.cold = await measure(bench+'RareNeedle', 1);
  evidence.performance.warm = await measure(bench+'RareNeedle');
  evidence.performance.denseFirst = await measure(bench+'commonterm', 1);
  evidence.performance.denseWarm = await measure(bench+'commonterm');
  evidence.performance.prSelective = await measure(`/__e2e/pr3-search?sessionId=${ids.bench}&q=RareNeedle`);
  evidence.performance.prDense = await measure(`/__e2e/pr3-search?sessionId=${ids.bench}&q=commonterm`);
  const append = () => api('/__e2e/append-history', { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ sessionId: ids.bench, messages: [{ role: 'tool', content: 'RareNeedle appended' }] }) });
  await append();
  await api('/__e2e/cold-registry', { method: 'POST' });
  evidence.performance.appendFirst = await measure(bench+'RareNeedle', 1);
  evidence.performance.prAppend = await measure(`/__e2e/pr3-search?sessionId=${ids.bench}&q=RareNeedle`);
  assert.equal(evidence.performance.appendFirst[0].total, 21);
  const globals = '/api/history/search?'+options+'&q=SharedNeedle&limit=100';
  let page = await api(globals), seen = [...page.body.hits];
  const oldCursor = page.body.nextCursor;
  while (page.body.nextCursor) {
    page = await api(globals+'&cursor='+encodeURIComponent(page.body.nextCursor));
    seen.push(...page.body.hits);
  }
  assert.equal(seen.length, 601);
  assert.equal(page.body.totalMatches, 1202);
  assert.equal(new Set(seen.map((hit) => hit.messageId)).size, 601);
  await api('/__e2e/append-history', { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ sessionId: ids.remote, messages: [{ role: 'assistant', content: 'unrelated new body' }] }) });
  assert.equal((await api(globals+'&cursor='+encodeURIComponent(oldCursor))).status, 409);
  assert.equal(await fs.stat(path.join(runtime, 'history_search.sqlite3')).then(() => true, () => false), false);
  evidence.tests.push('Global >500 complete results, bounded references and no SQLite creation');
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  tab = await context.newPage();
  tab.on('console', (message) => { if (message.type() === 'error') evidence.consoleErrors.push(message.text()); });
  tab.on('pageerror', (error) => evidence.pageErrors.push(error.message));
  tab.on('request', (request) => evidence.requests.push(request.url()));
  await tab.route('**/*', (route) => /:(8767|8768)(\/|$)/.test(route.request().url()) ? route.abort() : route.continue());
  await tab.goto(base+'/react/?panE2E=1');
  await tab.waitForTimeout(700);
  assert.equal(evidence.requests.filter((url) => /SessionHistorySearch|GlobalHistorySearch|HistorySearchRoles|\/api\/history\/search/.test(url)).length, 0);
  await tab.keyboard.press('Control+f');
  assert.equal(await tab.getByTestId('session-history-search-input').count(), 0);
  assert.equal(await tab.getByTestId('chat-tools-sidebar').count(), 0);
  await tab.locator('[title="App settings"]').first().click();
  await tab.locator('#app-settings-tab-appearance').click();
  const toggle = tab.getByRole('switch').filter({ hasText: 'Enable Session and global history search' });
  await toggle.click();
  await tab.getByRole('switch').filter({ hasText: 'Show QQ messages' }).click();
  await tab.getByRole('button', { name: 'Close', exact: true }).last().click();
  const ensureSidebarOpen = async () => {
    const dock = tab.getByTestId('message-navigation-dock');
    if (await dock.getAttribute('data-expanded') === 'true') return;
    if (await dock.getAttribute('data-placement') === 'viewport-end') {
      await tab.getByTestId('mobile-message-navigation-toggle').click();
    } else {
      // Escape may have folded the dock while the pointer is still over its
      // handle. Re-enter it, just as a user would after dismissing the panel.
      await tab.mouse.move(300, 300);
      await dock.locator('.message-navigation-dock__handle').hover();
    }
    await tab.waitForFunction(() => document.querySelector('[data-testid="message-navigation-dock"]')?.dataset.expanded === 'true');
    await tab.waitForTimeout(200); // let the shared width/panel transition settle
  };
  assert.equal(await tab.getByTestId('message-navigation-dock').getAttribute('data-expanded'), 'false');
  assert.equal(await tab.getByTestId('session-history-search-toggle').isVisible(), false);
  assert.equal(await tab.getByTestId('global-history-search-toggle').isVisible(), false);
  await ensureSidebarOpen();
  await tab.getByTestId('session-history-search-toggle').waitFor({ state: 'visible' });
  await tab.getByTestId('global-history-search-toggle').waitFor({ state: 'visible' });
  const searchOnly = await tab.getByTestId('chat-tools-sidebar').evaluate((sidebar) => ({
    search: sidebar.querySelectorAll('.chat-tools-sidebar__search button').length,
    navigation: sidebar.querySelectorAll('.chat-tools-sidebar__navigation').length,
  }));
  assert.deepEqual(searchOnly, { search: 2, navigation: 0 });
  await tab.locator('[title="App settings"]').first().click();
  await tab.locator('#app-settings-tab-appearance').click();
  await tab.getByRole('switch').filter({ hasText: 'Show message navigation rail' }).click();
  await tab.getByRole('button', { name: 'Close', exact: true }).last().click();
  await ensureSidebarOpen();
  const sidebarLayout = await tab.getByTestId('chat-tools-sidebar').evaluate((sidebar) => {
    const buttons = [...sidebar.querySelectorAll('.chat-tools-sidebar__search button')].map((b) => b.getBoundingClientRect().toJSON());
    return { buttons, navigation: sidebar.querySelector('.chat-tools-sidebar__navigation').getBoundingClientRect().toJSON() };
  });
  assert.equal(sidebarLayout.buttons.length, 2);
  assert.equal(sidebarLayout.buttons[0].x, sidebarLayout.buttons[1].x);
  assert.ok(sidebarLayout.buttons[0].bottom <= sidebarLayout.buttons[1].top);
  assert.ok(sidebarLayout.buttons[1].bottom <= sidebarLayout.navigation.top);
  evidence.sidebarLayout = sidebarLayout;
  // Escape folds the entire shared panel, not just the navigation below it.
  await tab.locator('.message-navigation-dock__handle').focus();
  await tab.keyboard.press('Escape');
  await tab.waitForTimeout(200);
  assert.equal(await tab.getByTestId('session-history-search-toggle').isVisible(), false);
  assert.equal(await tab.getByTestId('global-history-search-toggle').isVisible(), false);
  assert.equal(await tab.locator('.message-navigation-rail').isVisible(), false);
  evidence.tests.push('One desktop dock folds both search buttons and navigation, search-only also starts folded');
  await ensureSidebarOpen();
  await tab.locator('[data-session-card-id]').filter({ hasText: 'Search Legacy Local' }).first().click();
  await ensureSidebarOpen();
  await tab.getByTestId('session-history-search-toggle').click();
  await tab.getByTestId('session-history-search-input').fill('LegacyLocalNeedle');
  await tab.waitForFunction(() => document.querySelector('[data-testid="session-history-search-count"]')?.textContent?.trim() === '1 / 8');
  await tab.locator('[data-search-target] mark').first().waitFor({ state: 'visible' });
  await ensureSidebarOpen();
  await tab.getByTestId('global-history-search-toggle').click();
  await tab.getByTestId('global-history-search-input').fill('LegacyGlobalNeedle');
  await tab.getByText(/1 occurrences found so far/).waitFor({ state: 'visible' });
  assert.equal(await tab.getByTestId('global-history-search-load-more').count(), 0);
  await tab.getByText(/9 occurrences/).waitFor({ state: 'visible' });
  await tab.getByRole('search', { name: 'Global history search' }).getByRole('button', { name: /Search Legacy Global, assistant/ }).first().click();
  await tab.locator('[data-search-target] mark').first().waitFor({ state: 'visible' });
  const legacyHistory = await api(`/api/sessions/${ids.legacyGlobal}/history?limit=50`);
  assert.equal(legacyHistory.body.history.length, 4);
  assert.ok(legacyHistory.body.history.every((row) => row.messageId.startsWith('pan:') && row.pluginField === 'preserve'));
  evidence.tests.push('Legacy Session backfill/reload navigation and global provisional-to-complete streamed preparation (800ms fixture delay)');
  await tab.locator('[data-session-card-id]').filter({ hasText: 'Search Acceptance' }).first().click();
  await tab.getByTestId('session-history-search-toggle').waitFor({ state: 'visible' });
  await tab.evaluate(() => document.activeElement?.blur());
  await tab.keyboard.press('Control+f');
  const input = tab.getByTestId('session-history-search-input');
  await input.fill('DeepNeedle');
  await tab.waitForFunction(() => document.querySelector('[data-testid="session-history-search-count"]')?.textContent?.trim() === '1 / 7');
  await tab.locator('[data-search-target] .history-search-word-active').waitFor({ state: 'visible' });
  assert.equal(await tab.locator('[data-search-target] mark').count(), 2);
  for (let ordinal = 2; ordinal <= 7; ordinal++) {
    await tab.getByRole('button', { name: 'Next result', exact: true }).click();
    await tab.waitForFunction((expected) => document.querySelector('[data-testid="session-history-search-count"]')?.textContent?.trim() === `${expected} / 7`, ordinal);
    await tab.getByRole('button', { name: 'Next result', exact: true }).waitFor({ state: 'visible' });
    await tab.waitForFunction(() => !document.querySelector('button[aria-label="Next result"]')?.disabled);
  }
  assert.equal(await tab.locator('[data-search-expanded-role="thinking"]').count(), 1);
  await tab.getByRole('checkbox', { name: 'Search tool messages' }).uncheck();
  await tab.getByRole('checkbox', { name: 'Search thinking messages' }).uncheck();
  await tab.waitForFunction(() => document.querySelector('[data-testid="session-history-search-count"]')?.textContent?.trim() === '1 / 3');
  assert.equal(await tab.locator('[data-search-expanded-role]').count(), 0);
  await ensureSidebarOpen();
  await tab.getByTestId('global-history-search-toggle').click();
  await tab.getByTestId('global-history-search-input').fill('SharedNeedle');
  await tab.getByTestId('global-history-search-load-more').waitFor({ state: 'visible' });
  assert.match(await tab.getByRole('search', { name: 'Global history search' }).innerText(), /1202 occurrences/);
  await tab.getByTestId('global-history-search-load-more').click();
  await tab.getByRole('search', { name: 'Global history search' }).getByRole('button', { name: /Search Remote, user/ }).first().click();
  await tab.locator('[data-search-target] mark').first().waitFor({ state: 'visible' });
  await tab.locator('[data-session-card-id]').filter({ hasText: 'Search Benchmark' }).first().click();
  await ensureSidebarOpen();
  await tab.getByTestId('session-history-search-toggle').click();
  await tab.getByRole('checkbox', { name: 'Search tool messages' }).check();
  await tab.getByRole('checkbox', { name: 'Search thinking messages' }).check();
  const countStart = performance.now();
  const searchRequestsBefore = evidence.requests.filter((url) => url.includes('/api/history/search')).length;
  await input.fill('RareNeedle');
  await tab.waitForFunction(() => document.querySelector('[data-testid="session-history-search-count"]')?.textContent?.trim() === '1 / 21');
  evidence.performance.browserCountMs = performance.now()-countStart;
  evidence.performance.browserCountRequests = evidence.requests.filter((url) => url.includes('/api/history/search')).length-searchRequestsBefore;
  await tab.waitForFunction(() => !document.querySelector('button[aria-label="Next result"]')?.disabled);
  await input.fill('commonterm');
  await tab.waitForFunction(() => document.querySelector('[data-testid="session-history-search-count"]')?.textContent?.trim() === '1 / 20000');
  await tab.waitForFunction(() => !document.querySelector('button[aria-label="Next result"]')?.disabled);
  await ensureSidebarOpen();
  await tab.getByTestId('global-history-search-toggle').click();
  await tab.getByTestId('global-history-search-input').fill('SharedNeedle');
  await tab.getByTestId('global-history-search-load-more').waitFor({ state: 'visible' });
  await tab.screenshot({ path: path.join(evidenceDir, 'desktop.png') });
  await tab.setViewportSize({ width: 390, height: 560 });
  await tab.waitForTimeout(250);
  assert.equal(await tab.getByTestId('message-navigation-dock').getAttribute('data-expanded'), 'false');
  assert.equal(await tab.getByTestId('session-history-search-toggle').isVisible(), false);
  assert.equal(await tab.getByTestId('global-history-search-toggle').isVisible(), false);
  await ensureSidebarOpen();
  assert.equal(await tab.getByTestId('session-history-search-toggle').isVisible(), true);
  assert.equal(await tab.getByTestId('global-history-search-toggle').isVisible(), true);
  await tab.getByTestId('mobile-message-navigation-toggle').click();
  await tab.waitForTimeout(200);
  assert.equal(await tab.getByTestId('session-history-search-toggle').isVisible(), false);
  assert.equal(await tab.getByTestId('global-history-search-toggle').isVisible(), false);
  evidence.tests.push('Mobile topbar toggles one whole shared panel; both search buttons fold together');
  const geometry = await tab.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, width: innerWidth,
    popup: document.querySelector('.global-history-search__popup')?.getBoundingClientRect().toJSON() }));
  assert.ok(geometry.scrollWidth <= geometry.width);
  evidence.geometry = geometry;
  await tab.screenshot({ path: path.join(evidenceDir, 'mobile.png') });
  await tab.setViewportSize({ width: 1440, height: 900 });
  await tab.waitForTimeout(200);
  await tab.locator('[title="App settings"]').first().click();
  await tab.locator('#app-settings-tab-appearance').click();
  await toggle.click();
  await tab.getByRole('button', { name: 'Close', exact: true }).last().click();
  assert.equal(await tab.locator('[data-search-target], .history-search-word, .chat-message-jump-highlight').count(), 0);
  const requestsBefore = evidence.requests.length;
  await tab.keyboard.press('Control+f');
  await tab.waitForTimeout(150);
  assert.equal(evidence.requests.length, requestsBefore);
  assert.equal(await tab.getByTestId('session-history-search-input').count(), 0);
  assert.equal(await tab.getByTestId('message-navigation-dock').count(), 1);
  assert.equal(await tab.getByTestId('session-history-search-toggle').count(), 0);
  await tab.locator('[title="App settings"]').first().click();
  await tab.locator('#app-settings-tab-appearance').click();
  await tab.getByRole('switch').filter({ hasText: 'Show message navigation rail' }).click();
  await tab.getByRole('button', { name: 'Close', exact: true }).last().click();
  assert.equal(await tab.getByTestId('chat-tools-sidebar').count(), 0);
  assert.equal(await tab.getByTestId('message-navigation-dock').count(), 0);
  evidence.tests.push('Chromium default-off, occurrence navigation, tool/thinking expansion, filters, QQ hidden target, global count/jump and hot unload');
  assert.equal(evidence.consoleErrors.length, 0);
  assert.equal(evidence.pageErrors.length, 0);
  assert.equal(evidence.requests.filter((url) => /:(8767|8768)(\/|$)/.test(url)).length, 0);
  await context.tracing.stop({ path: path.join(evidenceDir, 'trace.zip') });
  await context.close();
  console.log(JSON.stringify({ tests: evidence.tests, performance: evidence.performance }, null, 2));
} catch (error) {
  evidence.error = String(error.stack || error);
  if (tab) {
    evidence.failedBody = await tab.locator('body').innerText().catch(() => 'unavailable');
    await tab.screenshot({ path: path.join(evidenceDir, 'failure.png') }).catch(() => {});
  }
  throw error;
} finally {
  await browser?.close();
  if (server && server.exitCode === null) {
    // Only our child process, never a PID inferred from an occupied port.
    server.kill();
    await new Promise((resolve) => { if (server.exitCode !== null) resolve(); else { server.once('exit', resolve); setTimeout(resolve, 5000); } });
  }
  evidence.cleanup = spawnSync('powershell.exe', ['-NoProfile', '-Command',
    `@(Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess)`], { encoding: 'utf8', windowsHide: true }).stdout.trim();
  await fs.writeFile(path.join(evidenceDir, 'evidence.json'), JSON.stringify(evidence, null, 2));
  await fs.writeFile(path.join(evidenceDir, 'server.log'), logs);
  assert.equal(evidence.cleanup, '', 'owned test port released');
  console.log(`Evidence: ${evidenceDir}; disposable runtime retained: ${runtime}`);
}
