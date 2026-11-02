import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';
const base = process.env.PAN_E2E_BASE_URL;
const runtime = process.env.PAN_E2E_RUNTIME;
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
async function post(url, body) {
  const r = await fetch(base + url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  assert.ok(r.ok);
  return r.json();
}
try {
  const sessions = await (await fetch(base + '/api/sessions?summary=1')).json();
  const a = sessions.sessions.find((s) => s.name === 'Chat Stream');

  const raw =
    'RAW VERBATIM\n```html\n<b>literal</b>\n```\n[large](large.bin) [binary](binary.bin) [local](notes.md) [web](https://example.test/raw)';
  const files = {
    'large.bin': Buffer.alloc(5 * 1024 * 1024 + 1, 65),
    'binary.bin': Buffer.from([0xff, 0xfe, 0, 128, 42]),
  };
  for (const [name, bytes] of Object.entries(files))
    await fs.writeFile(path.join(runtime, 'session-workdir', name), bytes);
  await post('/__e2e/append-history', {
    sessionId: a.id,
    messages: [{ role: 'assistant', content: raw }],
  });
  await page.goto(base + '/react/');
  await page.locator('[data-session-card-id]').filter({ hasText: 'Chat Stream' }).first().click();
  await page.getByRole('link', { name: 'large', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Switch to Raw view', exact: true }).click();
  const body = page.locator('[data-raw-message-text]').filter({ hasText: 'RAW VERBATIM' });
  assert.equal(await body.textContent(), raw);
  assert.equal(await body.locator('b').count(), 0);
  await page.getByRole('button', { name: 'Switch to TUI view', exact: true }).click();
  for (const width of [1440, 390]) {
    for (const [name, bytes] of Object.entries(files)) {
      // Start each file from the chat route; mobile uses the same real link.
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(base + '/react/');
      await page
        .locator('[data-session-card-id]')
        .filter({ hasText: 'Chat Stream' })
        .first()
        .click();
      await page.getByRole('button', { name: 'Switch to TUI view', exact: true }).click();
      await page.setViewportSize({ width, height: 900 });
      const link = page.getByRole('link', {
        name: name === 'large.bin' ? 'large' : 'binary',
        exact: true,
      });
      await link.waitFor();
      await link.click();
      await page.getByRole('alert').filter({ hasText: '无法预览此文件' }).waitFor();
      assert.match(page.url(), /editor/);
      assert.equal(await page.locator('.monaco-editor').count(), 0);
      const download = page.waitForEvent('download');
      await page.getByRole('button', { name: '下载当前文件', exact: true }).click();
      const d = await download;
      assert.equal(await d.failure(), null);
      assert.deepEqual(await fs.readFile(await d.path()), bytes);
      assert.equal(
        await page.getByRole('button', { name: '保存文件', exact: true }).isDisabled(),
        true,
      );
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(base + '/react/');
  await page.locator('[data-session-card-id]').filter({ hasText: 'Chat Stream' }).first().click();
  await page.getByRole('button', { name: 'Switch to Raw view', exact: true }).click();
  await post('/__e2e/stream', {
    sessionId: a.id,
    event: { type: 'assistant', content: 'LIVE [local](notes.md) <i>literal</i>' },
  });
  await page
    .locator('[data-raw-message-text]')
    .filter({ hasText: 'LIVE [local](notes.md) <i>literal</i>' })
    .waitFor();
  await post('/__e2e/broadcast', {
    event: {
      type: 'worker.result',
      sessionId: a.id,
      workerId: 'e2e-browser-worker',
      generation: 0,
      status: 'done',
      result: 'TERMINAL [local](notes.md)',
    },
  });
  await page
    .locator('[data-raw-message-text]')
    .filter({ hasText: 'TERMINAL [local](notes.md)' })
    .waitFor();
  // Hold a real read response across a Session switch: the old failure must
  // neither navigate to Editor nor publish a tab under the new Session.
  await page.getByRole('button', { name: 'Switch to TUI view', exact: true }).click();
  let releaseRead;
  let readStarted;
  const started = new Promise((resolve) => {
    readStarted = resolve;
  });
  const held = new Promise((resolve) => {
    releaseRead = resolve;
  });
  await page.route('**/api/fs/read?**', async (route) => {
    if (new URL(route.request().url()).searchParams.get('path')?.endsWith('large.bin')) {
      const response = await route.fetch();
      readStarted();
      await held;
      await route.fulfill({ response });
    } else await route.continue();
  });
  await page.getByRole('link', { name: 'large', exact: true }).click();
  await started;
  await page.locator('[data-session-card-id]').filter({ hasText: 'Alpha Session' }).first().click();
  releaseRead();
  await page.waitForTimeout(250);
  assert.doesNotMatch(page.url(), /editor/);
  await page.unroute('**/api/fs/read?**');
  await page.getByRole('button', { name: 'Switch to Raw view', exact: true }).click();
  await post('/__e2e/stream', {
    sessionId: a.id,
    event: { type: 'assistant', content: 'FOREIGN [local](notes.md)' },
  });
  await page.waitForTimeout(250);
  assert.equal(
    await page.locator('[data-raw-message-text]').filter({ hasText: 'FOREIGN' }).count(),
    0,
  );
  assert.equal(
    await page.locator('[data-raw-message-text]').filter({ hasText: 'RAW VERBATIM' }).count(),
    0,
  );
  assert.deepEqual(errors, []);
  console.log(
    'PASS production HTTP Chromium: raw exact history/live, literal HTML, large/binary readonly Editor and exact downloads desktop/mobile, foreign Session negative control',
  );
} catch (error) {
  await page.screenshot({ path: path.join(runtime, 'failure.png') });
  console.log((await page.locator('body').innerText()).slice(0, 3000));
  throw error;
} finally {
  await browser.close();
}
