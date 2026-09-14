/* global DataTransfer, DragEvent, Event, URL, console, document, localStorage, process, setTimeout, window */

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

const baseURL = process.env.PAN_E2E_BASE_URL || 'http://127.0.0.1:8765';
const runtime = process.env.PAN_E2E_RUNTIME || path.resolve('test-results/pan-e2e-runtime');
const artifacts = path.join(runtime, 'artifacts');
await fs.mkdir(artifacts, { recursive: true });

const browser = await chromium.launch({ headless: true });
const results = [];

async function poll(read, check, timeout = 5000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    last = await read();
    if (check(last)) return last;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`poll timeout; last=${JSON.stringify(last)}`);
}

async function openPage() {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  await page.goto(`${baseURL}/react/`);
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  return { context, page };
}

async function openPageInContext(context) {
  const page = await context.newPage();
  await page.goto(`${baseURL}/react/`);
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  return page;
}

async function selectSession(page, name) {
  const card = page.locator('[data-session-card-id]').filter({ hasText: name }).first();
  await card.waitFor({ state: 'visible' });
  await card.click();
  return card;
}

async function selectChat(page) {
  await selectSession(page, 'Chat Stream');
  await page.getByRole('heading', { name: 'Browser file-link fixtures' }).waitFor({ state: 'visible' });
}

async function returnToChat(page) {
  await page.getByRole('link', { name: 'Chat', exact: true }).click();
  await page.waitForURL(/\/react\/?$/);
  await selectChat(page);
}

async function runCase(name, body) {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  let context;
  let page;
  const started = new Date().toISOString();
  try {
    ({ context, page } = await openPage());
    await body(page);
    await page.screenshot({ path: path.join(artifacts, `${slug}.png`), fullPage: true });
    await context.tracing.stop({ path: path.join(artifacts, `${slug}.zip`) });
    results.push({ name, status: 'passed', started, screenshot: `${slug}.png`, trace: `${slug}.zip` });
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    if (page) await page.screenshot({ path: path.join(artifacts, `${slug}-failure.png`), fullPage: true }).catch(() => {});
    if (context) await context.tracing.stop({ path: path.join(artifacts, `${slug}-failure.zip`) }).catch(() => {});
    results.push({ name, status: 'failed', started, error: message, screenshot: `${slug}-failure.png`, trace: `${slug}-failure.zip` });
  } finally {
    await context?.close();
  }
}

await runCase('sorting short and long pointer press', async (page) => {
  const sort = page.getByRole('button', { name: /^Sort sessions:/ });
  assert.equal(await sort.getAttribute('aria-label'), 'Sort sessions: recent');
  await sort.click();
  assert.equal(await sort.getAttribute('aria-label'), 'Sort sessions: name');
  const box = await sort.boundingBox();
  assert.ok(box);
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.waitForTimeout(650);
  await page.getByRole('menu', { name: 'Session list options' }).waitFor({ state: 'visible' });
  await page.mouse.up();
  assert.equal(await sort.getAttribute('aria-label'), 'Sort sessions: name');
  await page.mouse.click(x, y);
  assert.equal(await sort.getAttribute('aria-label'), 'Sort sessions: custom');
});

await runCase('drag auto scroll and HTTP order persistence', async (page) => {
  const viewport = page.locator('aside div.flex-1.overflow-y-auto').first();
  const metrics = () => viewport.evaluate((el) => ({ top: el.scrollTop, height: el.clientHeight, scrollHeight: el.scrollHeight }));
  const initial = await metrics();
  assert.ok(initial.scrollHeight > initial.height);
  const sourceCard = await selectSession(page, 'Drag 01');
  const sourceId = await sourceCard.getAttribute('data-session-card-id');
  const handle = sourceCard.getByTestId('drag-handle');
  const source = await handle.boundingBox();
  const viewportBox = await viewport.boundingBox();
  assert.ok(source && viewportBox);
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  await page.mouse.move(source.x + source.width / 2 + 40, source.y + source.height / 2 + 20);
  await page.evaluate(() => { document.querySelector('aside div.flex-1.overflow-y-auto').scrollTop = 0; });
  await page.mouse.move(viewportBox.x + 20, viewportBox.y + viewportBox.height - 70);
  await page.waitForTimeout(220);
  const nearEdge = await metrics();
  await page.evaluate(() => { document.querySelector('aside div.flex-1.overflow-y-auto').scrollTop = 0; });
  await page.waitForTimeout(30);
  await page.mouse.move(viewportBox.x + 20, viewportBox.y + viewportBox.height - 3);
  await page.waitForTimeout(220);
  const atEdge = await metrics();
  assert.ok(nearEdge.top > 0, `near-edge scroll did not move: ${JSON.stringify(nearEdge)}`);
  assert.ok(atEdge.top > nearEdge.top, `edge speed did not increase: ${JSON.stringify({ nearEdge, atEdge })}`);
  assert.ok(atEdge.top <= 18 * 20, `edge scroll exceeded bounded test window: ${atEdge.top}`);
  await page.waitForTimeout(350);
  const beforeDrop = await metrics();
  const targetCard = page.locator('[data-session-card-id]').last();
  await targetCard.scrollIntoViewIfNeeded();
  const targetBox = await targetCard.boundingBox();
  assert.ok(targetBox, 'no visible edge target after auto-scroll');
  assert.notEqual(await targetCard.getAttribute('data-session-card-id'), sourceId);
  await page.mouse.move(targetBox.x + Math.min(100, targetBox.width / 2), targetBox.y + 2);
  await page.waitForTimeout(80);
  const responsePromise = page.waitForResponse((response) => response.url().endsWith('/api/sessions/order') && response.request().method() === 'POST');
  await page.mouse.up();
  const response = await responsePromise;
  assert.equal(response.status(), 200);
  assert.equal((await response.json()).ok, true);
  assert.ok(beforeDrop.top > 0);
  const server = await page.request.get(`${baseURL}/api/sessions?summary=1`);
  assert.equal(server.ok(), true);
  const names = (await server.json()).sessions.map((session) => session.name);
  assert.ok(names.includes('Drag 01'));
  assert.ok(names.indexOf('Drag 01') > 0, `dragged session was not reordered: ${names.join(', ')}`);
});

await runCase('stream follows exact bottom only', async (page) => {
  const card = await selectSession(page, 'Chat Stream');
  await page.getByRole('heading', { name: 'Browser file-link fixtures' }).waitFor({ state: 'visible' });
  const scroller = page.locator('main div.overflow-auto').first();
  await poll(async () => {
    const distance = await scroller.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight);
    if (distance !== 0) {
      await scroller.evaluate((el) => {
        el.scrollTop = el.scrollHeight;
        el.dispatchEvent(new Event('scroll', { bubbles: true }));
      });
    }
    return distance;
  }, (distance) => distance === 0, 5000);
  const sessionId = await card.getAttribute('data-session-card-id');
  assert.ok(sessionId);
  const stream = async (itemId, text) => {
    const response = await page.request.post(`${baseURL}/__e2e/stream`, { data: { sessionId, event: { type: 'assistant', role: 'assistant', content: text, item_id: itemId, delta: true } } });
    assert.equal(response.ok(), true);
  };
  await stream('bottom-stream', `bottom-follow\n${'line\n'.repeat(16)}`);
  await page.locator('main').getByText('bottom-follow').first().waitFor({ state: 'visible' });
  await page.waitForTimeout(100);
  assert.equal(await scroller.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight), 0);
  await scroller.evaluate((el) => { el.scrollTop = Math.max(0, el.scrollHeight - el.clientHeight - 1); el.dispatchEvent(new Event('scroll', { bubbles: true })); });
  const onePixelAway = await scroller.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight);
  assert.ok(onePixelAway >= 1);
  await stream('away-stream', `one-pixel-away\n${'line\n'.repeat(24)}`);
  await page.locator('main').getByText('one-pixel-away').first().waitFor({ state: 'visible' });
  await page.waitForTimeout(120);
  const afterAway = await scroller.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight);
  assert.ok(afterAway > onePixelAway, `stream pulled one-pixel-away reader to bottom: before=${onePixelAway}, after=${afterAway}`);
});

await runCase('markdown files through real editor and external link preservation', async (page) => {
  await selectChat(page);
  const readPaths = [];
  page.on('request', (request) => { if (request.url().includes('/api/fs/read?')) readPaths.push(request.url()); });
  await page.getByRole('link', { name: 'relative file' }).click();
  await page.waitForURL(/\/react\/editor$/);
  assert.equal(new URL(page.url()).pathname, '/react/editor');
  await page.getByRole('button', { name: 'Edit' }).click();
  await page.locator('.monaco-editor').waitFor({ state: 'visible' });
  await poll(() => page.evaluate(() => {
    const editor = window.monaco?.editor?.getEditors?.()[0];
    return editor ? { position: editor.getPosition()?.lineNumber, selection: editor.getSelection() } : null;
  }), (state) => state?.position === 42 && state.selection?.startLineNumber === 42 && state.selection?.endLineNumber === 42);

  await returnToChat(page);
  const windowsLink = page.getByRole('link', { name: 'windows server path' });
  const windowsHref = await windowsLink.getAttribute('href');
  console.log(`windows server href=${windowsHref}`);
  assert.match(windowsHref || '', /^\/api\/attachments\/editor\/att_[A-Za-z0-9]{32}\?session_id=[^#]+#L42-L48$/);
  assert.ok(!windowsHref?.includes('pan-e2e-runtime'));
  await windowsLink.click();
  await page.waitForURL(/\/react\/editor$/);
  assert.equal(new URL(page.url()).pathname, '/react/editor');
  await page.locator('.monaco-editor').waitFor({ state: 'visible' });
  await poll(() => page.evaluate(() => window.monaco?.editor?.getEditors?.()[0]?.getSelection() ?? null), (selection) => selection?.startLineNumber === 42 && selection?.endLineNumber === 48);

  await returnToChat(page);
  await page.getByRole('link', { name: 'file URI' }).click();
  await page.waitForURL(/\/react\/editor$/);
  assert.equal(new URL(page.url()).pathname, '/react/editor');
  await page.locator('.monaco-editor').waitFor({ state: 'visible' });

  await returnToChat(page);
  assert.equal(await page.getByRole('link', { name: 'http link' }).getAttribute('href'), 'https://example.com/pan-e2e');
  assert.equal(await page.getByRole('link', { name: 'mailto link' }).getAttribute('href'), 'mailto:pan-e2e@example.com');
  assert.equal(await page.getByRole('link', { name: 'anchor link' }).getAttribute('href'), '#local-anchor');
  await page.getByRole('link', { name: 'anchor link' }).click();
  const anchorUrl = new URL(page.url());
  assert.match(anchorUrl.pathname, /\/react\/?$/);
  assert.equal(anchorUrl.hash, '#local-anchor');

  await returnToChat(page);
  await page.getByRole('link', { name: 'missing file' }).click();
  await page.getByText(/打开文件失败/).waitFor({ state: 'visible' });
  assert.ok(readPaths.some((url) => url.includes('notes.md')));
  assert.ok(readPaths.some((url) => url.includes('linked.ts')));
});

async function runCrossSessionServerFileDrop() {
  const name = 'cross-session server-file drag and optimistic send';
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  let sourcePage;
  let targetPage;
  let releaseQueue = () => {};
  const started = new Date().toISOString();
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  try {
    sourcePage = await openPageInContext(context);
    targetPage = await openPageInContext(context);
    await selectChat(sourcePage);
    const sourceCard = sourcePage.locator('[data-session-card-id]').filter({ hasText: 'Chat Stream' }).first();
    const sourceSessionId = await sourceCard.getAttribute('data-session-card-id');
    assert.ok(sourceSessionId);
    const sourceHistoryResponse = await sourcePage.request.get(
      `${baseURL}/api/sessions/${encodeURIComponent(sourceSessionId)}/history?before=0&limit=50`,
    );
    assert.equal(sourceHistoryResponse.status(), 200);
    const sourceHistoryBefore = (await sourceHistoryResponse.json()).history;

    const sourceLink = sourcePage.getByRole('link', { name: 'relative file' });
    const sourceHref = await sourceLink.getAttribute('href');
    assert.match(sourceHref || '', /^\/api\/attachments\/editor\//);
    assert.equal(await sourceLink.getAttribute('draggable'), 'true');
    const drag = await sourceLink.evaluate((node) => {
      const dataTransfer = new DataTransfer();
      node.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer }));
      return {
        custom: dataTransfer.getData('application/x-pan-attachment'),
        plain: dataTransfer.getData('text/plain'),
      };
    });
    const payload = JSON.parse(drag.custom);
    assert.equal(payload.sourceSessionId, sourceSessionId);
    assert.equal(payload.source, 'message');
    assert.equal(payload.location.line, 42);
    assert.ok(payload.serverAttachmentId);
    assert.equal(Object.prototype.hasOwnProperty.call(payload, 'path'), false);
    assert.ok(!/[A-Za-z]:[\\/]/.test(drag.custom));
    assert.ok(payload.href.startsWith('/api/attachments/'));
    assert.equal(drag.plain, 'relative file');

    const targetCard = await selectSession(targetPage, 'Alpha Session');
    const targetSessionId = await targetCard.getAttribute('data-session-card-id');
    assert.ok(targetSessionId);
    const editor = targetPage.getByTestId('rich-text-composer');
    await editor.click();
    await targetPage.keyboard.type('请处理 ');
    const editorBox = await editor.boundingBox();
    assert.ok(editorBox);
    const dropResult = await targetPage.evaluate(({ custom, displayName, x, y }) => {
      const root = document.querySelector('[data-testid="rich-text-composer"]');
      if (!root) throw new Error('composer not found');
      const dataTransfer = new DataTransfer();
      dataTransfer.setData('application/x-pan-attachment', custom);
      dataTransfer.setData('text/plain', displayName);
      const init = { bubbles: true, cancelable: true, dataTransfer, clientX: x, clientY: y };
      const dragover = new DragEvent('dragover', init);
      root.dispatchEvent(dragover);
      const drop = new DragEvent('drop', init);
      root.dispatchEvent(drop);
      return { dragoverPrevented: dragover.defaultPrevented, dropPrevented: drop.defaultPrevented };
    }, { custom: drag.custom, displayName: drag.plain, x: editorBox.x + 40, y: editorBox.y + editorBox.height / 2 });
    assert.equal(dropResult.dragoverPrevented, true);
    assert.equal(dropResult.dropPrevented, true);
    await editor.locator('[data-composer-attachment]').waitFor({ state: 'visible' });

    let queueRequestPayload;
    let openQueue;
    const queueGate = new Promise((resolve) => { openQueue = resolve; });
    let uploadRequests = 0;
    targetPage.on('request', (request) => {
      if (request.url().includes('/api/attachments/upload')) uploadRequests += 1;
    });
    await targetPage.route(
      `${baseURL}/api/sessions/${encodeURIComponent(targetSessionId)}/queue`,
      async (route) => {
        if (route.request().method() !== 'POST') {
          await route.continue();
          return;
        }
        queueRequestPayload = route.request().postDataJSON();
        await queueGate;
        await route.continue();
      },
    );
    const queueResponsePromise = targetPage.waitForResponse((response) =>
      response.url().includes(`/api/sessions/${encodeURIComponent(targetSessionId)}/queue`)
      && response.request().method() === 'POST',
    );
    await targetPage.getByRole('button', { name: 'Send' }).click();
    await poll(() => queueRequestPayload, (value) => !!value);
    assert.equal(await editor.textContent(), '');
    assert.equal(await editor.locator('[data-composer-attachment]').count(), 0);
    assert.equal(uploadRequests, 0);
    assert.ok(queueRequestPayload.parts?.some((part) =>
      part.type === 'attachment' && part.attachmentId === payload.serverAttachmentId));
    assert.ok(queueRequestPayload.text.includes(payload.displayName));
    const serializedQueuePayload = JSON.stringify(queueRequestPayload);
    assert.equal(Object.prototype.hasOwnProperty.call(queueRequestPayload, 'path'), false);
    assert.ok(!serializedQueuePayload.includes('D:\\'));
    assert.ok(!serializedQueuePayload.includes('\\\\'));
    assert.equal(await sourcePage.url(), `${baseURL}/react/`);

    releaseQueue = openQueue;
    releaseQueue();
    const queueResponse = await queueResponsePromise;
    assert.equal(queueResponse.status(), 200);
    const queueResult = await queueResponse.json();
    assert.equal(queueResult.ok, true);
    assert.ok(queueResult.item.parts?.some((part) =>
      part.type === 'attachment' && part.attachmentId === payload.serverAttachmentId),
    `server enqueue response lost attachment parts: ${JSON.stringify(queueResult)}`);
    const targetQueueResponse = await targetPage.request.get(
      `${baseURL}/api/sessions/${encodeURIComponent(targetSessionId)}/queue`,
    );
    assert.equal(targetQueueResponse.status(), 200);
    const targetQueue = await targetQueueResponse.json();
    // A live isolated Worker may consume the queue immediately; either the
    // authoritative enqueue response or the still-pending queue snapshot is
    // sufficient proof that the target accepted the reference.
    assert.ok(
      targetQueue.items.some((item) => item.parts?.some((part) =>
        part.type === 'attachment' && part.attachmentId === payload.serverAttachmentId))
      || queueResult.item.parts?.some((part) =>
        part.type === 'attachment' && part.attachmentId === payload.serverAttachmentId),
    );

    const sourceHistoryAfterResponse = await sourcePage.request.get(
      `${baseURL}/api/sessions/${encodeURIComponent(sourceSessionId)}/history?before=0&limit=50`,
    );
    assert.deepEqual((await sourceHistoryAfterResponse.json()).history, sourceHistoryBefore);
    await context.tracing.stop({ path: path.join(artifacts, `${slug}.zip`) });
    return { name, status: 'passed', started, trace: `${slug}.zip`, uploadRequests, queueRequestPayload };
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    await context.tracing.stop({ path: path.join(artifacts, `${slug}-failure.zip`) }).catch(() => {});
    return { name, status: 'failed', started, error: message, trace: `${slug}-failure.zip` };
  } finally {
    releaseQueue();
    await context.close();
  }
}

results.push(await runCrossSessionServerFileDrop());

await browser.close();
await fs.writeFile(path.join(runtime, 'browser-results.json'), JSON.stringify({ browser: browser.version(), baseURL, results }, null, 2));
for (const result of results) {
  console.log(`${result.status.toUpperCase()} ${result.name}`);
  if (result.error) console.error(result.error);
}
if (results.some((result) => result.status === 'failed')) process.exit(1);
