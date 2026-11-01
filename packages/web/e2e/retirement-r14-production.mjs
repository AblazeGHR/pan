import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'vite';
import { chromium } from '@playwright/test';

const root = fileURLToPath(new URL('..', import.meta.url));
if (process.argv[2] === '--build') {
  await build({ configFile: path.join(root, 'vite.config.ts'), mode: 'production', root: path.join(root, 'src'),
    build: { outDir: path.join(root, 'test-results/retirement-r14-production'), emptyOutDir: true,
      rollupOptions: { input: path.join(root, 'src/retirement-r14-harness.html') } } });
} else {
  const config = JSON.parse(await readFile(process.argv[2], 'utf8'));
  assert.ok(![8767, 8768].includes(new URL(config.url).port * 1));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = [], writes = [], instances = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    page.on('request', (request) => {
      assert.equal(new URL(request.url()).origin, new URL(config.url).origin);
      if (request.method() !== 'GET') writes.push(new URL(request.url()).pathname);
    });
    await page.addInitScript((token) => { window.__ownedR14Token = token; }, config.token);
    await page.goto(config.url + '/react/retirement-r14-harness.html');
    const click = (name) => page.getByRole('button', { name, exact: true }).click();
    await click('JobTemplates');
    for (const kind of ['assign', 'shell']) {
      const name = 'Browser ' + kind;
      await click(`New ${kind} Template`);
      await page.getByLabel('Template name', { exact: true }).fill(name);
      if (kind === 'assign') {
        await page.getByLabel('Session ID', { exact: true }).fill(config.sessionId);
        await page.getByLabel('Text', { exact: true }).fill(config.text);
      } else {
        await page.getByLabel('Command', { exact: true }).fill(config.command);
        await page.getByLabel('Working directory', { exact: true }).fill(config.cwd);
      }
      assert.equal(writes.length, instances.length * 3);
      await click('Save passive Template');
      await click('Publish ' + name);
      await click('Instantiate ' + name);
      assert.equal(await page.getByLabel('Enable Job and configured subscriptions').isChecked(), false);
      const response = page.waitForResponse((r) => new URL(r.url()).pathname.endsWith('/instantiate') && r.request().method() === 'POST');
      await click('Create from Template');
      const result = await response;
      assert.equal(result.status(), 201);
      const receipt = await result.json();
      instances.push({ kind, operationId: receipt.operationId, ...receipt.result.objectIds });
      await page.getByRole('region', { name: 'Instantiate Template' }).waitFor({ state: 'detached' });
    }
    assert.deepEqual(errors, []);
    assert.equal(writes.length, 6);
    console.log(JSON.stringify({ chromium: browser.version(), pageErrors: errors, writes, instances,
      layer: 'fresh Vite production bundle + real owned canonical HTTP, no intercepted API replies' }));
  } finally { await browser.close(); }
}
