import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
const [base, token, owner, evidence, python] = process.argv.slice(2);
if (new URL(base).hostname !== '127.0.0.1' || ['8767', '8768'].includes(new URL(base).port)) throw Error('Owned instance required');
const root = fileURLToPath(new URL('../../../', import.meta.url));
const mcp = async args => {
  const { stdout } = await promisify(execFile)(python, ['-m', 'tests.support.no_answer_mcp', base, owner, JSON.stringify(args)],
    { cwd: root, windowsHide: true, timeout: 25000, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
  return JSON.parse(stdout);
};
await mkdir(evidence, { recursive: true });
const browser = await chromium.launch({ headless: true,
  ...(process.env.PAN_TEST_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PAN_TEST_CHROMIUM_EXECUTABLE } : {}) });
try {
  const card = (await mcp({ operation: 'create', recipient_id: 'test-user', request_key: 'gui-no-answer',
    card: { title: 'No answer lifecycle', body: 'Keep this original question visible', choices: [{ id: 'yes', label: 'Yes' }] } })).card;
  expect(card.status).toBe('open');
  const pending = await mcp({ operation: 'list_pending', limit: 1 });
  expect(pending.cards.map(c => c.id)).toContain(card.id);
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
  const page = await context.newPage(); const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(`${base}/react/locker`);
  await page.getByLabel('Local user key', { exact: true }).fill(process.env.PAN_TEST_JOB_KEY);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  const row = page.locator(`[data-card-id="${card.id}"]`);
  await expect(row.getByText('Awaiting response', { exact: true })).toBeVisible();
  await row.getByRole('textbox').fill('A stale draft must never be sent');
  await row.getByRole('radio', { name: 'Yes', exact: true }).check();
  const done = (await mcp({ operation: 'no_answer_needed', card_id: card.id, expected_revision: card.revision })).card;
  expect(done.status).toBe('no_answer_needed'); expect(done.response).toBeNull();
  expect((await mcp({ operation: 'no_answer_needed', card_id: card.id, expected_revision: 0 })).card).toEqual(done);
  expect((await mcp({ operation: 'list_pending' })).cards).toEqual([]);
  // Deliberately submit the stale production GUI. Backend refuses it and the GUI refreshes terminal state.
  const denied = page.waitForResponse(r => r.url().endsWith(`/api/user-inbox/${card.id}/submit`));
  await row.getByRole('button', { name: 'Submit', exact: true }).click();
  expect((await denied).status()).toBe(409);
  await expect(row.locator('.locker-status')).toHaveText('无需回答 · No answer needed');
  await expect(row.getByText('Keep this original question visible', { exact: true })).toBeVisible();
  await expect(row.getByRole('textbox')).toHaveCount(0);
  await expect(row.getByRole('radio')).toHaveCount(0);
  await expect(row.getByRole('button', { name: 'Submit', exact: true })).toHaveCount(0);
  await expect(row.getByRole('button', { name: 'Reject', exact: true })).toHaveCount(0);
  await expect(row.getByRole('button', { name: /Mark card/ })).toHaveCount(0);
  const headers = { Authorization: `Bearer ${token}` };
  for (const op of ['submit', 'respond', 'reject']) {
    expect((await context.request.post(`${base}/api/user-inbox/${card.id}/${op}`, {
      headers, data: { requestId: 'replay', expectedRevision: done.revision, answer: 'late' } })).status()).toBe(409);
  }
  await page.screenshot({ path: `${evidence}/terminal-desktop.png`, fullPage: true });
  await page.reload();
  await page.getByLabel('Local user key', { exact: true }).fill(process.env.PAN_TEST_JOB_KEY);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(row.locator('.locker-status')).toHaveText('无需回答 · No answer needed');
  await page.setViewportSize({ width: 390, height: 844 });
  await row.scrollIntoViewIfNeeded();
  await expect(row.locator('.locker-status')).toBeVisible();
  await page.screenshot({ path: `${evidence}/terminal-mobile.png`, fullPage: true });
  await row.getByRole('button', { name: 'Delete record', exact: true }).click();
  await expect(row).toHaveCount(0);
  expect((await context.request.get(`${base}/api/user-inbox/${card.id}`, { headers })).status()).toBe(404);
  expect((await mcp({ operation: 'create', recipient_id: 'test-user', request_key: 'gui-no-answer', card: card.definition })).status).toBe('rejected');
  expect(errors).toEqual([]);
  await writeFile(`${evidence}/browser.json`, JSON.stringify({ base, owner, cardId: card.id, done,
    realStdio: true, pendingVisible: true, staleSubmit409: true, reject409: true, terminalVisible: true,
    reloadPreserved: true, deleted: true, tombstonePreserved: true, pageErrors: errors }, null, 2));
} finally { await browser.close(); }
