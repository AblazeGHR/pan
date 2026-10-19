/* Real-browser route regression: Chat → Editor/Jobs → Chat keeps the ordinary
 * composer draft (text, URL node, occurrence identity, pending + uploaded
 * attachment bytes). Navigation goes through the app's own entry links, not a
 * manual unmount, and the final send is delivered to the deterministic fake
 * CLI so the bytes can be hashed from its recorded provider input. */
/* global process, Buffer, indexedDB, requestAnimationFrame, DataTransfer, ClipboardEvent, URL */
import fs from 'node:fs/promises';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import { chromium, expect } from '@playwright/test';

const identity = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const base = `http://127.0.0.1:${identity.port}`;
const sessionId = identity.session;
const pendingBytes = Buffer.from(identity.pendingText, 'utf8');
const uploadedBytes = Buffer.from(identity.uploadedText, 'utf8');
const hash = (value) => crypto.createHash('sha256').update(value).digest('hex');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await context.newPage();
const evidence = { ...identity, errors: [], checks: {} };
page.on('pageerror', (error) => evidence.errors.push(String(error)));
const editor = () => page.getByTestId('rich-text-composer');
const draft = async () => (await context.request.get(`${base}/api/sessions/${sessionId}/draft`)).json();
const localKeys = () => page.evaluate(() => new Promise((resolve, reject) => {
  const open = indexedDB.open('pan-composer-files', 1);
  open.onsuccess = () => {
    const database = open.result;
    const read = database.transaction('files').objectStore('files').getAllKeys();
    read.onsuccess = () => { database.close(); resolve(read.result); };
    read.onerror = () => reject(read.error);
  };
  open.onerror = () => reject(open.error);
}));

/** The composer's own inline structure, in document order. */
const structure = () => editor().evaluate((element) => [
  ...element.querySelectorAll('[data-composer-url-id],[data-composer-attachment]'),
].map((node) => node.dataset.composerUrlId
  ? `url:${node.dataset.composerUrlId}`
  : `att:${node.dataset.composerAttachment}`));

async function openChat() {
  // Wait for the addressed Session to be selected (its cold draft read is the
  // observable edge) before typing: the composer is also mounted for the
  // transient "no Session" state, and a selection arriving later re-binds it.
  await Promise.all([
    page.waitForResponse((response) =>
      response.url().includes(`/sessions/${sessionId}/draft`) && response.request().method() === 'GET'),
    page.goto(`${base}/react/?session=${sessionId}`),
  ]);
  await expect(editor()).toBeVisible();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function go(entry) {
  await page.getByRole('link', { name: entry, exact: true }).click();
  await expect(editor()).toHaveCount(0);
  await expect.poll(() => new URL(page.url()).pathname).toBe(
    entry === 'Chat' ? '/react/' : `/react/${entry === 'Terminal' ? 'terminals' : entry.toLowerCase()}`,
  );
}

async function backToChat() {
  await page.getByRole('link', { name: 'Chat', exact: true }).click();
  await expect(editor()).toBeVisible();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

/** Restore assertions shared by every panel round trip. */
async function expectDraftRestored(expected) {
  await expect(editor()).toContainText('route text');
  assert.deepEqual(await structure(), expected.structure, 'inline order/identity must round-trip');
  await expect(editor().locator('[data-composer-url-id]')).toHaveCount(1);
  await expect(page.getByTestId('server-attachments')).toContainText('route-pending.txt');
  await expect(page.getByRole('button', { name: '重试上传 route-pending.txt', exact: true })).toBeVisible();
  await expect(editor().locator('[data-composer-attachment]')).toHaveCount(1);
  // The embedded, already-uploaded chip must not be duplicated back into the
  // loose chip row after the remount.
  await expect(page.getByTestId('server-attachments')).not.toContainText('route-upload.txt');
}

try {
  await openChat();
  await editor().click();
  await page.keyboard.press('Control+End');
  await page.keyboard.insertText('route text ');
  // Real paste path: plain clipboard text is parsed into a URL node.
  await editor().evaluate((element) => {
    const transfer = new DataTransfer();
    transfer.setData('text/plain', 'see https://route.test/x now ');
    element.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: transfer }));
  });
  await expect(editor().locator('[data-composer-url-id]')).toHaveCount(1);

  // Hold the first upload at the transport layer: the chip must survive a
  // route switch while it is still un-uploaded, not only after completion.
  // Later uploads (the ready chip, and the explicit retry) continue normally.
  const uploadPattern = `**/api/sessions/${sessionId}/attachments`;
  let heldRoute;
  await page.route(uploadPattern, async (route) => {
    if (!heldRoute) { heldRoute = route; return; }
    await route.continue();
  });
  await page.getByTestId('client-attachment-input').setInputFiles({
    name: 'route-pending.txt', mimeType: 'text/plain', buffer: pendingBytes,
  });
  await expect(page.getByTestId('server-attachments')).toContainText('route-pending.txt');
  await expect(page.getByTestId('attachment-upload-progress')).toContainText('上传中');
  // Wait until the transport really is holding the request (the chip shows
  // "uploading" immediately, before the request leaves the browser).
  await expect.poll(() => !!heldRoute).toBe(true);

  await page.getByTestId('client-attachment-input').setInputFiles({
    name: 'route-upload.txt', mimeType: 'text/plain', buffer: uploadedBytes,
  });
  await expect.poll(async () => (await draft()).draft?.attachments
    ?.find((item) => item.displayName === 'route-upload.txt')?.attachmentId).toBeTruthy();
  const chip = page.getByTestId('draggable-attachment-chip').filter({ hasText: 'route-upload.txt' });
  await expect(chip).toBeVisible();
  const bounds = await editor().boundingBox();
  await chip.dragTo(editor(), { targetPosition: { x: Math.min(320, bounds.width - 12), y: 12 } });
  await expect(editor().locator('[data-composer-attachment]')).toHaveCount(1);
  // Both attachment records reach the server: the uploaded one by identity,
  // the pending one by browser-owned localFileId.
  await expect.poll(async () => (await draft()).draft?.attachments?.length).toBe(2);
  const pendingLocalFileId = (await draft()).draft.attachments
    .find((item) => item.displayName === 'route-pending.txt')?.localFileId;
  assert.match(pendingLocalFileId, new RegExp(`^${sessionId}:`));
  // Browser-owned bytes exist for the pending file (the uploaded one may also
  // have briefly been pending before it completed; both are session-scoped).
  const localKeyCount = (await localKeys()).filter((key) => key.startsWith(`${sessionId}:`)).length;
  assert.ok(localKeyCount >= 1, String(localKeyCount));

  const expected = { structure: await structure() };
  assert.equal(expected.structure.filter((item) => item.startsWith('att:')).length, 1);
  assert.equal(expected.structure.filter((item) => item.startsWith('url:')).length, 1);
  await page.screenshot({ path: `${identity.runtime}/before-navigation.png`, fullPage: true });

  await go('Editor');
  await backToChat();
  await expectDraftRestored(expected);
  evidence.checks.editorRoundTripKeepsDraft = true;

  await go('Jobs');
  await backToChat();
  await expectDraftRestored(expected);
  evidence.checks.jobsRoundTripKeepsDraft = true;

  // 390px mobile: the entry links live in the drawer. Opening it once is
  // enough — the drawer stays open across the route change, so the return
  // also goes through the page entry list rather than history.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '打开侧边栏' }).click();
  await go('Editor');
  await backToChat();
  await expectDraftRestored(expected);
  evidence.checks.mobileEditorRoundTripKeepsDraft = true;
  await page.screenshot({ path: `${identity.runtime}/mobile-restored.png`, fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });

  // Complete the interrupted upload after the round trips; the retried request
  // must carry the original File bytes, which the server then stores.
  if (heldRoute) await heldRoute.abort().catch(() => {});
  await page.getByRole('button', { name: '重试上传 route-pending.txt', exact: true }).click();
  await expect.poll(async () => (await draft()).draft?.attachments
    ?.find((item) => item.displayName === 'route-pending.txt')?.attachmentId).toBeTruthy();
  const uploadedDraft = (await draft()).draft;
  assert.equal(uploadedDraft.attachments.length, 2);
  for (const item of uploadedDraft.attachments) assert.ok(item.attachmentId, JSON.stringify(item));
  // The bytes behind the restored localFileId are the original File, not a
  // placeholder: the stored upload hashes to the seeded client bytes.
  const pendingAttachment = uploadedDraft.attachments
    .find((item) => item.displayName === 'route-pending.txt');
  const pendingDownload = await context.request.get(base + pendingAttachment.href);
  assert.equal(hash(await pendingDownload.body()), hash(pendingBytes));
  evidence.checks.pendingBytesSurvivedRouteSwitch = hash(pendingBytes);

  await editor().press('Enter');
  await expect.poll(async () => (await (await context.request.get(`${base}/api/sessions/${sessionId}/queue`)).json()).items?.length).toBe(1);
  await expect.poll(async () => (await draft()).draft).toBeNull();
  await expect.poll(async () => (await localKeys()).filter((key) => key.startsWith(`${sessionId}:`)).length).toBe(0);
  evidence.checks.sentDraftClearedAndBytesPruned = true;
  await page.screenshot({ path: `${identity.runtime}/sent.png`, fullPage: true });

  assert.deepEqual(evidence.errors, []);
} catch (error) {
  evidence.failure = String(error);
  evidence.body = await page.locator('body').innerText();
  await page.screenshot({ path: `${identity.runtime}/failure.png`, fullPage: true });
  throw error;
} finally {
  await fs.writeFile(`${identity.runtime}/chromium.json`, JSON.stringify(evidence, null, 2));
  await browser.close();
}
