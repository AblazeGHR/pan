/* global process, Buffer, indexedDB, requestAnimationFrame, DataTransfer, DragEvent */
import fs from 'node:fs/promises';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import { chromium, expect } from '@playwright/test';

const identity = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const base = `http://127.0.0.1:${identity.port}`;
const [a] = identity.sessions;
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext();
const page = await context.newPage();
const evidence = { ...identity, errors: [], checks: {} };
page.on('pageerror', error => evidence.errors.push(String(error)));
const bytes = Buffer.from('client real bytes\x00 中文\n');
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const draft = async id => (await context.request.get(`${base}/api/sessions/${id}/draft`)).json();
const localKeys = () => page.evaluate(() => new Promise((resolve, reject) => {
  const request = indexedDB.open('pan-composer-files', 1);
  request.onsuccess = () => {
    const database = request.result;
    const read = database.transaction('files').objectStore('files').getAllKeys();
    read.onsuccess = () => { database.close(); resolve(read.result); };
    read.onerror = () => reject(read.error);
  };
  request.onerror = () => reject(request.error);
}));
async function open(id) {
  await Promise.all([
    page.waitForResponse(response => response.url().includes(`/sessions/${id}/draft`) && response.request().method() === 'GET'),
    page.goto(`${base}/react/?session=${id}`),
  ]);
  await expect(page.getByTestId('rich-text-composer')).toBeVisible();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
try {
  await open(a);
  // Hold an actual upload request before it reaches HTTP, reproducing a slow
  // upload deterministically. All completed requests use the real server.
  await page.route(`**/api/sessions/${a}/attachments`, route => route.abort('failed'));
  await page.getByTestId('client-attachment-input').setInputFiles({ name: 'pending.txt', mimeType: 'text/plain', buffer: bytes });
  await expect(page.getByTestId('server-attachments')).toContainText('pending.txt');
  if (identity.baseline) {
    await expect.poll(async () => (await draft(a)).revision).toBeGreaterThan(0);
    const saved = await draft(a);
    assert.equal(saved.draft?.attachments?.length ?? 0, 0);
    await open(a);
    await expect(page.getByTestId('rich-text-composer')).toBeVisible();
    await expect(page.getByTestId('server-attachments')).toHaveCount(0);
    evidence.checks.baselinePendingFileDisappears = true;
  } else {
    await expect.poll(async () => (await draft(a)).draft?.attachments?.[0]?.localFileId).toMatch(new RegExp(`^${a}:`));
    assert.equal((await localKeys()).filter(key => key.startsWith(`${a}:`)).length, 1);
    await page.getByText('Attachment B', { exact: true }).click();
    await expect(page.getByTestId('server-attachments')).toHaveCount(0);
    await page.getByText('Attachment A', { exact: true }).click();
    await expect(page.getByTestId('server-attachments')).toContainText('pending.txt');
    await open(a);
    await expect(page.getByTestId('server-attachments')).toContainText('pending.txt');
    await page.unroute(`**/api/sessions/${a}/attachments`);
    await page.getByRole('button', { name: '重试上传 pending.txt', exact: true }).click();
    await expect.poll(async () => (await draft(a)).draft?.attachments?.[0]?.attachmentId).toBeTruthy();
    const uploaded = (await draft(a)).draft.attachments[0];
    const response = await context.request.get(base + uploaded.href);
    assert.equal(hash(await response.body()), hash(bytes));
    evidence.checks.pendingFileReloadRetryHash = hash(bytes);
    // Start a fresh ordinary draft containing an upload and a real registered
    // server file. Use normal UI upload, ordinary composer and real PUT/GET.
    await page.getByRole('button', { name: '取消附件 pending.txt', exact: true }).click();
    await page.getByTestId('rich-text-composer').fill('before [Docs](https://example.test/x) after');
    await page.getByTestId('client-attachment-input').setInputFiles({ name: 'client.txt', mimeType: 'text/plain', buffer: bytes });
    await expect.poll(async () => (await draft(a)).draft?.attachments?.find(item => item.displayName === 'client.txt')?.attachmentId).toBeTruthy();
    const registered = await (await context.request.post(`${base}/api/sessions/${a}/attachments/from-server-file`, { data: { path: identity.serverFile } })).json();
    assert.ok(registered.attachmentId, JSON.stringify(registered));
    // Drag a server-owned reference through the same native drop handler.
    const payload = { source: 'editor', sourceSessionId: a, serverAttachmentId: registered.attachmentId,
      displayName: 'server.txt', href: registered.href };
    await page.getByTestId('rich-text-composer').evaluate((element, payload) => {
      const transfer = new DataTransfer();
      transfer.setData('application/x-pan-attachment', JSON.stringify(payload));
      const rect = element.getBoundingClientRect();
      element.dispatchEvent(new DragEvent('drop', { bubbles: true, dataTransfer: transfer, clientX: rect.left + 12, clientY: rect.top + 12 }));
    }, payload);
    await expect.poll(async () => (await draft(a)).draft?.attachments?.length).toBe(2);
    const before = (await draft(a)).draft;
    const revision = (await draft(a)).revision;
    const conflict = await (await context.request.put(`${base}/api/sessions/${a}/draft`, { data: { draft: before, baseRevision: revision - 1 } })).json();
    assert.equal(conflict.error?.code, 'draft_conflict');
    assert.deepEqual((await draft(a)).draft, before);
    await page.getByText('Attachment B', { exact: true }).click();
    await expect.poll(async () => page.getByTestId('rich-text-composer').innerText()).not.toContain('before');
    await page.getByTestId('rich-text-composer').fill('other session');
    await page.getByText('Attachment A', { exact: true }).click();
    await expect(page.getByTestId('server-attachments')).toContainText('client.txt');
    await expect(page.getByTestId('rich-text-composer').locator('[data-composer-attachment]')).toContainText('server.txt');
    assert.deepEqual((await draft(a)).draft, before);
    await open(a);
    await expect(page.getByTestId('server-attachments')).toContainText('client.txt');
    evidence.checks.readyDraftReloadAndSwitch = true;
    await page.getByTestId('rich-text-composer').press('Enter');
    await expect.poll(async () => (await (await context.request.get(`${base}/api/sessions/${a}/queue`)).json()).items?.length).toBe(1);
    const queue = async () => (await (await context.request.get(`${base}/api/sessions/${a}/queue`)).json()).items[0];
    const original = await queue();
    await expect.poll(async () => (await draft(a)).draft).toBeNull();
    await expect.poll(async () => (await localKeys()).filter(key => key.startsWith(`${a}:`)).length).toBe(0);
    await page.getByTestId('rich-text-composer').fill('ordinary draft preserved');
    await page.getByTestId('client-attachment-input').setInputFiles({ name: 'unsent.txt', mimeType: 'text/plain', buffer: bytes });
    await expect.poll(async () => (await draft(a)).draft?.attachments?.[0]?.attachmentId).toBeTruthy();
    const ordinary = (await draft(a)).draft;
    await page.getByRole('button', { name: /^发送队列/ }).click();
    await page.getByTitle('编辑', { exact: true }).click();
    const editor = page.getByTestId('queue-rich-text-composer');
    await expect(editor).toBeVisible();
    await expect(editor.locator('[data-composer-attachment]')).toHaveCount(2);
    await expect(editor.locator('[data-composer-url-id]')).toHaveCount(1);
    assert.ok(!(await editor.innerText()).includes('attachmentId'));
    // Exercise native Chromium dragging of the restored node, then deletion
    // and cancel. Neither the resource list nor the ordinary draft may change.
    const first = editor.locator('[data-composer-attachment]').first();
    await expect(first).toHaveAttribute('draggable', 'true');
    const bounds = await editor.boundingBox();
    await first.dragTo(editor, { targetPosition: { x: bounds.width - 8, y: bounds.height - 8 } });
    await expect(editor.locator('[data-composer-attachment]')).toHaveCount(2);
    await editor.getByRole('button', { name: '删除附件 server.txt', exact: true }).click();
    await expect(editor.locator('[data-composer-attachment]')).toHaveCount(1);
    await page.getByRole('button', { name: '取消队列编辑', exact: true }).click();
    await expect(editor).toHaveCount(0);
    assert.deepEqual(await queue(), original);
    assert.deepEqual((await draft(a)).draft, ordinary);
    await page.getByTitle('编辑', { exact: true }).click();
    await expect(editor.locator('[data-composer-attachment]')).toHaveCount(2);
    const urlId = await editor.locator('[data-composer-url-id]').getAttribute('data-composer-url-id');
    await editor.press('Control+End');
    await editor.press('End');
    await page.keyboard.insertText(' edited');
    await page.getByRole('button', { name: '保存队列编辑', exact: true }).click();
    await expect(editor).toHaveCount(0);
    const after = await queue();
    assert.equal(after.id, original.id);
    assert.equal(after.meta.revision, original.meta.revision + 1);
    assert.deepEqual(after.parts.filter(p => p.type === 'attachment').map(p => p.attachmentId),
      original.parts.filter(p => p.type === 'attachment').map(p => p.attachmentId));
    for (const attachment of before.attachments) {
      const response = await context.request.get(base + attachment.href);
      assert.equal(hash(await response.body()), hash(attachment.displayName === 'client.txt' ? bytes : await fs.readFile(identity.serverFile)));
    }
    assert.deepEqual((await draft(a)).draft, ordinary);
    await page.getByTitle('编辑', { exact: true }).click();
    await expect(editor.locator('[data-composer-url-id]')).toHaveAttribute('data-composer-url-id', urlId);
    await page.screenshot({ path: identity.runtime + '/editor.png', fullPage: true });
    await page.getByRole('button', { name: '取消队列编辑', exact: true }).click();
    evidence.checks.nativeDragDeleteCancelAndOrdinaryDraftIsolation = true;
    evidence.checks.queueVisualEditOriginalIdentityOrderAndHashes = true;
    await page.screenshot({ path: identity.runtime + '/saved.png', fullPage: true });
  }
  assert.deepEqual(evidence.errors, []);
} catch (error) {
  evidence.failure = String(error);
  evidence.body = await page.locator('body').innerText();
  await page.screenshot({ path: identity.runtime + '/failure.png', fullPage: true });
  throw error;
} finally {
  await fs.writeFile(identity.runtime + '/chromium.json', JSON.stringify(evidence, null, 2));
  await browser.close();
}
