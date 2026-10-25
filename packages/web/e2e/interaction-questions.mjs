// Production bundle in Chromium, all HTTP fulfilled in-process: no listener or service.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, extname } from 'node:path';

const out = fileURLToPath(new URL('../../../evidence/interaction-card-questions/', import.meta.url));
const dist = fileURLToPath(new URL('../dist/', import.meta.url));
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [], requests = [], checks = [];
page.on('pageerror', (error) => errors.push(error.message));
const base = 'https://pan-questions.test';
let card = {
  schemaVersion: 2, id: 'questions', lockerId: 'locker', ownerSessionId: 'ses_questions',
  recipientId: 'user', createdAt: '2026-10-25T10:00:00Z', read: false, revision: 0, status: 'open',
  definition: { schemaVersion: 2, title: '发布前确认 · 多问题交互卡', body: '请逐题确认。拒绝单个问题后，可以继续处理其他题目；所有内容将在点击提交时一次保存。', summaryBody: '发布前确认', questions: [
    { id: 'mode', prompt: '本次使用哪种交付方式？', type: 'single_choice', choices: [{ id: 'preview', label: '生成预览，等待验收' }, { id: 'release', label: '准备发布候选' }] },
    { id: 'targets', prompt: '需要验证哪些环境？', type: 'multi_choice', choices: [{ id: 'desktop', label: '桌面 · 1440px' }, { id: 'mobile', label: '移动端 · 390px' }] },
    { id: 'note', prompt: '请补充验收说明', type: 'text' },
    { id: 'optional', prompt: '其他补充（可跳过）', type: 'text', required: false },
  ] }, response: null, trustedChoices: {}, delivery: { state: 'not_requested' }, rejectionDelivery: { state: 'not_requested' },
};
await page.route('**/*', async (route) => {
  const req = route.request(), url = new URL(req.url());
  if (url.origin !== base) return route.abort();
  if (!url.pathname.startsWith('/api/')) {
    const relative = url.pathname.startsWith('/react/assets/') ? url.pathname.slice('/react/'.length) : 'index.html';
    const path = resolve(dist, relative);
    if (!path.startsWith(dist)) return route.abort();
    return route.fulfill({ body: await readFile(path), contentType: { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[extname(path)] ?? 'application/octet-stream' });
  }
  const path = url.pathname, body = req.postData() ? req.postDataJSON() : {};
  requests.push({ path, method: req.method(), body });
  let json = { sessions: [], workers: [], workspaces: [], panels: [], jobs: [], adapters: [] };
  if (path === '/api/automation/login') json = { token: 'synthetic-only', expiresIn: 3600 };
  if (path === '/api/user-lockers') json = { lockers: [{ id: 'locker', sessionId: 'ses_questions', sourceName: '交互卡验收', workspaceIds: [], available: true, total: 1, unread: card.read ? 0 : 1 }] };
  if (path === '/api/user-locker-messages') json = { messages: [], nextAfter: null };
  if (path === '/api/reminding-cards') json = { cards: [] };
  if (path === '/api/user-inbox') json = { cards: [card], nextAfter: null };
  if (path === '/api/user-inbox/questions/submit') {
    card = { ...card, revision: 1, read: true, status: 'responded', response: { id: 'response', requestId: body.requestId, outcome: 'submit', answers: body.answers } };
    json = { card };
  }
  if (path === '/api/user-inbox/questions/read') { card.read = body.read; json = { card }; }
  await route.fulfill({ json });
});
try {
  await page.goto(base + '/react/locker');
  await page.getByLabel('Local user key').fill('synthetic-only');
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  const row = page.locator('[data-card-id="questions"]');
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: 'Submit', exact: true }).click();
  await expect(row.getByRole('alert')).toHaveCount(3);
  expect(requests.filter((r) => r.path.endsWith('/submit'))).toHaveLength(0);
  checks.push('required validation next to each question, no premature request');
  await row.getByRole('radio', { name: '生成预览，等待验收' }).check();
  await row.getByRole('checkbox', { name: '桌面 · 1440px' }).check();
  await row.getByRole('checkbox', { name: '移动端 · 390px' }).check();
  await row.getByRole('button', { name: '拒绝此题', exact: true }).nth(2).click();
  await row.getByRole('textbox', { name: '请补充验收说明：拒绝原因' }).fill('此题需要更多上下文，其他问题已完成确认。');
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await row.getByRole('button', { name: 'Submit', exact: true }).scrollIntoViewIfNeeded();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    expect(overflow).toBe(false);
    await page.screenshot({ path: resolve(out, `questions-${width}.png`) });
    checks.push(`${width}: scroll to submit, no horizontal overflow`);
  }
  await row.getByRole('button', { name: 'Submit', exact: true }).click();
  await expect(row.getByText('Read-only')).toBeVisible();
  await expect(row.getByRole('textbox')).toHaveCount(0);
  await expect(row.getByText('已拒绝此题：此题需要更多上下文，其他问题已完成确认。')).toBeVisible();
  const sent = requests.filter((r) => r.path.endsWith('/submit'));
  expect(sent).toHaveLength(1);
  expect(sent[0].body.answers).toEqual({ mode: { choiceId: 'preview' }, targets: { choiceIds: ['desktop', 'mobile'] }, note: { rejected: true, reason: '此题需要更多上下文，其他问题已完成确认。' } });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await row.getByText('Read-only').scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve(out, `readonly-${width}.png`) });
  }
  checks.push('single submission with stable question IDs, mixed answer/rejection read-only');
  expect(errors).toEqual([]);
  await writeFile(resolve(out, 'browser.json'), JSON.stringify({ checks, errors, chromium: browser.version(), transport: 'all requests intercepted; no HTTP server or listener', submissions: sent }, null, 2));
  console.log(JSON.stringify({ checks, errors }));
} catch (error) {
  await page.screenshot({ path: resolve(out, 'browser-failure.png') });
  throw error;
} finally { await browser.close(); }
