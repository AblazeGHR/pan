import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from '@playwright/test';

if (process.argv[2] === '--build') {
  // Same production entry used by the accepted passive-Template browser gate.
  await import('./retirement-r14-production.mjs');
} else {
  const config = JSON.parse(await readFile(process.argv[2], 'utf8'));
  assert.ok(![8767, 8768].includes(Number(new URL(config.url).port)));
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = [], writes = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    page.on('request', (request) => {
      assert.equal(new URL(request.url()).origin, new URL(config.url).origin);
      if (request.method() !== 'GET') writes.push(new URL(request.url()).pathname);
    });
    await page.addInitScript((token) => { window.__ownedR14Token = token; }, config.token);
    await page.goto(config.url + '/react/retirement-r14-harness.html');
    if (config.phase === 'edit') {
      await page.getByRole('button', { name: '编辑 ' + config.name, exact: true }).click();
      await page.getByText('Job policies', { exact: true }).click();
      assert.equal(await page.getByLabel('Runtime notification target while pending').isChecked(), true);
      await page.getByLabel('Notification Manager contract').locator('summary').click();
      assert.match(await page.getByLabel('Notification Manager contract').innerText(), /outbox-reservation\+job-config-cas/);
      await page.getByLabel('targetRef', { exact: true }).fill(config.target);
      const saved = page.waitForResponse((r) => r.request().method() === 'PUT' && new URL(r.url()).pathname === '/api/jobs/' + config.jobId);
      await page.getByRole('button', { name: '保存定义', exact: true }).click();
      const response = await saved;
      assert.equal(response.status(), 200, await response.text());
      const result = await response.json();
      assert.equal(result.result.runtimeAudit.after.notification.recipients[0].targetRef, config.target);
      assert.equal(writes.length, 1);
    } else {
      await page.getByRole('button', { name: 'Runs ' + config.name, exact: true }).click();
      const detail = page.getByLabel('Notification receipts ' + config.runId);
      await detail.locator('summary').click();
      const visible = await detail.innerText();
      assert.match(visible, /"status": "acked"/);
      assert.ok(visible.includes(config.outboxId) && visible.includes(config.target));
      assert.match(visible, /payloadDigest/);
      assert.equal(writes.length, 0);
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ phase: config.phase, chromium: browser.version(), pageErrors: errors, writes,
      layer: 'fresh production CanonicalJobsView + real owned HTTP; no intercepted responses' }));
  } finally { await browser.close(); }
}
