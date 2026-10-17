// Called by the real HTTP pytest fixture; no route interception or browser mocks.
/* global process, console, window, document, WebSocket, fetch, setTimeout */
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';
const dependencyRoot = process.env.PAN_TEST_WEB_DEPENDENCIES || path.resolve('packages/web/node_modules');
const { chromium, expect } = await import(pathToFileURL(path.join(dependencyRoot, '@playwright/test/index.mjs')).href);
const [base, sid, entry, gate] = process.argv.slice(2);
assert.ok(!/:(8767|8768)$/.test(base));
const browser = await chromium.launch({headless: true});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  await page.addInitScript(() => {
    const NativeWebSocket = window.WebSocket;
    window.testSockets = [];
    window.WebSocket = class extends NativeWebSocket {
      constructor(...args) { super(...args); window.testSockets.push(this); }
    };
  });
  const requests = [];
  page.on('request', request => {
    assert.ok(!/:(8767|8768)\//.test(request.url()));
    if (request.method() === 'POST' && request.url().endsWith('/worker/delayed-restart')) requests.push(request.url());
  });
  await page.goto(base + '/react/');
  await page.locator(`[data-session-card-id="${sid}"]`).click();
  const settings = page.getByRole('button', {name: 'Session settings', exact: true});
  if (entry === 'settings') await settings.click();
  const button = entry === 'settings'
    ? page.getByRole('button', {name: '◷ Delayed restart', exact: true})
    : page.getByTitle('Delayed restart: after task completion, before queued work', {exact: true});
  await button.waitFor({state: 'visible'});
  assert.equal(await button.isEnabled(), true, 'running with locked queued message accepts delayed restart');
  const responsePromise = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/worker/delayed-restart'));
  await button.click();
  const response = await responsePromise;
  assert.equal(response.status(), 200);
  const body = await response.json();
  assert.equal(body.delayedRestart.status, 'pending');
  console.log(JSON.stringify({entry, http: response.status(), body}));
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.disabled && /Delayed restart|delayed restart pending/.test(b.textContent + b.title)));
  assert.equal(requests.length, 1);
  if (entry !== 'settings') await settings.click();
  await page.getByRole('status').filter({hasText: 'Restart pending: waiting for task completion'}).waitFor();
  const pendingButton = page.getByRole('button', {name: '◷ Delayed restart pending', exact: true});
  // The label and tooltip explain the intentional duplicate guard at the entry itself.
  await pendingButton.waitFor();
  assert.equal(await pendingButton.isDisabled(), true);
  assert.match(await pendingButton.getAttribute('title'), /pending/);
  if (entry === 'reconnect') {
    await page.keyboard.press('Escape');
    const other = await (await fetch(base + '/api/sessions', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: 'other-session', adapter: 'cbc'})})).json();
    // Leave A selected only after its pending intent was received. Reconnect on B.
    await page.locator(`[data-session-card-id="${other.id}"]`).click();
    await page.context().setOffline(true);
    await page.evaluate(() => window.testSockets.forEach(socket => socket.close()));
    await page.waitForFunction(() => window.testSockets.every(socket => socket.readyState !== WebSocket.OPEN));
    await fs.writeFile(gate, 'release');
    let completed;
    for (let attempt = 0; attempt < 100; attempt++) {
      const state = await (await fetch(base + `/api/sessions/${sid}/worker/delayed-restart`)).json();
      if (state.delayedRestart.status === 'completed') { completed = state.delayedRestart; break; }
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.equal(completed?.status, 'completed');
    // Start another real held turn on the replacement, recreating the reported
    // combination: running + locked backlog + a stale pending UI projection.
    await fs.unlink(gate);
    const queued = await (await fetch(base + `/api/sessions/${sid}/queue`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: 'hold-A', clientMessageId: 'running-after-restart'}),
    })).json();
    assert.ok(!queued.error);
    let workerStatus;
    for (let attempt = 0; attempt < 100; attempt++) {
      workerStatus = (await (await fetch(base + `/api/sessions/${sid}`)).json()).workerStatus;
      if (workerStatus === 'running') break;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.equal(workerStatus, 'running');
    await page.context().setOffline(false);
    await page.waitForTimeout(1500);
    await page.locator(`[data-session-card-id="${sid}"]`).click();
    const clock = page.locator('[data-testid="topbar"] button').filter({has: page.locator('svg.lucide-clock')});
    await clock.waitFor();
    await expect(clock).toBeEnabled({timeout: 5000});
    console.log('AFTER_RECONNECT', {workerStatus, completed, button: await clock.evaluate(b => ({disabled: b.disabled, title: b.title}))});
    assert.equal(await clock.isEnabled(), true, 'returning to A must refresh its missed terminal restart state');
  }
} finally {
  await browser.close();
}
