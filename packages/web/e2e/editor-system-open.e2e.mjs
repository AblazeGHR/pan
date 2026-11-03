import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

const base = process.env.PAN_E2E_BASE_URL;
const runtime = process.env.PAN_E2E_RUNTIME;
const callsPath = path.join(runtime, 'opener-calls.jsonl');
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));

async function post(url, body) {
  return fetch(base + url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

async function sessionId() {
  const sessions = await (await fetch(base + '/api/sessions?summary=1')).json();
  return sessions.sessions.find((session) => session.name === 'Chat Stream').id;
}

async function readCalls() {
  try {
    return (await fs.readFile(callsPath, 'utf8'))
      .split('\n')
      .filter(Boolean)
      .map((line) => JSON.parse(line));
  } catch (error) {
    if (error.code === 'ENOENT') return [];
    throw error;
  }
}

async function waitForCall(kind, name, expected) {
  for (const deadline = Date.now() + 5000; Date.now() < deadline; ) {
    const calls = (await readCalls()).filter(
      (call) => call.kind === kind && path.basename(call.path) === name,
    );
    if (calls.length >= expected) return calls.at(-1);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.fail(`missing ${kind} call for ${name}: ${JSON.stringify(await readCalls())}`);
}

async function openEditor(linkName) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(base + '/react/');
  await page
    .locator('[data-session-card-id]')
    .filter({ hasText: 'Chat Stream' })
    .first()
    .click();
  const link = page.getByRole('link', { name: linkName, exact: true });
  await link.waitFor();
  await link.click();
  await page.getByTestId('editor-file-topbar').waitFor();
  assert.match(page.url(), /editor/);
}

async function osActions() {
  const open = page.getByRole('button', { name: '用系统默认方式打开', exact: true });
  const reveal = page.getByRole('button', { name: '在系统资源管理器中定位', exact: true });
  await open.waitFor();
  await reveal.waitFor();
  return { open, reveal };
}

async function clickAndWait(requestPath, button) {
  const response = page.waitForResponse(
    (candidate) =>
      candidate.url().includes(requestPath) && candidate.request().method() === 'POST',
  );
  await button.click();
  const result = await response;
  assert.equal(result.status(), 200, `${requestPath} -> ${result.status()}`);
  const body = await result.json();
  assert.equal(body.ok, true, `${requestPath} ok`);
}

try {
  const id = await sessionId();

  await openEditor('notes');
  const notes = await osActions();
  const platformInfo = await page.evaluate(async () => (await fetch('/api/platform')).json());
  console.log('platform capabilities', platformInfo);
  console.log('reveal state', {
    enabled: await notes.reveal.isEnabled(),
    title: await notes.reveal.getAttribute('title'),
  });
  assert.equal(platformInfo.capabilities['fileManager.reveal'], true);
  assert.equal(await notes.open.isEnabled(), true);
  assert.equal(await notes.reveal.isEnabled(), true);
  await clickAndWait('/api/editor/open-external', notes.open);
  await waitForCall('open', 'notes.md', 1);
  await clickAndWait('/api/editor/reveal-external', notes.reveal);
  await waitForCall('reveal', 'notes.md', 1);

  // The real endpoint keeps the same traversal fence for both actions.
  for (const endpoint of ['open-external', 'reveal-external']) {
    const traversal = await post(`/api/editor/${endpoint}`, { session_id: id, path: '../outside.txt' });
    assert.equal(traversal.status, 403, `${endpoint} traversal`);
    const missing = await post(`/api/editor/${endpoint}`, { session_id: id, path: 'missing.md' });
    assert.equal(missing.status, 404, `${endpoint} missing`);
    const directory = await post(`/api/editor/${endpoint}`, { session_id: id, path: '.' });
    assert.equal(directory.status, 400, `${endpoint} directory`);
    const unknown = await post(`/api/editor/${endpoint}`, { session_id: 'ses_missing', path: 'notes.md' });
    assert.equal(unknown.status, 400, `${endpoint} unknown session has no workdir`);
  }
  assert.equal((await readCalls()).length, 2, 'negative controls must not call the opener');

  for (const [link, file, message] of [
    ['binary', 'binary.bin', 'Cannot read binary file'],
    ['large', 'large.bin', 'File too large'],
  ]) {
    await openEditor(link);
    await page.getByRole('alert').filter({ hasText: message }).waitFor();
    assert.equal(await page.getByRole('button', { name: '保存文件', exact: true }).isDisabled(), true);
    const actions = await osActions();
    assert.equal(await actions.open.isEnabled(), true, `${file} open enabled`);
    assert.equal(await actions.reveal.isEnabled(), true, `${file} reveal enabled`);
    await clickAndWait('/api/editor/open-external', actions.open);
    await waitForCall('open', file, 1);
    await clickAndWait('/api/editor/reveal-external', actions.reveal);
    await waitForCall('reveal', file, 1);
  }

  const diskBefore = await fs.readFile(path.join(runtime, 'session-workdir', 'dirty.txt'), 'utf8');
  await openEditor('dirty');
  await page.locator('.monaco-editor .view-lines').first().waitFor();
  await page.locator('.monaco-editor .view-lines').first().click();
  await page.keyboard.press('Control+A');
  await page.keyboard.type('DIRTY BUFFER ONLY');
  const save = page.getByRole('button', { name: '保存文件', exact: true });
  await page.waitForFunction(
    () =>
      [...document.querySelectorAll('button')].some(
        (button) => button.getAttribute('aria-label') === '保存文件' && !button.disabled,
      ),
    undefined,
    { timeout: 5000 },
  );
  assert.equal(await save.isEnabled(), true);
  const dirtyActions = await osActions();
  assert.equal(await dirtyActions.open.isEnabled(), true);
  assert.match(await dirtyActions.open.getAttribute('title'), /磁盘上的版本/);
  const beforeOpen = (await readCalls()).length;
  await clickAndWait('/api/editor/open-external', dirtyActions.open);
  await waitForCall('open', 'dirty.txt', 1);
  await page.getByText('已打开磁盘上的版本，未保存的修改未写入').waitFor();
  const diskAfter = await fs.readFile(path.join(runtime, 'session-workdir', 'dirty.txt'), 'utf8');
  assert.equal(diskAfter, diskBefore, 'opening must not flush the dirty buffer');
  await clickAndWait('/api/editor/reveal-external', dirtyActions.reveal);
  await waitForCall('reveal', 'dirty.txt', 1);
  assert.equal((await readCalls()).length, beforeOpen + 2);

  const callsBeforeDisabled = (await readCalls()).length;
  const disabled = await post('/__e2e/reveal-capability', { available: false });
  assert.equal(disabled.status, 200);
  await openEditor('notes');
  await page.waitForFunction(
    () =>
      document.querySelector('button[aria-label="在系统资源管理器中定位"]')?.disabled ===
      true,
  );
  const disabledReveal = page.getByRole('button', {
    name: '在系统资源管理器中定位',
    exact: true,
  });
  assert.equal(await disabledReveal.isDisabled(), true);
  assert.equal(
    await disabledReveal.getAttribute('title'),
    '无法在系统资源管理器中定位：当前环境没有可用的资源管理器',
  );
  assert.equal(
    await page.getByRole('button', { name: '用系统默认方式打开', exact: true }).isEnabled(),
    true,
  );
  const apiDisabled = await post('/api/editor/reveal-external', {
    session_id: id,
    path: 'notes.md',
  });
  assert.equal(apiDisabled.status, 501);
  assert.equal((await readCalls()).length, callsBeforeDisabled);

  const enabled = await post('/__e2e/reveal-capability', { available: true });
  assert.equal(enabled.status, 200);
  await openEditor('notes');
  await page.waitForFunction(
    () =>
      document.querySelector('button[aria-label="在系统资源管理器中定位"]')?.disabled ===
      false,
  );
  const enabledReveal = page.getByRole('button', {
    name: '在系统资源管理器中定位',
    exact: true,
  });
  assert.equal(await enabledReveal.isEnabled(), true);

  await page.setViewportSize({ width: 390, height: 900 });
  assert.equal(await page.getByRole('button', { name: '用系统默认方式打开', exact: true }).isVisible(), true);
  assert.equal(await enabledReveal.isVisible(), true);
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    true,
    '390px bar must not overflow',
  );

  assert.deepEqual(errors, []);
  console.log(
    'PASS production HTTP Chromium: open+reveal notes, failed binary/large tabs, dirty disk-only open, capability false disables+501, 403/404/400 negative controls, 390px fit',
  );
} catch (error) {
  await page.screenshot({ path: path.join(runtime, 'failure.png') });
  console.log((await page.locator('body').innerText()).slice(0, 3000));
  throw error;
} finally {
  await browser.close();
}
