import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'vite';
import { chromium } from '@playwright/test';

const root = fileURLToPath(new URL('..', import.meta.url));
if (process.argv[2] === '--build') {
  await build({ configFile: path.join(root, 'vite.config.ts'), mode: 'production', root: path.join(root, 'src'),
    build: { outDir: path.join(root, 'test-results/retirement-r25-production'), emptyOutDir: true,
      rollupOptions: { input: path.join(root, 'src/retirement-r25-harness.html') } } });
} else {
  const config = JSON.parse(await readFile(process.argv[2], 'utf8'));
  assert.ok(![8767, 8768].includes(Number(new URL(config.url).port)));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = [], frames = [], templateWrites = [];
    page.on('pageerror', e => errors.push(String(e)));
    page.on('websocket', socket => socket.on('framereceived', f => frames.push(JSON.parse(String(f.payload)))));
    page.on('request', request => {
      if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/job-templates')
        templateWrites.push(request.postDataJSON());
    });
    await page.addInitScript(token => { window.__ownedR14Token = token; }, config.token);
    await page.goto(config.url + '/react/retirement-r25-harness.html');
    await page.waitForFunction(() => document.querySelector('button'));
    // Real owned driver endpoint advances the canonical Run/outbox only;
    // browser responses and WS frames are never intercepted.
    const drive = await page.request.post(config.url + '/owned/drive');
    assert.equal(drive.status(), 200);
    const notice = page.getByText('Job delivery incomplete: ses_b', { exact: true });
    await notice.waitFor({ state: 'visible' });
    assert.equal(await notice.count(), 1);
    await page.request.post(config.url + '/owned/duplicate');
    await page.waitForTimeout(120);
    assert.equal(await notice.count(), 1);
    await page.getByRole('button', { name: 'JobTemplates', exact: true }).click();
    await page.getByRole('button', { name: 'New assign Template', exact: true }).click();
    await page.getByLabel('Template name', { exact: true }).fill('Runtime browser Template');
    await page.getByLabel('Session ID', { exact: true }).fill('ses_a');
    await page.getByLabel('Text', { exact: true }).fill('  runtime browser\nraw  ');
    await page.getByLabel('Use Delivery Manager runtime target/text before queue acceptance').check();
    await page.getByRole('button', { name: 'Save passive Template', exact: true }).click();
    await page.getByRole('form', { name: 'Simple assign Template' }).waitFor({ state: 'detached' });
    assert.equal(templateWrites.length, 1);
    assert.equal(templateWrites[0].definition.jobDefinition.steps[0].actionRef.id, 'product_assign_runtime_v1');
    const terminal = frames.filter(f => f.outboxId === config.outboxId);
    assert.ok(terminal.length >= 2);
    assert.ok(terminal.every(f => f.failedTargets[0] === 'ses_b'));
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ chromium: browser.version(), frames: terminal, templateWrites,
      pageErrors: errors, layer: 'fresh production React+real HTTP+original server.broadcast real WS' }));
  } finally { await browser.close(); }
}
