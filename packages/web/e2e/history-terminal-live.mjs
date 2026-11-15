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
  // CBC failed result: the native reason lives in errors/errors_info (no
  // "result" field), while the assistant prose is ordinary content and must
  // never become the diagnosis or be duplicated as an error message.
  const c = await create('History-terminal-owned-CBC');
  await api('POST', '/api/spawn', { sessionId: c.id });
  await select(c.id);
  await expect(page.locator('[data-terminal-id]')).toHaveCount(0);
  await api('POST', `/api/sessions/${c.id}/queue`, { text: 'cbc-quota-result', clientMessageId: crypto.randomUUID() });
  const cbcHistory = async () => (await api('GET', `/api/sessions/${c.id}/history?limit=500`)).history;
  await expect.poll(async () => (await cbcHistory()).filter(row => row.kind === 'terminal').length).toBe(1);
  await expect.poll(() => page.evaluate(() => window.__heldTerminalFrames.length)).toBeGreaterThan(0);
  await page.evaluate(() => window.__heldTerminalFrames.splice(0).forEach(deliver => deliver()));
  const cbcRows = await cbcHistory();
  const cbcTerminal = cbcRows.find(row => row.kind === 'terminal');
  const cbcProseText = '429 您的使用量已超出频率限制（普通说明，不是诊断）。';
  assert.equal(cbcTerminal.terminal.status, 'error');
  assert.equal(cbcTerminal.terminal.error.code, 6004);
  assert.equal(cbcTerminal.terminal.error.source, 'cli.error');
  assert.match(cbcTerminal.terminal.error.message, /429 您的使用量已超出频率限制/);
  const cbcProse = cbcRows.find(row => row.messageId === cbcTerminal.terminal.anchorMessageId);
  assert.equal(cbcProse.content, cbcProseText);
  assert.notEqual(cbcProse.content, cbcTerminal.terminal.error.message);
  assert.deepEqual(cbcRows.filter(row => row.role === 'assistant').map(row => row.content), [cbcProseText]);
  const assertCbcCard = async () => {
    await expect(page.locator('[data-terminal-status="error"]')).toHaveCount(1);
    const card = page.locator('[data-terminal-status="error"]');
    const chat = page.locator('div.overflow-auto:has([data-scroll-anchor-key])').first();
    await expect(card).toContainText('429 您的使用量已超出频率限制');
    await expect(page.getByText('未提供错误详情', { exact: true })).toHaveCount(0);
    // The sidebar preview repeats the text, so count the chat row itself.
    await expect(chat.getByText(cbcProseText)).toHaveCount(1);
    if (!(await page.locator('details[open]').count())) {
      await page.getByText('诊断详情', { exact: true }).click();
    }
    await expect(card).toContainText('cli.error');
    await expect(card).toContainText('6004');
  };
  await assertCbcCard();
  await page.screenshot({ path: output + '/cbc-terminal.png', fullPage: true });
  await page.reload();
  await select(c.id);
  await assertCbcCard();
  evidence.cbcNativeReason = {
    sessionId: c.id, status: cbcTerminal.terminal.status,
    code: cbcTerminal.terminal.error.code, source: cbcTerminal.terminal.error.source,
    messageHasReason: true, assistantProsePreserved: true, proseIsDiagnosis: false,
    duplicateAssistantErrorRows: 0, refresh: true, diagnosticsExpanded: true,
    terminalId: cbcTerminal.messageId,
  };
  // Real record shape: the assistant body is byte-identical to errors[0]. The
  // error must appear once — as the red card — not twice (bubble + card).
  const d = await create('History-terminal-owned-CBC-echo');
  await api('POST', '/api/spawn', { sessionId: d.id });
  await select(d.id);
  await expect(page.locator('[data-terminal-id]')).toHaveCount(0);
  const echoHistory = async () => (await api('GET', `/api/sessions/${d.id}/history?limit=500`)).history;
  const enqueueEcho = text => api('POST', `/api/sessions/${d.id}/queue`, { text, clientMessageId: crypto.randomUUID() });
  await enqueueEcho('cbc-quota-echo');
  await expect.poll(async () => (await echoHistory()).filter(row => row.kind === 'terminal').length).toBe(1);
  await expect.poll(() => page.evaluate(() => window.__heldTerminalFrames.length)).toBeGreaterThan(0);
  await page.evaluate(() => window.__heldTerminalFrames.splice(0).forEach(deliver => deliver()));
  const echoRows = await echoHistory();
  const echoTerminal = echoRows.find(row => row.kind === 'terminal');
  const echoText = echoTerminal.terminal.error.message;
  assert.equal(echoTerminal.terminal.status, 'error');
  assert.equal(echoTerminal.terminal.error.code, 6004);
  assert.equal(echoTerminal.terminal.error.source, 'cli.error');
  // The durable assistant body is preserved verbatim and remains the anchor.
  const echoAssistant = echoRows.find(row => row.messageId === echoTerminal.terminal.anchorMessageId);
  assert.equal(echoAssistant.role, 'assistant');
  assert.equal(echoAssistant.content, echoText);
  assert.equal(echoRows.filter(row => row.role === 'assistant').length, 1);
  const echoChat = page.locator('div.overflow-auto:has([data-scroll-anchor-key])').first();
  const assertEchoCard = async () => {
    await expect(page.locator('[data-terminal-status="error"]')).toHaveCount(1);
    const card = page.locator('[data-terminal-status="error"]');
    await expect(card).toContainText('429 您的使用量已超出频率限制');
    await expect(card).toContainText('6004');
    await expect(page.getByText('未提供错误详情', { exact: true })).toHaveCount(0);
    // The card renders the reason twice (header + diagnostics <pre>). A third
    // chat copy would be the echoed assistant bubble rendered as a normal answer.
    await expect(card.getByText(echoText)).toHaveCount(2);
    await expect(echoChat.getByText(echoText)).toHaveCount(2);
  };
  await assertEchoCard();
  await page.screenshot({ path: output + '/cbc-echo-card.png', fullPage: true });
  await page.reload();
  await select(d.id);
  await assertEchoCard();
  // Push the echo turn out of the first page, then recover it through real
  // paginated HTTP; the fold must survive paging.
  await enqueueEcho('history-terminal-long');
  await expect.poll(async () => (await echoHistory()).filter(row => row.kind === 'terminal').length).toBe(2);
  await page.reload();
  await select(d.id);
  let echoPaged = false;
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.pathname.endsWith('/history') && Number(url.searchParams.get('before')) > 0) echoPaged = true;
  });
  await expect.poll(async () => {
    await echoChat.hover();
    await page.mouse.wheel(0, -100000);
    await echoChat.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll')); });
    return page.locator('[data-terminal-status="error"]').count();
  }, { timeout: 20000 }).toBe(1);
  assert.ok(echoPaged, 'older echo terminal must be recovered through real paginated HTTP');
  const echoPagedRow = (await echoHistory()).find(row => row.kind === 'terminal' &&
    row.terminal.status === 'error');
  assert.equal(echoPagedRow.messageId, echoTerminal.messageId);
  await expect(echoChat.getByText(echoText)).toHaveCount(2);
  await page.screenshot({ path: output + '/cbc-echo-paged.png', fullPage: true });
  const foldCardCopies = await page.locator('[data-terminal-status="error"]')
    .getByText(echoText).count();
  const foldChatCopies = await echoChat.getByText(echoText).count();
  evidence.cbcEchoFold = {
    sessionId: d.id, code: echoTerminal.terminal.error.code,
    source: echoTerminal.terminal.error.source, assistantBodyPreserved: true,
    cardCopies: foldCardCopies, chatCopies: foldChatCopies,
    outsideCardCopies: foldChatCopies - foldCardCopies,
    foldedIntoCard: true, refresh: true, pagination: echoPaged,
    duplicateFrameDeduped: true, terminalId: echoTerminal.messageId,
  };
  // Negative controls: previous completed turn with the identical body, a
  // same-prefix ordinary answer, and a failed run without native detail must
  // all keep their normal bubbles.
  const e = await create('History-terminal-owned-CBC-echo-negatives');
  await api('POST', '/api/spawn', { sessionId: e.id });
  await select(e.id);
  await expect(page.locator('[data-terminal-id]')).toHaveCount(0);
  const negativeHistory = async () => (await api('GET', `/api/sessions/${e.id}/history?limit=500`)).history;
  const enqueueNegative = text => api('POST', `/api/sessions/${e.id}/queue`, { text, clientMessageId: crypto.randomUUID() });
  await enqueueNegative('same-429-done');
  await expect.poll(async () => (await negativeHistory()).filter(row => row.kind === 'terminal').length).toBe(1);
  await enqueueNegative('cbc-quota-echo');
  await expect.poll(async () => (await negativeHistory()).filter(row => row.kind === 'terminal').length).toBe(2);
  await expect.poll(() => page.evaluate(() => window.__heldTerminalFrames.length)).toBeGreaterThan(0);
  await page.evaluate(() => window.__heldTerminalFrames.splice(0).forEach(deliver => deliver()));
  await enqueueNegative('cbc-unknown-error');
  await expect.poll(async () => (await negativeHistory()).filter(row => row.kind === 'terminal').length).toBe(3);
  await expect.poll(() => page.evaluate(() => window.__heldTerminalFrames.length)).toBeGreaterThan(0);
  await page.evaluate(() => window.__heldTerminalFrames.splice(0).forEach(deliver => deliver()));
  const negativeRows = await negativeHistory();
  const negativeTerminals = negativeRows.filter(row => row.kind === 'terminal');
  assert.deepEqual(negativeTerminals.map(row => row.terminal.status), ['done', 'error', 'error']);
  assert.deepEqual(negativeRows.filter(row => row.role === 'assistant').map(row => row.content), [
    echoText, echoText, '429 频率限制说明（本次没有结构化原因）。']);
  const assertNegatives = async () => {
    const chat = page.locator('div.overflow-auto:has([data-scroll-anchor-key])').first();
    const cards = page.locator('[data-terminal-status="error"]');
    // Only the failed echo card carries the provider text (2 rendered copies);
    // the previous completed turn with the identical body stays a normal bubble.
    await expect(cards.getByText(echoText)).toHaveCount(2);
    await expect(chat.getByText(echoText)).toHaveCount(3);
    await expect(chat.getByText('429 频率限制说明（本次没有结构化原因）。')).toHaveCount(1);
    await expect(cards).toHaveCount(2);
    await expect(page.getByText('未提供错误详情', { exact: true })).toHaveCount(1);
    await expect(page.locator('[data-terminal-status="done"]')).toHaveCount(1);
  };
  await assertNegatives();
  await page.screenshot({ path: output + '/cbc-echo-negatives.png', fullPage: true });
  await page.reload();
  await select(e.id);
  await assertNegatives();
  const negativeCardCopies = await page.locator('[data-terminal-status="error"]')
    .getByText(echoText).count();
  const negativeChatCopies = await page.locator('div.overflow-auto:has([data-scroll-anchor-key])')
    .first().getByText(echoText).count();
  evidence.cbcEchoNegatives = {
    sessionId: e.id, statuses: negativeTerminals.map(row => row.terminal.status),
    cardCopies: negativeCardCopies, chatCopies: negativeChatCopies,
    outsideCardCopies: negativeChatCopies - negativeCardCopies,
    previousDoneSameTextVisible: true, samePrefixProseVisible: true,
    unknownProseVisible: true, unknownPlaceholder: true, errorCards: 2,
    refresh: true,
  };
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
