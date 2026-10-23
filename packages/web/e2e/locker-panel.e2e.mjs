import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
const [base, token, owner, evidence] = process.argv.slice(2);
if (!base || new URL(base).hostname !== '127.0.0.1' || ['8767', '8768'].includes(new URL(base).port)) throw new Error('Owned random-port instance required');
await mkdir(evidence, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ extraHTTPHeaders: { Authorization: `Bearer ${token}` }, viewport: { width: 1360, height: 900 } });
  const create = async (key, card) => {
    const response = await context.request.post(`${base}/api/interaction-cards`, { data: { ownerSessionId: owner,
      recipientId: 'test-user', requestKey: key, card } });
    expect(response.ok()).toBeTruthy(); return (await response.json()).card;
  };
  const question = await create('ui-answer', { title: 'Browser Card', body: '**Full Markdown**\n\n[File](https://example.invalid/file)',
    summaryBody: 'Short notification', answerInput: { label: 'Your answer' }, choices: [{ id: 'go', label: 'Proceed', triggerRef: { id: 'synthetic-trigger', revision: 1 } }] });
  const rejected = await create('ui-reject', { title: 'Rejected Card', body: 'Retain original Card', summaryBody: 'Rejected summary' });
  const page = await context.newPage(); const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(`${base}/react/locker`);
  await page.getByLabel('Local user key', { exact: true }).fill(process.env.PAN_TEST_JOB_KEY);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('main', { name: 'Locker' })).toBeVisible();
  const row = page.locator(`[data-card-id="${question.id}"]`);
  await expect(row.getByText('Full Markdown', { exact: true })).toBeVisible();
  await expect(row.getByText(owner, { exact: true })).toBeVisible();
  await row.getByRole('textbox', { name: 'Your answer' }).fill('Browser response');
  await row.getByRole('radio', { name: 'Proceed', exact: true }).check();
  await row.getByRole('button', { name: 'Submit', exact: true }).click();
  await expect(row.getByRole('region', { name: 'Completed interaction' })).toContainText('Submitted');
  await expect(row.getByRole('textbox')).toHaveCount(0);
  await expect(row.getByRole('region', { name: 'Completed interaction' })).toContainText('Browser response');
  await row.getByText('Delivery details', { exact: true }).click();
  await expect(row.getByText('Trigger delivery: accepted', { exact: true })).toBeVisible();
  const rejectRow = page.locator(`[data-card-id="${rejected.id}"]`);
  await rejectRow.getByRole('button', { name: 'Reject', exact: true }).click();
  await rejectRow.getByRole('textbox', { name: 'Reason for rejection', exact: true }).fill('Owned rejection probe');
  await rejectRow.getByRole('button', { name: 'Confirm rejection', exact: true }).click();
  await expect(rejectRow.getByRole('region', { name: 'Completed interaction' })).toContainText('Rejected');
  await expect(rejectRow.getByText('Retain original Card')).toBeVisible();
  await page.screenshot({ path: `${evidence}/locker-desktop.png`, fullPage: true });
  await page.reload();
  await page.getByLabel('Local user key', { exact: true }).fill(process.env.PAN_TEST_JOB_KEY);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.locator(`[data-card-id="${rejected.id}"]`).getByRole('region', { name: 'Completed interaction' })).toContainText('Rejected');
  await page.locator(`[data-card-id="${rejected.id}"]`).getByRole('button', { name: 'Delete record', exact: true }).click();
  await expect(page.locator(`[data-card-id="${rejected.id}"]`)).toHaveCount(0);
  const deleted = await context.request.get(`${base}/api/user-inbox/${rejected.id}`);
  expect(deleted.status()).toBe(404);
  const receipt = await context.request.get(`${base}/api/interaction-cards/${rejected.id}/receipt?ownerSessionId=${owner}`);
  expect(receipt.ok()).toBeTruthy();
  expect((await receipt.json()).receipt.response.outcome).toBe('reject');
  const sources = await (await context.request.get(`${base}/api/user-lockers`)).json();
  expect(sources.lockers).toHaveLength(1);
  const persisted = await (await context.request.get(`${base}/api/user-inbox/${question.id}`)).json();
  expect(persisted.card.read).toBe(true);
  expect(persisted.card.response.answer).toBe('Browser response');
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('main', { name: 'Locker' })).toBeVisible();
  await page.screenshot({ path: `${evidence}/locker-mobile.png`, fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
  await writeFile(`${evidence}/browser.json`, JSON.stringify({ base, owner, cardId: question.id, rejectedId: rejected.id,
    submitted: true, rejected: true, reloaded: true, deleted: true, readOriginal: true, pageErrors: errors }, null, 2));
} finally { await browser.close(); }
