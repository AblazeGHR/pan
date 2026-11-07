import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { chromium, expect } from '@playwright/test';
const root = path.resolve(import.meta.dirname, '../../..');
const output = path.resolve(import.meta.dirname, '../test-results/session-creating', `${Date.now()}`);
await fs.mkdir(output, { recursive: true });
const probe = net.createServer();
await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
const port = probe.address().port;
await new Promise(resolve => probe.close(resolve));
assert(![8767, 8768].includes(port));
const base = `http://127.0.0.1:${port}`;
const server = spawn(process.env.PAN_E2E_PYTHON || 'D:/project/Pan/.venv/Scripts/python.exe', [path.join(import.meta.dirname, 'session-creating-server.py')], {
  cwd: root, windowsHide: true, env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: path.join(output, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'],
});
let logs = '';
server.stdout.on('data', data => logs += data);
server.stderr.on('data', data => logs += data);
const api = async (route, body) => (await fetch(base + route, body === undefined ? {} : { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })).json();
const evidence = { port, serverPid: server.pid, root, output, stages: [], ws: [], requests: [] };
let browser;
try {
  await expect.poll(async () => { try { return (await api('/__e2e/identity')).checkout; } catch { return ''; } }, { timeout: 60000 }).toBe(root);
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  const toasts = [];
  evidence.toasts = toasts;
  await page.exposeFunction('captureToast', text => toasts.push(text));
  await page.addInitScript(() => new MutationObserver(() => {
    for (const element of document.querySelectorAll('[role="alert"], [role="status"], .pointer-events-auto')) {
      const text = element.textContent;
      if (text) window.captureToast(text);
    }
  }).observe(document, { childList: true, subtree: true }));
  page.on('response', async response => { if (response.url() === base + '/api/sessions' && response.request().method() === 'POST') evidence.createResponse = await response.json(); });
  page.on('request', request => { if (request.url().includes('/api/sessions/')) evidence.requests.push(request.url()); });
  page.on('websocket', socket => socket.on('framereceived', event => { if (String(event.payload).match(/session\.(created|deleted)/)) evidence.ws.push(String(event.payload)); }));
  await page.goto(base + '/react/');
  const cards = page.locator('[data-session-card-id]');
  await expect(cards.first()).toBeVisible();
  const other = await cards.first().getAttribute('data-session-card-id');
  await page.locator(`[data-session-card-id="${other}"]`).click();
  await page.waitForLoadState('networkidle');
  const active = page.locator('[data-session-card-id].border-l-accent');
  const quick = page.getByTitle('Quick new session', { exact: true });
  const create = async mode => {
    await api('/__creating/arm', { mode });
    await quick.click();
    await expect.poll(async () => (await api('/__creating/state')).entered).toBe(true);
    await expect(active).toHaveAttribute('data-session-card-id', /^__pending_/);
    await expect(page.locator('[contenteditable="true"]').first()).toBeVisible();
    return active.getAttribute('data-session-card-id');
  };
  const placeholder = await create('success');
  // Force a genuine WS -> list refresh while persistence is held.
  await api('/__e2e/broadcast', { event: { type: 'session.updated', sessionId: other } });
  await expect(active).toHaveAttribute('data-session-card-id', placeholder);
  assert(!evidence.requests.some(url => url.includes('__pending_')));
  await api('/__creating/release/before', {});
  await expect.poll(async () => (await api('/__creating/state')).committed).toBe(true);
  await expect.poll(() => evidence.ws.length).toBeGreaterThan(0);
  await expect(active).toHaveAttribute('data-session-card-id', placeholder);
  const list = await api('/api/sessions?summary=1');
  const created = list.sessions.find(session => session.name.startsWith('session-'));
  assert(created, JSON.stringify(list).slice(0, 300));
  await api('/__creating/release/after', {});
  await expect(active).toHaveAttribute('data-session-card-id', created.id);
  await expect.poll(() => evidence.requests.some(url => url.includes(`/${created.id}/draft`))).toBe(true);
  assert(!toasts.some(text => text.includes('Session does not exist')));
  evidence.stages.push({ immediatePage: true, wsBeforeResponse: true, identity: created.id, placeholder });
  await create('failure');
  await page.locator(`[data-session-card-id="${other}"]`).click();
  await api('/__creating/release/before', {});
  await expect(page.getByText('controlled creation failure', { exact: true })).toBeVisible();
  await expect(active).toHaveAttribute('data-session-card-id', other);
  evidence.stages.push({ failureVisible: true, lateFailurePreservesSelection: true });
  await create('success');
  await page.locator(`[data-session-card-id="${other}"]`).click();
  await api('/__creating/release/before', {});
  await expect.poll(async () => (await api('/__creating/state')).committed).toBe(true);
  await api('/__creating/release/after', {});
  await expect(page.getByText('Session created', { exact: true }).first()).toBeVisible();
  await expect(active).toHaveAttribute('data-session-card-id', other);
  // Delete the server identity while its create response is held (WS wins).
  const previousIds = new Set((await api('/api/sessions?summary=1')).sessions.map(item => item.id));
  const deletedPlaceholder = await create('success');
  await api('/__creating/release/before', {});
  await expect.poll(async () => (await api('/__creating/state')).committed).toBe(true);
  const deleting = (await api('/api/sessions?summary=1')).sessions.find(item => !previousIds.has(item.id));
  assert(deleting);
  const deletedEventCount = evidence.ws.length;
  await fetch(base + `/api/sessions/${deleting.id}`, { method: 'DELETE' });
  await expect.poll(() => evidence.ws.length).toBeGreaterThan(deletedEventCount);
  await api('/__creating/release/after', {});
  await expect(page.locator(`[data-session-card-id="${deletedPlaceholder}"]`)).toHaveCount(0);
  await expect(page.locator(`[data-session-card-id="${deleting.id}"]`)).toHaveCount(0);
  evidence.stages.push({ deletedBeforeResponse: deleting.id, notResurrected: true });
  const missing = await api('/api/sessions/ses_absent_creating_negative/draft');
  assert.equal(missing.error.message, 'Session does not exist');
  const missingHistory = await fetch(base + '/api/sessions/ses_absent_creating_negative/navigation');
  assert.equal(missingHistory.status, 404);
  evidence.realHttp404 = missingHistory.status;
  assert(!evidence.requests.some(url => url.includes('__pending_')));
  evidence.stages.push({ lateSuccessPreservesSelection: true, realMissing: missing });
  await page.screenshot({ path: path.join(output, 'final.png') });
  evidence.result = 'passed';
} catch (error) {
  evidence.result = 'failed'; evidence.error = String(error); throw error;
} finally {
  await browser?.close();
  server.kill();
  await new Promise(resolve => server.once('exit', resolve));
  evidence.serverExited = server.exitCode !== null || server.signalCode !== null;
  await expect.poll(async () => {
    evidence.portClosed = await new Promise(resolve => {
      const socket = net.connect({ host: '127.0.0.1', port });
      socket.once('connect', () => { socket.destroy(); resolve(false); });
      socket.once('error', () => resolve(true));
    });
    return evidence.portClosed;
  }, { timeout: 10000 }).toBe(true);
  await fs.writeFile(path.join(output, 'server.log'), logs);
  await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({ result: evidence.result, output }));
}
