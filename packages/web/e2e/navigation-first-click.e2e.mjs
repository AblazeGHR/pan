/* global window, document, setTimeout, process, fetch, console */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { chromium } from '@playwright/test';

// Production bundle + disposable HTTP fixture; never use a running Pan service.
const root = path.resolve(process.env.PAN_NAV_CHECKOUT || path.resolve(import.meta.dirname, '../../..'));
const port = 8797;
const scrollbar = process.env.PAN_NAV_SCROLLBAR === '1' || process.argv.includes('--scrollbar');
const base = `http://127.0.0.1:${port}`;
const output = path.resolve(import.meta.dirname, '../test-results', `navigation-first-click-${Date.now()}`);
await fs.mkdir(output, { recursive: true });
await new Promise((resolve, reject) => {
  const probe = net.createServer(); probe.once('error', reject);
  probe.listen(port, '127.0.0.1', () => probe.close(resolve));
});
const server = spawn(process.env.PAN_E2E_PYTHON || 'D:/project/Pan-main/.venv/Scripts/python.exe', [path.join(root, 'packages/web/e2e/server.py')], {
  cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: output },
});
let logs = '';
server.stdout.on('data', chunk => { logs += chunk; });
server.stderr.on('data', chunk => { logs += chunk; });
const report = { root, port, scenarios: [], errors: [], passed: false };
let browser;
async function poll(read, check, timeout = 30000) {
  for (const end = Date.now() + timeout; Date.now() < end;) {
    const value = await read(); if (check(value)) return value;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('Polling timed out');
}
async function geometry(scroller, target) {
  return scroller.evaluate((el, target) => {
    const row = [...el.querySelectorAll('[data-scroll-anchor-key]')].find(r => r.dataset.scrollAnchorKey.includes(':nav-first-' + target + ':'));
    const v = el.getBoundingClientRect(), r = row?.getBoundingClientRect();
    return { target, found: Boolean(row), top: r ? r.top - v.top : null, height: r?.height,
      viewportHeight: v.height, scrollTop: el.scrollTop, contentHeight: el.scrollHeight,
      centerError: r ? r.top + r.height / 2 - v.top - v.height / 2 : null };
  }, target);
}
function assertLocated(value) {
  assert.ok(value.found, `target ${value.target} must be rendered on the first click`);
  assert.ok(value.top < value.viewportHeight && value.top + value.height > 0, `target ${value.target} must intersect the viewport`);
  // Edge targets cannot be centered once the native scroll range is clamped.
  const clamped = value.scrollTop <= 1 || value.scrollTop >= value.contentHeight - value.viewportHeight - 1;
  assert.ok(clamped || Math.abs(value.centerError) <= 16, `target ${value.target} must be centered within existing content padding: ${JSON.stringify(value)}`);
}
try {
  await poll(async () => { try { return (await fetch(`${base}/api/sessions?summary=1`)).ok; } catch { return false; } }, Boolean);
  browser = await chromium.launch({ headless: true, ignoreDefaultArgs: scrollbar ? ['--hide-scrollbars'] : [], args: scrollbar ? ['--disable-features=OverlayScrollbar,FluentOverlayScrollbar'] : [] });
  report.chromium = browser.version();
  const api = await browser.newContext();
  const sessions = await (await api.request.get(`${base}/api/sessions?summary=1`)).json();
  const session = (sessions.sessions ?? sessions).find(s => s.name === 'Alpha Session');
  assert.ok(session);
  const messages = Array.from({ length: 1200 }, (_, i) => {
    const slot = i % 20;
    const role = slot === 0 ? 'user' : slot <= 3 ? 'assistant' : slot % 2 ? 'tool' : 'thinking';
    const content = role === 'user' ? `JUMP row ${i}` : role === 'assistant'
      ? `${slot === 1 ? '@@@@by agent: fixture\n' : ''}REPORT row ${i}\n\n${Array.from({ length: 8 + (i % 11) * 7 }, (_, j) => `Paragraph ${i}.${j}: ${'variable width markdown text '.repeat(i % 4 + 1)}`).join('\n\n')}`
      : `${role} row ${i}: ${'diagnostic non-body text '.repeat(i % 12 + 1)}`;
    return { role, content, messageId: `nav-first-${i}` };
  });
  assert.equal((await api.request.post(`${base}/__e2e/append-history`, { data: { sessionId: session.id, messages } })).status(), 200);
  await api.close();
  for (const merge of [false, true]) {
    const context = await browser.newContext({ viewport: { width: 1120, height: 900 } });
    assert.equal((await context.request.put(`${base}/api/settings/ui`, { data: {
      mergeConsecutiveNonBodyBlocks: merge, showMessageNavigationRail: true, showTaskAgent: true,
      chatViewStyle: 'bubble', keepScrollOnSessionSwitch: true,
    } })).status(), 200);
    const page = await context.newPage();
    page.on('pageerror', error => report.errors.push(error.message));
    await page.goto(`${base}/react/?panE2E=1`);
    await page.locator('[data-session-card-id]').filter({ hasText: 'Alpha Session' }).first().click();
    await page.waitForFunction(() => !window.__panSessionStore.getState().historyLoading && window.__panSessionStore.getState().currentMessages.length > 0);
    const scroller = page.locator('.chat-view-stage .overflow-auto').first();
    if (scrollbar) {
      // Headless Chromium uses overlay bars. Give the fixture a visible native
      // gutter so mouse coordinates hit the browser thumb, not message text.
      await page.addStyleTag({ content: '.chat-view-stage .overflow-auto::-webkit-scrollbar { width: 17px; } .chat-view-stage .overflow-auto::-webkit-scrollbar-thumb { background: #888; min-height: 30px; } .chat-view-stage .overflow-auto::-webkit-scrollbar-button { display: none; }' });
      await page.waitForTimeout(200);
      const initial = await scroller.evaluate(el => {
        window.__scrollbarEvents = [];
        for (const name of ['pointerdown', 'pointermove', 'mousedown', 'mousemove', 'scroll']) document.addEventListener(name, e => {
          window.__scrollbarEvents.push({ name, target: e.target === el ? 'scroller' : e.target?.tagName, buttons: e.buttons, top: el.scrollTop });
        }, true);
        const r = el.getBoundingClientRect();
        return { x: r.right - 7, y: r.bottom - 10, destination: r.top + r.height * 0.4, gutter: el.offsetWidth - el.clientWidth, top: el.scrollTop, height: el.scrollHeight, viewport: el.clientHeight };
      });
      await page.mouse.move(initial.x, initial.y);
      await page.mouse.down();
      await page.mouse.move(initial.x, initial.destination, { steps: 40 });
      await page.waitForTimeout(250);
      await page.mouse.up();
      await page.waitForTimeout(1000);
      const afterDrag = await scroller.evaluate(el => ({ top: el.scrollTop, height: el.scrollHeight, viewport: el.clientHeight, events: window.__scrollbarEvents }));
      await context.request.post(`${base}/__e2e/append-history`, { data: { sessionId: session.id, messages: [{ role: 'assistant', content: 'New tail while reading older history', messageId: `scrollbar-tail-${merge}` }] } });
      // Drive the view update explicitly: append-history only persists fixture
      // rows and does not emit a Worker event. This is a store/render test.
      await page.evaluate(() => {
        const store = window.__panSessionStore, state = store.getState();
        store.setState({ currentMessages: [...state.currentMessages, { role: 'assistant', content: 'New tail while reading older history', messageId: 'scrollbar-visible-tail' }] });
      });
      await page.waitForTimeout(500);
      const afterAppend = await scroller.evaluate(el => ({ top: el.scrollTop, height: el.scrollHeight, viewport: el.clientHeight }));
      const scenario = { merge, initial, afterDrag, afterAppend };
      report.scenarios.push(scenario);
      console.log(JSON.stringify(scenario));
      assert.ok(initial.gutter > 0, 'test must use a real non-overlay native scrollbar');
      assert.ok(afterDrag.top < afterDrag.height - afterDrag.viewport - 100, 'native thumb drag must leave the bottom');
      assert.ok(afterAppend.top < afterAppend.height - afterAppend.viewport - 100, 'tail updates must not undo thumb browsing');
      await context.close();
      continue;
    }
    await page.locator('[data-testid="message-navigation-dock"]').hover();
    await page.locator('.message-navigation-rail[data-index-status="ready"]').waitFor();
    const scenario = { merge, clicks: [] };
    report.scenarios.push(scenario);
    for (const target of [100, 800, 400, 1100, 200, 100]) {
      for (const attempt of [1, 2]) {
        await page.locator(`.message-navigation-marker[title="JUMP row ${target}"]`).click();
        await page.waitForTimeout(1000);
        const position = await geometry(scroller, target);
        scenario.clicks.push({ attempt, ...position });
        assertLocated(position);
      }
    }
    // Worker reports can be taller than the viewport and have a cold estimate.
    await page.getByRole('tab', { name: /^Worker/ }).click();
    for (const target of [801, 401, 101]) {
      await page.locator(`.message-navigation-marker[title^="REPORT row ${target}"]`).click();
      await page.waitForTimeout(1000);
      const position = await geometry(scroller, target);
      scenario.clicks.push({ attempt: 1, kind: 'worker', ...position });
      assertLocated(position);
    }
    // A newer click must own the final viewport even during reconciliation.
    await page.getByRole('tab', { name: /^用户/ }).click();
    await page.locator('.message-navigation-marker[title="JUMP row 800"]').click();
    await page.waitForTimeout(60);
    await page.locator('.message-navigation-marker[title="JUMP row 200"]').click();
    await page.waitForTimeout(1000);
    scenario.rapidClick = await geometry(scroller, 200);
    assertLocated(scenario.rapidClick);
    // A subsequent genuine wheel must remain in control, with no snap-back.
    await scroller.hover();
    const before = await scroller.evaluate(el => el.scrollTop);
    await page.mouse.wheel(0, 300);
    await page.waitForTimeout(500);
    scenario.afterWheel = await scroller.evaluate(el => el.scrollTop);
    assert.ok(scenario.afterWheel > before + 100, 'navigation must hand control back to the next wheel');
    console.log(JSON.stringify({ merge, clicks: scenario.clicks, rapidClick: scenario.rapidClick }));
    await context.close();
  }
  assert.deepEqual(report.errors, []);
  report.passed = true;
} catch (error) {
  report.failure = error.stack;
  throw error;
} finally {
  await browser?.close();
  try {
    const identity = JSON.parse(await fs.readFile(path.join(output, 'server-identity.json'), 'utf8'));
    if (identity.port === port && path.resolve(identity.checkout) === root) process.kill(identity.pid);
  } catch { if (server.exitCode === null) server.kill(); }
  await poll(async () => new Promise(resolve => {
    const socket = net.connect({ host: '127.0.0.1', port });
    socket.once('connect', () => { socket.destroy(); resolve(false); });
    socket.once('error', () => { socket.destroy(); resolve(true); });
    socket.setTimeout(300, () => { socket.destroy(); resolve(false); });
  }), Boolean, 3000);
  report.cleanup = { portFree: true };
  await fs.writeFile(path.join(output, 'server.log'), logs);
  await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(report, null, 2));
  console.log(`Evidence: ${output}`);
}
