import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';
const [base, inputFile, out, profile] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || /:(8767|8768)$/.test(base)) throw Error('Isolated port required');
const input = JSON.parse(await fs.readFile(inputFile, 'utf8'));
const context = await chromium.launchPersistentContext(profile, { headless: true, viewport: { width: 1280, height: 1000 } });
const page = await context.newPage();
const requests = [], errors = [];
page.on('request', request => { if (request.url().includes('/api/')) requests.push({ method: request.method(), path: new URL(request.url()).pathname }); });
page.on('pageerror', error => errors.push(error.message));
try {
  await page.goto(base + '/react/jobs');
  // Latest authorization must hide new Jobs until user login.
  await expect(page.getByText('New Action boundary', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'New Job', exact: true }).click();
  await page.getByLabel('Local user key', { exact: true }).fill(process.env.PAN_TEST_JOB_KEY);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Unified Jobs', exact: true }).click();
  await expect(page.getByText('New Action boundary', { exact: true })).toBeVisible();
  await expect(page.getByText('Legacy tree boundary', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Legacy Jobs / 旧 Job', exact: true }).click();
  await expect(page.getByText('Legacy tree boundary', { exact: true })).toBeVisible();
  await expect(page.getByText('Legacy timer boundary', { exact: true })).toBeVisible();
  await expect(page.getByText('New Action boundary', { exact: true })).toHaveCount(0);
  // Old create reuses the existing form and POST /api/jobs, never action-jobs.
  await page.getByRole('button', { name: 'New legacy Job', exact: true }).click();
  const create = page.getByRole('dialog', { name: 'New legacy Job' });
  await expect(create.getByLabel('Job user authorization')).toHaveCount(0);
  await create.getByRole('button', { name: '自定义', exact: true }).click();
  await create.getByLabel('Createable Job kind', { exact: true }).selectOption('session-message');
  await create.getByLabel('Name', { exact: true }).fill('GUI legacy created');
  await create.getByLabel('Target session', { exact: true }).selectOption(input.sessionId);
  await create.getByLabel('Message text', { exact: true }).fill('GUI future message');
  await create.getByLabel('Message schedule type', { exact: true }).selectOption('interval');
  await create.getByLabel('Message interval seconds', { exact: true }).fill('3600');
  const createdResponse = page.waitForResponse(r => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await create.getByRole('button', { name: 'Create job', exact: true }).click();
  const createdBody = await (await createdResponse).json();
  if (!createdBody.ok) throw Error('Legacy create: ' + JSON.stringify(createdBody));
  await expect(create).toHaveCount(0);
  await expect(page.getByText('GUI legacy created', { exact: true })).toBeVisible();
  const search = page.getByRole('searchbox', { name: 'Search jobs' });
  await search.fill('Legacy tree boundary');
  await page.getByRole('button', { name: 'Unified Jobs', exact: true }).click();
  await expect(search).toHaveValue('');
  await expect(page.getByText('New Action boundary', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Legacy Jobs / 旧 Job', exact: true }).click();
  await expect(search).toHaveValue('Legacy tree boundary');
  await search.fill('');
  await page.getByText('Legacy tree boundary', { exact: true }).click();
  await expect(page.getByLabel('Legacy execution details')).toBeVisible();
  await page.getByText('Original record / log path / delivery details', { exact: true }).click();
  await expect(page.getByText('Unified execution', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Action reference', { exact: true })).toHaveCount(0);
  await expect.poll(() => requests.some(row => row.path === `/api/background-jobs/${input.processId}` && row.method === 'GET')).toBeTruthy();
  await page.getByRole('dialog', { name: 'Job detail' }).getByRole('button', { name: 'Cancel job', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Retry legacy Job', exact: true })).toBeVisible();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Retry legacy Job', exact: true }).click();
  await expect.poll(() => requests.some(row => row.path === `/api/background-jobs/${input.processId}/retry` && row.method === 'POST')).toBeTruthy();
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  // Running DELETE must reach production cleanup (no extra status-only gate).
  async function deleteFromDrawer(name) {
    await page.getByText(name, { exact: true }).click();
    await page.getByRole('dialog', { name: 'Job detail' }).getByRole('button', { name: 'Delete', exact: true }).click();
    await page.getByRole('dialog', { name: 'Delete job' }).getByRole('button', { name: 'Delete', exact: true }).click();
  }
  const deleteResponse = page.waitForResponse(r => r.url().endsWith(`/api/jobs/${input.deleteId}`) && r.request().method() === 'DELETE');
  await deleteFromDrawer('Legacy safe delete boundary');
  if (!(await deleteResponse).ok() || !(await (await deleteResponse).json()).deleted) throw Error('Production safe delete did not complete');
  await expect(page.getByText('Legacy safe delete boundary', { exact: true })).toHaveCount(0);
  const unknownResponse = page.waitForResponse(r => r.url().endsWith('/api/jobs/job_unknown_fixture') && r.request().method() === 'DELETE');
  await deleteFromDrawer('Legacy unknown boundary');
  if ((await (await unknownResponse).json()).error?.code !== 'job_busy') throw Error('Unknown deletion must fail closed');
  await page.getByRole('dialog', { name: 'Delete job' }).getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Job detail' }).getByText('cleanup_pending', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  for (const width of [1280, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    const geometry = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
    if (geometry.scrollWidth > width) throw Error('Overflow ' + JSON.stringify(geometry));
    await page.screenshot({ path: `${out}/legacy-${width}.png`, fullPage: true });
  }
  if (requests.some(row => row.method !== 'GET' && row.path.includes('/action-jobs'))) throw Error('Legacy action reached new Job API');
  if (errors.length) throw Error(JSON.stringify(errors));
  await fs.writeFile(`${out}/browser.json`, JSON.stringify({ ok: true, version: context.browser()?.version(), requests, errors, profile }, null, 2));
} catch (error) {
  await fs.writeFile(`${out}/failure-requests.json`, JSON.stringify({requests,errors}, null, 2));
  await page.screenshot({ path: `${out}/failure.png`, fullPage: true });
  await fs.writeFile(`${out}/failure.txt`, await page.locator('body').innerText());
  throw error;
} finally { await context.close(); }
