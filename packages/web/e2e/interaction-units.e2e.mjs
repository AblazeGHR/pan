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
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
  const headers = { Authorization: `Bearer ${token}` };
  const create = async (key, card) => (await mcp({ operation: 'create', recipient_id: 'test-user', request_key: key, card })).card;
  const read = async card => (await (await context.request.get(`${base}/api/user-inbox/${card.id}`, { headers })).json()).card;
  const prepared = await (await context.request.post(`${base}/__test/interaction-action`)).json();
  const bindingCapabilities = await mcp({ operation: 'capabilities' });
  const supportedSessionProducts = ['product_session_message', 'product_session_broadcast', 'product_session_message_v2', 'product_session_broadcast_v2', 'product_assign_v2'];
  const sessionBindings = bindingCapabilities.actions.filter(a => supportedSessionProducts.includes(a.actionRef.id));
  expect(sessionBindings).toHaveLength(5);
  expect(sessionBindings.every(a => a.bindable)).toBe(true);
  const runtimeBindings = bindingCapabilities.actions.filter(a => ['product_assign_runtime_v1', 'product_session_message_runtime_v1', 'product_session_broadcast_runtime_v1'].includes(a.actionRef.id));
  expect(runtimeBindings).toHaveLength(3);
  expect(runtimeBindings.every(a => !a.bindable && a.reason.includes('deliveryRuntime'))).toBe(true);
  expect((await context.request.patch(`${base}/api/sessions/${owner}/queue/paused`, { data: { paused: true } })).ok()).toBe(true);
  const sessionUnitCards = [];
  for (const binding of sessionBindings) {
    const args = binding.operation === 'session-broadcast' ? { sessionIds: [owner], text: `fixed ${binding.actionRef.id}` } : { sessionId: owner, text: `fixed ${binding.actionRef.id}` };
    sessionUnitCards.push(await create(`direct-${binding.actionRef.id}`, { schemaVersion: 3, title: `Direct ${binding.actionRef.id}`, units: [
      { id: 'q', prompt: 'Direct Session provider', type: 'single_choice', choices: [{ id: 'go', label: 'Fixed Session send', actionRef: binding.actionRef, arguments: args }] },
    ] }));
  }
  for (const binding of runtimeBindings) {
    const args = binding.operation === 'session-broadcast' ? { sessionIds: [owner], text: 'runtime' } : { sessionId: owner, text: 'runtime' };
    const refused = await mcp({ operation: 'create', recipient_id: 'test-user', request_key: `refused-${binding.actionRef.id}`, card: {
      schemaVersion: 3, title: 'Refuse before selection', units: [{ id: 'q', prompt: 'Runtime', type: 'single_choice', choices: [{ id: 'go', label: 'Runtime', actionRef: binding.actionRef, arguments: args }] }],
    } });
    expect(refused.status).toBe('rejected');
    expect(refused.error.message).toContain('deliveryRuntime');
  }
  const card = await create('units-browser', { schemaVersion: 3, title: 'Unified card', units: [
    { id: 'text', prompt: 'Describe result', type: 'text' },
    { id: 'action', prompt: 'Choose fixed action', type: 'single_choice', choices: [{ id: 'go', label: 'Run owned command', ...prepared }] },
    { id: 'reject', prompt: 'Sensitive question', type: 'text' },
  ] });
  expect(card).toBeTruthy();
  const page = await context.newPage(); const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(`${base}/react/locker`);
  await page.getByLabel('Local user key', { exact: true }).fill(process.env.PAN_TEST_JOB_KEY);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  const row = page.locator(`[data-card-id="${card.id}"]`);
  for (const sessionCard of sessionUnitCards) {
    const sessionRow = page.locator(`[data-card-id="${sessionCard.id}"]`);
    await sessionRow.getByRole('radio', { name: 'Fixed Session send', exact: true }).check();
    await sessionRow.getByRole('textbox').fill('BTW stays a note');
    await sessionRow.getByRole('button', { name: '提交此问题' }).click();
    await expect(sessionRow.getByRole('status')).toHaveText('全部处理');
    expect((await read(sessionCard)).units[0].executions[0].state).toBe('accepted');
  }

  const unit = name => row.getByRole('group', { name: new RegExp(name) });
  await expect(row.getByRole('status')).toHaveText('待答');
  await page.screenshot({ path: `${evidence}/units-input-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await row.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${evidence}/units-input-mobile.png`, fullPage: true });
  await page.setViewportSize({ width: 1360, height: 900 });
  await unit('Describe result').getByRole('textbox').fill('first full answer');
  await unit('Describe result').getByRole('button', { name: '提交此问题' }).click();
  await expect(row.getByRole('status')).toHaveText('部分提交');
  await expect(unit('Describe result').getByRole('textbox')).toHaveCount(0);
  await unit('Choose fixed action').getByRole('radio', { name: 'Run owned command' }).check();
  await unit('Choose fixed action').getByRole('textbox').fill('BTW does not alter or cancel the fixed command');
  await unit('Sensitive question').getByRole('button', { name: '拒绝回答此问题' }).click();
  // Rejection deliberately has no BTW.
  await row.getByRole('button', { name: '提交全部未处理问题' }).click();
  await expect(row.getByRole('status')).toHaveText('全部处理');
  const done = await read(card);
  expect(done.units.map(u => u.state)).toEqual(['answered', 'answered', 'rejected']);
  expect(done.units[1].executions[0].state).toBe('accepted');
  const batch = await mcp({ operation: 'submission', card_id: card.id, submission_id: done.submissions[1] });
  expect(Object.keys(batch.submission.answers)).toEqual(['action', 'reject']);
  expect(batch.submission.answers.text).toBeUndefined();
  await page.screenshot({ path: `${evidence}/units-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await row.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${evidence}/units-mobile.png`, fullPage: true });
  // V2 remains actionable through the same production unit interface.
  const old = await create('old-v2', { schemaVersion: 2, title: 'Old V2', questions: [
    { id: 'old', prompt: 'Old question', type: 'text' },
    { id: 'multi', prompt: 'Multiple options', type: 'multi_choice', choices: [{ id: 'a', label: 'Alpha' }, { id: 'b', label: 'Beta' }] },
  ] });
  await page.reload();
  const oldRow = page.locator(`[data-card-id="${old.id}"]`);
  await oldRow.getByRole('textbox', { name: 'Old question', exact: true }).fill('legacy answer');
  await oldRow.getByRole('checkbox', { name: 'Alpha' }).check();
  await oldRow.getByRole('checkbox', { name: 'Beta' }).check();
  await oldRow.getByRole('button', { name: '提交全部未处理问题' }).click();
  await expect(oldRow.getByRole('status')).toHaveText('全部处理');
  const v1 = await create('old-v1', { title: 'Old V1', answerInput: {}, body: 'Original V1 text' });
  await page.reload();
  const v1Row = page.locator(`[data-card-id="${v1.id}"]`);
  await v1Row.getByRole('textbox', { name: 'Old V1', exact: true }).fill('V1 compatibility answer');
  await v1Row.getByRole('button', { name: '提交此问题' }).click();
  await expect(v1Row.getByRole('status')).toHaveText('全部处理');
  expect((await read(v1)).response.answer).toBe('V1 compatibility answer');
  const concurrent = await create('concurrent', { schemaVersion: 3, title: 'Concurrent clients', units: [
    { id: 'a', prompt: 'Client A', type: 'text' }, { id: 'b', prompt: 'Client B', type: 'text' },
  ] });
  const attempts = ['a', 'b'].map(id => ({ requestId: `race-${id}`, expectedRevision: 0, unitAnswers: { [id]: { text: id } } }));
  const races = await Promise.all(attempts.map(data => context.request.post(`${base}/api/user-inbox/${concurrent.id}/submit`, { headers, data })));
  expect(races.map(r => r.status()).sort()).toEqual([200, 409]);
  const winner = races.findIndex(r => r.status() === 200);
  const duplicate = await (await context.request.post(`${base}/api/user-inbox/${concurrent.id}/submit`, { headers, data: attempts[winner] })).json();
  expect(duplicate.card.submissions).toHaveLength(1);
  // Four-unit summary and actual fixed TriggerRef admission.
  const large = await create('summary-four', { schemaVersion: 3, title: 'Four unit summary', units: Array.from({ length: 4 }, (_, i) => ({ id: `q${i}`, prompt: `Question ${i}`, type: 'text' })) });
  const largeResult = await (await context.request.post(`${base}/api/user-inbox/${large.id}/submit`, { headers,
    data: { requestId: 'four', expectedRevision: 0, unitAnswers: Object.fromEntries(Array.from({ length: 4 }, (_, i) => [`q${i}`, { rejected: true }])) } })).json();
  expect(largeResult.card.unitStatus).toBe('processed');
  const trigger = await create('v3-trigger', { schemaVersion: 3, title: 'Trigger units', units: [
    { id: 'q', prompt: 'Directed Trigger', type: 'single_choice', choices: [{ id: 'go', label: 'Go', triggerRef: { id: 'synthetic-trigger', revision: 1 } }] },
  ] });
  const triggerResult = await (await context.request.post(`${base}/api/user-inbox/${trigger.id}/submit`, { headers,
    data: { requestId: 'trigger', expectedRevision: 0, unitAnswers: { q: { choiceId: 'go', btw: 'fixed trigger note' } } } })).json();
  expect(triggerResult.card.delivery.state).toBe('accepted');
  expect(errors).toEqual([]);
  await writeFile(`${evidence}/browser.json`, JSON.stringify({ actionCard: card.id, submissions: done.submissions,
    sessionDirectProducts: sessionBindings.map(a => a.actionRef.id), sessionDirectCards: sessionUnitCards.map(c => c.id), runtimeCreateRefused: true, single: true, batch: true, rejectWithoutBtw: true, partialLocked: true, v2: old.id,
    v1: v1.id, concurrentHttpCAS: true, duplicateHttpNoReplay: true, multiChoice: true, directActionRef: true, triggerRef: true, summaryFour: large.id, pageErrors: errors }, null, 2));
} finally { await browser.close(); }
