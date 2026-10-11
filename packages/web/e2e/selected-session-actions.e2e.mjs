/* global window, URL, process, fetch, console */
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdirSync, writeFileSync } from 'node:fs';
import { chromium, expect } from '@playwright/test';

const cwd = fileURLToPath(new URL('..', import.meta.url));
const port = await new Promise((resolve) => {
  const server = net.createServer();
  server.listen(0, '127.0.0.1', () => {
    const port = server.address().port;
    server.close(() => resolve(port));
  });
});
const base = `http://127.0.0.1:${port}`;
const output = fileURLToPath(new URL('../evidence/selected-session-actions/', import.meta.url));
mkdirSync(output, { recursive: true });
const server = spawn(
  process.execPath,
  ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
  { cwd, windowsHide: true, stdio: 'ignore' },
);
let browser;
const evidence = { port, results: [], requests: [], errors: [] };
try {
  for (let attempt = 0; ; attempt++) {
    if (attempt > 80 || server.exitCode !== null) throw new Error('Vite startup failed');
    try {
      if ((await fetch(`${base}/selected-session-actions-harness.html`)).ok) break;
    } catch {
      /* startup */
    }
    await delay(100);
  }
  browser = await chromium.launch({ headless: true });
  for (const width of [1280, 390, 320]) {
    const context = await browser.newContext({
      viewport: { width, height: 844 },
      isMobile: width < 768,
      hasTouch: width < 768,
    });
    const page = await context.newPage();
    let failJob = true;
    let failWorkspace = true;
    let delayJob;
    let holdJob = false;
    let sessions = ['a', 'b', 'c'].map((id) => ({
      id,
      name: `Session ${id}`,
      alwaysThinkingEnabled: false,
      effort: '',
      history: [],
      workspaceIds: ['w1'],
      unreadDoneGeneration: 4,
      unreadDoneReadGeneration: 1,
      unreadDoneCount: 3,
      msgBridgeEnabled: false,
    }));
    page.on('pageerror', (error) => evidence.errors.push(error.message));
    await page.route('**/*', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      assert.ok(![8767, 8768].includes(Number(url.port)), 'Protected service request');
      if (url.origin !== base)
        return route.fulfill({ status: 200, contentType: 'text/css', body: '' });
      if (!url.pathname.startsWith('/api/') && !url.pathname.startsWith('/ws'))
        return route.continue();
      const body = request.postDataJSON();
      evidence.requests.push({ width, path: url.pathname, method: request.method(), body });
      const reply = (data, status = 200) =>
        route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
      if (url.pathname === '/api/sessions') return reply({ sessions });
      if (url.pathname.endsWith('/unread-done/ack')) {
        const id = url.pathname.split('/')[3];
        if (id === 'b') return reply({ error: { message: 'ACK rejected' } }, 409);
        sessions = sessions.map((session) =>
          session.id === id
            ? {
                ...session,
                unreadDoneGeneration: 5,
                unreadDoneReadGeneration: body.observed,
                unreadDoneCount: 5 - body.observed,
              }
            : session,
        );
        return reply({ ok: true, ...sessions.find((session) => session.id === id) });
      }
      if (url.pathname.endsWith('/msg-bridge')) {
        const id = url.pathname.split('/')[3];
        if (id === 'b' && body.enabled)
          return reply({ ok: false, error: { message: 'Default contact unavailable' } });
        sessions = sessions.map((session) =>
          session.id === id ? { ...session, msgBridgeEnabled: body.enabled } : session,
        );
        return reply({ ok: true, msgBridgeEnabled: body.enabled });
      }
      if (url.pathname.endsWith('/workspaces')) {
        const id = url.pathname.split('/')[3];
        if (id === 'b' && failWorkspace) return reply({ ok: false, error: { message: 'Workspace save failed' } });
        sessions = sessions.map((session) =>
          session.id === id ? { ...session, workspaceIds: body.workspaceIds } : session,
        );
        return reply({ ok: true, session: sessions.find((session) => session.id === id) });
      }
      if (url.pathname === '/api/jobs') {
        if (holdJob)
          await new Promise((resolve) => {
            delayJob = resolve;
          });
        if (failJob) return reply({ detail: 'Save failed' }, 500);
        return reply({ job: { jobId: 'job-isolated', ...body } });
      }
      return reply({ ok: true });
    });
    await page.goto(`${base}/selected-session-actions-harness.html`);
    const open = async (item) => {
      await page.getByRole('button', { name: /Actions/ }).click();
      await page.getByRole('menuitem', { name: item, exact: true }).click();
    };
    await open('Read all');
    await page.getByRole('status').filter({ hasText: '1 succeeded, 1 failed' }).waitFor();
    assert.equal((await page.evaluate(() => window.sessionState()))[0].unreadDoneCount, 1);
    assert.equal((await page.evaluate(() => window.sessionState()))[1].unreadDoneCount, 3);
    await open('Bell on');
    await page.getByRole('status').filter({ hasText: 'Default contact unavailable' }).waitFor();
    await page.getByRole('button', { name: /Actions/ }).waitFor({ state: 'visible' });
    await open('Bell off');
    await page.getByRole('status').filter({ hasText: '2 succeeded, 0 failed' }).waitFor();

    await page.evaluate(() => { window.setManager('b', 'a'); window.selectTargets(['b']); });
    await open('Workspace');
    await page.getByRole('button', { name: 'Destination', exact: true }).click();
    const confirmation = page.getByRole('dialog', { name: 'Confirm detach from manager' });
    await confirmation.waitFor();
    await confirmation.getByRole('button', { name: 'Cancel', exact: true }).click();
    await page.getByRole('status').filter({ hasText: 'No Sessions moved' }).waitFor();
    assert.equal(evidence.requests.filter((request) => request.width === width && request.path.endsWith('/workspaces')).length, 0);
    await page.evaluate(() => { window.setManager('b', null); window.selectTargets(['a', 'b']); });
    await open('Workspace');
    await page.evaluate(() => window.selectTargets(['c']));
    await page.getByRole('button', { name: 'Destination', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Workspace save failed' }).waitFor();
    await expect(page.getByRole('alert')).toContainText('Currently in target: 1/2 (Session a)');
    failWorkspace = false;
    await page.getByRole('button', { name: 'Destination', exact: true }).click();
    await page.getByRole('status').filter({ hasText: 'Moved 1' }).waitFor();
    const moved = await page.evaluate(() => window.sessionState());
    assert.deepEqual(
      moved.map((session) => session.workspaceIds),
      [['w2'], ['w2'], ['w1']],
    );
    await page.evaluate(() => window.selectTargets(['a', 'b']));
    await open('群发');
    assert.equal(await page.getByLabel('Createable Job kind').count(), 0);
    await page.getByLabel('Message text').fill('isolated broadcast');
    await page.getByLabel('Delay seconds').fill('60');
    await page.evaluate(() => window.selectTargets(['c']));
    await page.getByRole('button', { name: 'Create job' }).click();
    await page.getByRole('alert').filter({ hasText: 'Save failed' }).waitFor();
    assert.equal(await page.getByLabel('Message text').inputValue(), 'isolated broadcast');
    failJob = false;
    holdJob = true;
    await page.getByRole('button', { name: 'Create job' }).click();
    await expect(page.getByRole('button', { name: 'Create job' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Cancel selection' })).toBeDisabled();
    await page.keyboard.press('Escape');
    assert.equal(await page.getByRole('dialog', { name: '群发消息' }).count(), 1);
    while (!delayJob) await delay(10);
    delayJob();
    await page
      .getByRole('status')
      .filter({ hasText: 'Broadcast Job created: job-isolated' })
      .waitFor();
    const jobs = evidence.requests.filter(
      (request) => request.width === width && request.path === '/api/jobs',
    );
    assert.equal(jobs.length, 2);
    assert.deepEqual(jobs[1].body.target, { sessionId: 'a', sessionIds: ['a', 'b'] });
    assert.deepEqual(jobs[1].body.schedule, { type: 'once', delaySeconds: 60 });
    assert.equal(jobs[1].body.kind, 'session-broadcast');
    assert.equal('source' in jobs[1].body, false);
    await open('群发');
    await page.getByLabel('Message schedule type').selectOption('weekly');
    await page.evaluate(() => window.clearToasts());
    await page.screenshot({
      path: `${output}/${width}-broadcast.png`,
      fullPage: true,
      animations: 'disabled',
    });
    const dialog = await page.getByRole('dialog', { name: '群发消息' }).boundingBox();
    assert.ok(
      dialog.x >= 0 &&
        dialog.y >= 0 &&
        dialog.x + dialog.width <= width &&
        dialog.y + dialog.height <= 844,
      'Dialog outside viewport',
    );
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    assert.equal(
      evidence.requests.filter((request) => request.width === width && request.path === '/api/jobs')
        .length,
      2,
    );
    await page.getByRole('button', { name: /Actions/ }).click();
    await page.evaluate(() => window.clearToasts());
    await page.screenshot({
      path: `${output}/${width}-menu.png`,
      fullPage: true,
      animations: 'disabled',
    });
    const menu = await page.getByRole('menu', { name: 'Selected Session actions' }).boundingBox();
    assert.ok(menu.x >= 0 && menu.x + menu.width <= width);
    await page.keyboard.press('Escape');
    assert.equal(await page.getByRole('menu', { name: 'Selected Session actions' }).count(), 0);
    evidence.results.push({
      width,
      passed: true,
      cases: [
        'partial read / generation race',
        'Bell on validation / off API',
        'workspace snapshot / manager detach cancellation / partial failure / retry',
        'broadcast snapshot / save failure / retry / duplicate guard / pending escape',
        'weekly controls / cancel',
        'menu viewport / escape',
      ],
    });
    await context.close();
  }
  assert.deepEqual(evidence.errors, []);
  console.log(JSON.stringify(evidence.results, null, 2));
} finally {
  writeFileSync(`${output}/results.json`, JSON.stringify(evidence, null, 2));
  await browser?.close();
  if (process.platform === 'win32')
    execFileSync('taskkill', ['/pid', String(server.pid), '/t', '/f'], {
      windowsHide: true,
      stdio: 'ignore',
    });
  else server.kill('SIGTERM');
}
