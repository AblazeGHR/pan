/** Production bundle and real HTTP/WS. Every API response comes from Pan. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium, expect } from '@playwright/test';

const [base, inputPath, out] = process.argv.slice(2);
const url = new URL(base);
assert.equal(url.hostname, '127.0.0.1');
assert.ok(!['8767', '8768'].includes(url.port));
const input = JSON.parse(await fs.readFile(inputPath, 'utf8'));
const key = process.env.PAN_TEST_JOB_KEY;
assert.ok(key);
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: 'msedge' });
const proof = { base, transport: 'real HTTP and WebSocket; no routes or response mocks', screenshots: [], geometry: [], errors: [], jobId: null };
let page;
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 }, colorScheme: 'dark' });
    page = await context.newPage();
    page.on('pageerror', error => proof.errors.push(error.message));
    await page.goto(base + '/react/automation');
    await page.getByLabel('Local user key', { exact: true }).fill(key);
    await page.getByRole('button', { name: 'Log in', exact: true }).click();
    await expect(page.getByRole('button', { name: 'New Job', exact: true })).toBeEnabled();
    const login = await (await page.request.post(base + '/api/automation/login', { data: { key } })).json();
    const headers = { Authorization: 'Bearer ' + login.token };
    async function shot(name, target) {
      if (target) await target.scrollIntoViewIfNeeded();
      if (name === 'job') await page.locator('[aria-label="Automation management"]').evaluate(el => { el.scrollTop = 0; });
      await page.evaluate(() => document.fonts.ready);
      const geometry = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth,
        panel: document.querySelector('[aria-label="Automation management"]')?.scrollWidth }));
      assert.ok(geometry.document <= width + 1 && geometry.panel <= width + 1, JSON.stringify(geometry));
      proof.geometry.push({ name, ...geometry });
      const file = `${name}-${width}.png`;
      await page.screenshot({ path: path.join(out, file), fullPage: true, animations: 'disabled' });
      proof.screenshots.push(file);
    }
    if (width === 1440) {
      await page.getByRole('button', { name: 'New Job', exact: true }).click();
      await page.getByLabel('Job name', { exact: true }).fill('Phase5 browser chain');
      for (const step of [1, 2]) {
        await page.getByRole('button', { name: 'Add step', exact: true }).click();
        await page.getByLabel(`Action for step ${step}`).selectOption('phase5_pause');
      }
      await shot('chain-editor', page.getByRole('region', { name: 'Action chain', exact: true }));
      await page.getByRole('button', { name: 'Add time trigger', exact: true }).click();
      await page.getByLabel('Scheduler Event').selectOption(input.sourceId);
      await shot('existing-scheduler-flow', page.getByRole('region', { name: 'Time trigger 1', exact: true }));
      await page.getByRole('button', { name: 'Save and enable', exact: true }).click();
      await expect(page.getByRole('article', { name: 'Job Phase5 browser chain', exact: true })).toBeVisible();
      const jobs = await (await page.request.get(base + '/api/jobs/action-jobs', { headers })).json();
      const job = jobs.jobs.find(j => j.name === 'Phase5 browser chain');
      assert.equal(job.config.steps.length, 2);
      assert.notEqual(job.config.steps[0].stepId, job.config.steps[1].stepId);
      proof.jobId = job.jobId;
      const run = await page.request.post(base + `/api/jobs/action-jobs/${job.jobId}/run-now`, {
        headers, data: { expectedRevision: job.configRevision } });
      assert.ok(run.ok(), await run.text());
      await expect.poll(async () => {
        const value = await (await page.request.get(base + `/api/jobs/${job.jobId}`, { headers })).json();
        return value.job?.lastExecution?.stepExecutions?.map(step => step.status);
      }).toEqual(['completed', 'completed']);
    }
    const card = page.getByRole('article', { name: 'Job Phase5 browser chain', exact: true });
    await expect(card).toContainText(input.sourceName);
    await expect(card.getByRole('button', { name: 'Open Trigger', exact: true })).toBeVisible();
    await shot('job', card);
    await page.getByRole('tab', { name: 'Trigger', exact: true }).click();
    await expect(page.getByRole('tab', { name: 'Trigger', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByText('Managed by Job: Phase5 browser chain', { exact: true })).toBeVisible();
    await shot('trigger');
    await page.getByRole('tab', { name: 'Event Register', exact: true }).click();
    await expect(page.getByRole('tab', { name: 'Event Register', exact: true })).toHaveAttribute('aria-selected', 'true');
    const source = page.getByRole('article', { name: `Scheduler ${input.sourceName}`, exact: true });
    await expect(source).toContainText('Phase5 browser chain');
    await shot('event-register', source);
    if (width === 390) {
      await page.getByRole('tab', { name: 'Job', exact: true }).click();
      await page.getByRole('button', { name: 'Edit Phase5 browser chain', exact: true }).click();
      await expect(page.getByLabel('Action for step 2')).toHaveValue('phase5_pause');
      await shot('chain-editor', page.getByRole('region', { name: 'Action chain', exact: true }));
      await expect(page.getByLabel('Scheduler Event')).toHaveValue(input.sourceId);
      await shot('existing-scheduler-flow', page.getByRole('region', { name: 'Time trigger 1', exact: true }));
    }
    await context.close();
  }
  assert.deepEqual(proof.errors, []);
  proof.ok = true;
} catch (error) {
  proof.failure = String(error);
  if (page && !page.isClosed()) {
    await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true });
    await fs.writeFile(path.join(out, 'failure.html'), await page.content());
  }
  throw error;
} finally {
  proof.browserVersion = browser.version();
  await browser.close();
  await fs.writeFile(path.join(out, 'browser.json'), JSON.stringify(proof, null, 2));
}
console.log(JSON.stringify({ ok: true, screenshots: proof.screenshots.length, jobId: proof.jobId }));
