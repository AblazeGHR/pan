/* Owned authenticated HTTP/WS + production bundle. Only scheduling real WS
 * frames is controlled; no product route, response or event is fabricated. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { chromium, expect } from '@playwright/test';

const [base, output] = process.argv.slice(2);
assert.ok(base && ![8767, 8768].includes(Number(new URL(base).port)));
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const errors = [];
const evidence = { productionBundle: true, fakeCLI: true, authenticated: false };
let page;
try {
  const login = await context.request.post(base + '/api/automation/login', {
    data: { key: process.env.PAN_TEST_HISTORY_KEY },
  });
  assert.equal(login.status(), 200);
  evidence.authenticated = true;
  const api = async (method, path, data) => {
    const response = await context.request.fetch(base + path, { method, data, headers: { Origin: base } });
    assert.equal(response.status(), 200, `${method} ${path}: ${await response.text()}`);
    const value = await response.json();
    assert.ok(!value.error, JSON.stringify(value));
    return value;
  };
  const create = name => api('POST', '/api/sessions', { name, adapter: 'cbc',
    model: 'deepseek-v4-flash', outputMode: 'stream', mcpServers: [] });
  const a = await create('History-terminal-owned-A');
  const b = await create('History-terminal-owned-B');
  page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    const original = Object.getOwnPropertyDescriptor(WebSocket.prototype, 'onmessage');
    window.__heldTerminalFrames = [];
    window.__terminalFrames = [];
    Object.defineProperty(WebSocket.prototype, 'onmessage', {
      configurable: true,
      get() { return original.get.call(this); },
      set(handler) {
        original.set.call(this, handler && (event => {
          let data;
          try { data = JSON.parse(event.data); } catch { return handler.call(this, event); }
          if (data.type === 'worker.result') window.__terminalFrames.push({ status: data.status,
            id: data.historyTerminalId, taskSeq: data.taskSeq });
          if (data.type === 'worker.result' && data.status === 'error') {
            window.__heldTerminalFrames.push(() => { handler.call(this, event); handler.call(this, event); });
          } else handler.call(this, event);
        }));
      },
    });
  });
  await page.goto(`${base}/react/?session=${a.id}`);
  await expect(page.locator(`[data-session-card-id="${a.id}"]`)).toBeVisible();
  const select = async id => {
    await page.locator(`[data-session-card-id="${id}"]`).click();
  };
  await select(a.id);
  await api('POST', '/api/spawn', { sessionId: a.id });
  const enqueue = text => api('POST', `/api/sessions/${a.id}/queue`, { text, clientMessageId: crypto.randomUUID() });
  const history = () => api('GET', `/api/sessions/${a.id}/history?limit=500`);
  await enqueue('err-capacity-report');
  await expect.poll(async () => (await history()).history.filter(row => row.kind === 'terminal').length).toBe(1);
  await expect.poll(() => page.evaluate(() => window.__heldTerminalFrames.length)).toBe(1);
  await enqueue('later-success-body');
  await expect.poll(async () => (await history()).history.filter(row => row.kind === 'terminal').length).toBe(2);
  const durable = (await history()).history;
  const facts = durable.filter(row => row.kind === 'terminal');
  assert.deepEqual(facts.map(row => row.terminal.status), ['error', 'done']);
  assert.equal(facts[0].terminal.error.code, 'serverOverloaded');
  assert.equal(facts[0].terminal.error.source, 'codex_error_payload');
  assert.equal(durable.find(row => row.messageId === facts[0].terminal.anchorMessageId).content,
    'Synthetic assistant work summary; not an error diagnosis.');
  const assertDisplay = async () => {
    await expect(page.locator('[data-terminal-status="error"]')).toHaveCount(1);
    await expect(page.locator('[data-terminal-status="done"]')).toHaveCount(1);
    const positions = await page.evaluate(() => {
      const a = document.querySelector('[data-terminal-status="error"]');
      const b = document.querySelector('[data-terminal-status="done"]');
      return !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    });
    assert.ok(positions);
    await expect(page.getByText('[ERROR] Task completed', { exact: true })).toHaveCount(0);
    await expect(page.getByText('[DONE] Task completed', { exact: true })).toHaveCount(0);
  };
  // Deliver the old error twice after the newer execution's terminal.
  await page.evaluate(() => window.__heldTerminalFrames.splice(0).forEach(deliver => deliver()));
  evidence.actualWsFrames = await page.evaluate(() => window.__terminalFrames);
  assert.deepEqual(evidence.actualWsFrames.map(frame => frame.status), ['error', 'done']);
  await assertDisplay();
  await page.getByText('诊断详情', { exact: true }).click();
  await expect(page.locator('details[open]')).toContainText('serverOverloaded');
  await page.screenshot({ path: output + '/desktop.png', fullPage: true });
  await select(b.id);
  await expect(page.locator('[data-terminal-id]')).toHaveCount(0);
  await select(a.id);
  await assertDisplay();
  await page.reload();
  await select(a.id);
  await assertDisplay();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: output + '/mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await enqueue('history-terminal-long');
  await expect.poll(async () => (await history()).history.filter(row => row.kind === 'terminal').length).toBe(3);
  await page.reload();
  await select(a.id);
  let paged = false;
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.pathname.endsWith('/history') && Number(url.searchParams.get('before')) > 0) paged = true;
  });
  await expect.poll(() => page.locator('[data-scroll-anchor-key]').count()).toBeGreaterThan(0);
  assert.ok(await page.locator('[data-scroll-anchor-key]').count() < 130, 'real virtual list must omit offscreen rows');
  const scroller = page.locator('div.overflow-auto:has([data-scroll-anchor-key])').first();
  await expect.poll(async () => {
    await scroller.hover();
    await page.mouse.wheel(0, -100000);
    await scroller.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll')); });
    return page.locator('[data-terminal-status="error"]').count();
  }, { timeout: 15000 }).toBe(1);
  assert.ok(paged, 'older terminal must be recovered through real paginated HTTP');
  await expect(page.locator('[data-terminal-status="error"]')).toContainText('Selected model is at capacity');
  await page.screenshot({ path: output + '/paged-error.png', fullPage: true });
  const allFacts = (await history()).history.filter(row => row.kind === 'terminal');
  assert.deepEqual(allFacts.slice(0, 2).map(row => row.messageId), facts.map(row => row.messageId));
  Object.assign(evidence, { delayedOldErrorAfterDone: true, duplicateFrame: true, refresh: true,
    sessionSwitch: true, diagnosticsExpanded: true, terminalIds: allFacts.map(row => row.messageId), sessionId: a.id,
    fixedAnchor: true, order: facts.map(row => row.terminal.order), pageErrors: errors,
    pagination: paged, virtualization: true });
  assert.deepEqual(errors, []);
} finally {
  await fs.writeFile(output + '/browser.json', JSON.stringify(evidence, null, 2));
  await browser.close();
}
