/* global ClipboardEvent, DataTransfer, DragEvent, Element, File, NodeFilter, URL, console, document, process, window */

import assert from 'node:assert/strict';
import { Buffer } from 'node:buffer';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

const baseURL = process.env.PAN_ATTACHMENT_DND_BASE_URL || 'http://127.0.0.1:8765';
const runtime = process.env.PAN_ATTACHMENT_DND_RUNTIME
  || path.resolve('test-results/attachment-dnd-browser');
await fs.mkdir(runtime, { recursive: true });

const browser = await chromium.launch({ headless: true });
const results = [];
const browserVersion = browser.version();

function mockURL() {
  const url = new URL(baseURL);
  url.searchParams.set('mock', '1');
  return url.toString();
}

async function openPage() {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  const pageErrors = [];
  const protectedRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => {
    if (request.url().includes(':8768') || request.url().includes(':8767')) {
      protectedRequests.push(`${request.method()} ${request.url()}`);
    }
  });
  await page.goto(mockURL(), { waitUntil: 'domcontentloaded' });
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  await page.getByText('Alpha 主控', { exact: true }).click();
  await page.getByTestId('rich-text-composer').waitFor({ state: 'visible' });
  return { context, page, pageErrors, protectedRequests };
}

async function editorPoint(editor, text, offset) {
  return editor.evaluate((root, target) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node = null;
    while (walker.nextNode()) {
      const candidate = walker.currentNode;
      if (candidate.textContent === target.text) {
        node = candidate;
        break;
      }
    }
    if (!node) throw new Error(`editor text node not found: ${target.text}`);
    const range = document.createRange();
    range.setStart(node, target.offset);
    range.collapse(true);
    const rect = range.getBoundingClientRect();
    return { x: rect.left + 1, y: rect.top + Math.max(1, rect.height / 2) };
  }, { text, offset });
}

async function installEventLog(page) {
  await page.evaluate(() => {
    window.__attachmentDndEvents = [];
    for (const type of ['dragstart', 'dragenter', 'dragover', 'drop', 'dragend']) {
      document.addEventListener(type, (event) => {
        const data = event.dataTransfer;
        window.__attachmentDndEvents.push({
          type,
          target: event.target instanceof Element
            ? event.target.getAttribute('data-composer-attachment')
              || event.target.getAttribute('data-testid')
              || event.target.tagName
            : event.target?.nodeName,
          defaultPrevented: event.defaultPrevented,
          dropEffect: data?.dropEffect,
          types: data ? [...data.types] : [],
          customData: data?.getData('application/x-pan-attachment') || '',
        });
      }, false);
    }
  });
}

async function editorSnapshot(editor) {
  return editor.evaluate((root) => ({
    html: root.innerHTML,
    textContent: root.textContent,
    directChildren: [...root.children].map((node) => ({
      attachmentId: node.getAttribute('data-composer-attachment'),
      text: node.textContent,
    })),
    attachmentIds: [...root.querySelectorAll('[data-composer-attachment]')]
      .map((node) => node.getAttribute('data-composer-attachment')),
    selection: (() => {
      const selection = document.getSelection();
      return selection ? {
        node: selection.anchorNode?.nodeName,
        text: selection.anchorNode?.textContent,
        offset: selection.anchorOffset,
      } : null;
    })(),
    indicator: (() => {
      const node = root.parentElement?.querySelector('[data-testid="attachment-drop-caret"]');
      return node ? {
        left: node.style.left,
        top: node.style.top,
        height: node.style.height,
      } : null;
    })(),
  }));
}

async function dispatchNativeFiles(page, type, files, point = null) {
  return page.evaluate(({ eventType, fileSpecs, clientPoint }) => {
    const editor = document.querySelector('[data-testid="rich-text-composer"]');
    if (!editor) throw new Error('composer not found');
    const dataTransfer = new DataTransfer();
    for (const spec of fileSpecs) {
      dataTransfer.items.add(new File([spec.contents], spec.name, { type: spec.mimeType }));
    }
    const init = {
      bubbles: true,
      cancelable: true,
      dataTransfer,
      clientX: clientPoint?.x || 0,
      clientY: clientPoint?.y || 0,
    };
    const event = eventType === 'paste'
      ? new ClipboardEvent('paste', { ...init, clipboardData: dataTransfer })
      : new DragEvent(eventType, init);
    editor.dispatchEvent(event);
    return event.defaultPrevented;
  }, {
    eventType: type,
    fileSpecs: files,
    clientPoint: point,
  });
}

async function setEditorCaret(page, text, offset) {
  await page.getByTestId('rich-text-composer').evaluate((root, target) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node = null;
    while (walker.nextNode()) {
      if (walker.currentNode.textContent === target.text) {
        node = walker.currentNode;
        break;
      }
    }
    if (!node) throw new Error(`caret text node not found: ${target.text}`);
    const range = document.createRange();
    range.setStart(node, target.offset);
    range.collapse(true);
    const selection = document.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
  }, { text, offset });
}

async function runNativeFileInput() {
  const { context, page, pageErrors, protectedRequests } = await openPage();
  try {
    const editor = page.getByTestId('rich-text-composer');
    await editor.click();
    await page.keyboard.type('左侧文字 右侧文字', { delay: 8 });
    await setEditorCaret(page, '左侧文字 右侧文字', 5);
    const pastePrevented = await dispatchNativeFiles(page, 'paste', [
      { name: '粘贴一.txt', mimeType: 'text/plain', contents: 'paste one' },
      { name: '粘贴二.md', mimeType: 'text/markdown', contents: 'paste two' },
    ]);
    assert.equal(pastePrevented, true);
    await page.waitForFunction(() => document.querySelector('[data-testid="attachment-upload-progress"]')?.textContent?.includes('已完成'));
    await page.waitForTimeout(80);
    const pasted = await editorSnapshot(editor);
    assert.equal(pasted.textContent, '左侧文字 粘贴一.txt粘贴二.md右侧文字');
    assert.deepEqual(pasted.directChildren.map((child) => child.attachmentId), [null, pasted.attachmentIds[0], pasted.attachmentIds[1], null]);
    assert.deepEqual(pasted.directChildren.map((child) => child.text), ['左侧文字 ', '粘贴一.txt', '粘贴二.md', '右侧文字']);

    await editor.focus();
    await page.keyboard.press('Control+A');
    await page.keyboard.press('Backspace');
    await page.waitForTimeout(100);
    await editor.click();
    await page.keyboard.type('拖入前后', { delay: 8 });
    const dropBox = await editor.boundingBox();
    const dropPoint = await editorPoint(editor, '拖入前后', 2);
    assert.ok(dropBox && dropPoint);
    const dragData = [
      { name: '拖入一.txt', mimeType: 'text/plain', contents: 'drop one' },
      { name: '拖入二.txt', mimeType: 'text/plain', contents: 'drop two' },
    ];
    const dragOverPrevented = await dispatchNativeFiles(page, 'dragover', dragData, dropPoint);
    assert.equal(dragOverPrevented, true);
    const dropPrevented = await dispatchNativeFiles(page, 'drop', dragData, dropPoint);
    assert.equal(dropPrevented, true);
    await page.waitForFunction(() => document.querySelector('[data-testid="attachment-upload-progress"]')?.textContent?.includes('已完成'));
    await page.waitForTimeout(80);
    const dropped = await editorSnapshot(editor);
    assert.equal(dropped.textContent, '拖入拖入一.txt拖入二.txt前后');
    assert.equal(dropped.attachmentIds.length, 2);

    // A Pan attachment payload wins over a simultaneous OS File item. The OS
    // file must not become a second attachment or plain text.
    const customPoint = await editorPoint(editor, '拖入', 2);
    const customDrop = await page.evaluate(({ point }) => {
      const editorNode = document.querySelector('[data-testid="rich-text-composer"]');
      if (!editorNode) throw new Error('composer not found');
      const dataTransfer = new DataTransfer();
      dataTransfer.setData('application/x-pan-attachment', JSON.stringify({
        serverAttachmentId: `att_${'d'.repeat(32)}`,
        displayName: '已有附件.md',
        href: `/api/attachments/upload_${'c'.repeat(32)}.md?session_id=mock-alpha`,
        source: 'message',
      }));
      dataTransfer.items.add(new File(['must not upload'], '混淆文件.txt', { type: 'text/plain' }));
      editorNode.dispatchEvent(new DragEvent('dragover', {
        bubbles: true,
        cancelable: true,
        dataTransfer,
        clientX: point.x,
        clientY: point.y,
      }));
      const event = new DragEvent('drop', {
        bubbles: true,
        cancelable: true,
        dataTransfer,
        clientX: point.x,
        clientY: point.y,
      });
      editorNode.dispatchEvent(event);
      return {
        prevented: event.defaultPrevented,
        types: [...dataTransfer.types],
        customData: dataTransfer.getData('application/x-pan-attachment'),
      };
    }, { point: customPoint });
    assert.equal(customDrop.prevented, true);
    await page.waitForTimeout(180);
    const customSnapshot = await editorSnapshot(editor);
    assert.ok(customSnapshot.textContent.includes('已有附件.md'));
    assert.equal(customSnapshot.textContent.includes('混淆文件.txt'), false);
    assert.equal(await page.getByTestId('draggable-attachment-chip').filter({ hasText: '混淆文件.txt' }).count(), 0);

    // file:// URI payloads are rejected and cannot fall through to editor text.
    const uriPrevented = await page.evaluate(() => {
      const editorNode = document.querySelector('[data-testid="rich-text-composer"]');
      if (!editorNode) throw new Error('composer not found');
      const dataTransfer = new DataTransfer();
      dataTransfer.setData('text/uri-list', 'file:///C:/Users/test/private.txt');
      const event = new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer });
      editorNode.dispatchEvent(event);
      return event.defaultPrevented;
    });
    assert.equal(uriPrevented, true);
    await page.waitForFunction(() => document.body.innerText.includes('文件 URI'));

    assert.deepEqual(pageErrors, []);
    assert.deepEqual(protectedRequests, []);
    await page.screenshot({ path: path.join(runtime, 'native-file-paste-drop-fixed.png'), fullPage: true });
    return { name: 'native paste/drop files, MIME precedence, URI rejection and cancellation', pasted, dropped, customSnapshot, pageErrors, protectedRequests };
  } finally {
    await context.close();
  }
}

async function runNativeDropCancellation() {
  const { context, page, pageErrors, protectedRequests } = await openPage();
  try {
    const editor = page.getByTestId('rich-text-composer');
    await editor.click();
    await page.keyboard.type('取消前后', { delay: 8 });
    const point = await editorPoint(editor, '取消前后', 2);
    const file = [{ name: '取消.txt', mimeType: 'text/plain', contents: 'cancel me' }];
    assert.equal(await dispatchNativeFiles(page, 'dragover', file, point), true);
    assert.equal(await dispatchNativeFiles(page, 'drop', file, point), true);
    await page.getByRole('button', { name: '删除附件 取消.txt' }).waitFor({ state: 'visible' });
    await page.getByRole('button', { name: '删除附件 取消.txt' }).click();
    await page.waitForTimeout(260);
    assert.equal(await page.getByRole('button', { name: '删除附件 取消.txt' }).count(), 0);
    assert.equal((await editorSnapshot(editor)).textContent, '取消前后');
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(protectedRequests, []);
    return { name: 'native drop cancellation removes unfinished upload', pageErrors, protectedRequests };
  } finally {
    await context.close();
  }
}

async function runExternalMiddleDrop() {
  const { context, page, pageErrors, protectedRequests } = await openPage();
  try {
    const editor = page.getByTestId('rich-text-composer');
    await editor.click();
    const sourceText = '左边文字 右边文字';
    await page.keyboard.type(sourceText, { delay: 8 });
    await page.waitForTimeout(100);
    const editorBox = await editor.boundingBox();
    const target = await editorPoint(editor, sourceText, 5);
    assert.ok(editorBox && target);
    await installEventLog(page);
    await page.getByTestId('draggable-attachment').dragTo(editor, {
      targetPosition: { x: target.x - editorBox.x, y: target.y - editorBox.y },
    });
    await page.waitForTimeout(180);
    const snapshot = await editorSnapshot(editor);
    assert.equal(snapshot.textContent, '左边文字 接口说明.md右边文字');
    assert.deepEqual(snapshot.attachmentIds.length, 1);
    assert.deepEqual(snapshot.directChildren.map((child) => child.attachmentId), [null, snapshot.attachmentIds[0], null]);
    assert.deepEqual(snapshot.directChildren.map((child) => child.text), ['左边文字 ', '接口说明.md', '右边文字']);
    assert.deepEqual(snapshot.selection, { node: '#text', text: '右边文字', offset: 0 });
    assert.ok((await page.evaluate(() => window.__attachmentDndEvents))
      .some((event) => event.type === 'drop' && event.defaultPrevented));
    await editor.focus();
    await page.keyboard.type('继续');
    await page.waitForTimeout(100);
    const afterTyping = await editorSnapshot(editor);
    assert.equal(afterTyping.textContent, '左边文字 接口说明.md继续右边文字');
    assert.deepEqual(afterTyping.directChildren.map((child) => child.text), ['左边文字 ', '接口说明.md', '继续右边文字']);
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(protectedRequests, []);
    await page.screenshot({ path: path.join(runtime, 'external-middle-fixed.png'), fullPage: true });
    return { name: 'external attachment into typed text middle', snapshot, afterTyping, events: await page.evaluate(() => window.__attachmentDndEvents), pageErrors, protectedRequests };
  } finally {
    await context.close();
  }
}

async function runInternalMiddleMove() {
  const { context, page, pageErrors, protectedRequests } = await openPage();
  try {
    const editor = page.getByTestId('rich-text-composer');
    await editor.click();
    const sourceText = '左边文字 右边文字';
    await page.keyboard.type(sourceText, { delay: 8 });
    await page.waitForTimeout(100);
    const editorBox = await editor.boundingBox();
    const insertPoint = await editorPoint(editor, sourceText, 5);
    assert.ok(editorBox && insertPoint);
    await page.getByTestId('draggable-attachment').dragTo(editor, {
      targetPosition: { x: insertPoint.x - editorBox.x, y: insertPoint.y - editorBox.y },
    });
    await page.waitForTimeout(120);

    const editorAfterInsert = await editorSnapshot(editor);
    const attachmentId = editorAfterInsert.attachmentIds[0];
    assert.ok(attachmentId);
    const node = page.locator(`[data-composer-attachment="${attachmentId}"]`);
    const nodeBox = await node.boundingBox();
    const movePoint = await editorPoint(editor, '右边文字', 2);
    assert.ok(nodeBox && movePoint);
    await installEventLog(page);

    // Use mouse movement instead of locator.dragTo so the assertion observes
    // the real dragover state before the browser dispatches drop.
    await page.mouse.move(nodeBox.x + nodeBox.width / 2, nodeBox.y + nodeBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(nodeBox.x + nodeBox.width / 2 + 8, nodeBox.y + nodeBox.height / 2 + 4, { steps: 3 });
    await page.waitForTimeout(100);
    await page.mouse.move(movePoint.x, movePoint.y, { steps: 20 });
    await page.waitForTimeout(150);
    const during = await editorSnapshot(editor);
    assert.ok(during.indicator, 'internal dragover did not show an insertion caret');
    assert.deepEqual(during.attachmentIds, [attachmentId]);
    await page.screenshot({ path: path.join(runtime, 'internal-middle-during-fixed.png'), fullPage: true });
    await page.mouse.up();
    await page.waitForTimeout(180);

    const snapshot = await editorSnapshot(editor);
    assert.equal(snapshot.textContent, '左边文字 右边接口说明.md文字');
    assert.deepEqual(snapshot.attachmentIds, [attachmentId]);
    assert.deepEqual(snapshot.directChildren.map((child) => child.attachmentId), [null, attachmentId, null]);
    assert.deepEqual(snapshot.directChildren.map((child) => child.text), ['左边文字 右边', '接口说明.md', '文字']);
    assert.equal(snapshot.indicator, null);
    const events = await page.evaluate(() => window.__attachmentDndEvents);
    assert.ok(events.some((event) => event.type === 'dragover' && event.defaultPrevented && event.dropEffect === 'move'));
    assert.ok(events.some((event) => event.type === 'drop' && event.defaultPrevented));
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(protectedRequests, []);
    await page.screenshot({ path: path.join(runtime, 'internal-middle-after-fixed.png'), fullPage: true });
    return { name: 'inserted attachment moves to typed text middle', during, snapshot, events, pageErrors, protectedRequests };
  } finally {
    await context.close();
  }
}

async function runCtrlADeleteAttachment() {
  const { context, page, pageErrors, protectedRequests } = await openPage();
  try {
    const editor = page.getByTestId('rich-text-composer');
    const messageSource = page.getByTestId('draggable-attachment');
    const sourceText = '前面文字 后面文字';
    await editor.click();
    await page.keyboard.type(sourceText, { delay: 8 });
    await page.waitForTimeout(100);
    const editorBox = await editor.boundingBox();
    const insertPoint = await editorPoint(editor, sourceText, 5);
    assert.ok(editorBox && insertPoint);
    await messageSource.dragTo(editor, {
      targetPosition: { x: insertPoint.x - editorBox.x, y: insertPoint.y - editorBox.y },
    });
    await page.waitForTimeout(180);

    const beforeMessageClear = await editorSnapshot(editor);
    assert.equal(beforeMessageClear.textContent, '前面文字 接口说明.md后面文字');
    assert.deepEqual(beforeMessageClear.attachmentIds.length, 1);
    assert.equal(await page.locator('[data-testid="server-attachments"]').count(), 0);
    await page.screenshot({ path: path.join(runtime, 'ctrl-a-message-before.png'), fullPage: true });

    await editor.focus();
    await page.keyboard.press('Control+A');
    const selectedMessage = await page.evaluate(() => document.getSelection()?.toString() || '');
    assert.equal(selectedMessage, '前面文字 \n后面文字');
    await page.keyboard.press('Backspace');
    await page.waitForTimeout(180);
    const afterMessageClear = await editorSnapshot(editor);
    assert.equal(afterMessageClear.textContent, '');
    assert.deepEqual(afterMessageClear.attachmentIds, []);
    assert.equal(await page.locator('[data-testid="server-attachments"]').count(), 0);
    await page.screenshot({ path: path.join(runtime, 'ctrl-a-message-after.png'), fullPage: true });

    // Deleting an embedded occurrence does not disable the original message
    // source. A later drag creates a new occurrence with the existing product
    // semantics for message attachments.
    const reinsertText = '重新插入';
    await editor.click();
    await page.keyboard.type(reinsertText, { delay: 8 });
    await page.waitForTimeout(80);
    const reinsertBox = await editor.boundingBox();
    const reinsertPoint = await editorPoint(editor, reinsertText, 2);
    assert.ok(reinsertBox && reinsertPoint);
    await messageSource.dragTo(editor, {
      targetPosition: { x: reinsertPoint.x - reinsertBox.x, y: reinsertPoint.y - reinsertBox.y },
    });
    await page.waitForTimeout(160);
    const afterMessageReinsert = await editorSnapshot(editor);
    assert.equal(afterMessageReinsert.textContent, '重新接口说明.md插入');
    assert.deepEqual(afterMessageReinsert.attachmentIds.length, 1);
    assert.equal(await page.locator('[data-testid="server-attachments"]').count(), 0);

    // Ordinary Backspace remains the atomic-node path. The reinserted node is
    // removed without changing the surrounding text or creating a chip.
    await editor.focus();
    await page.keyboard.press('Backspace');
    await page.waitForTimeout(180);
    const afterOrdinaryBackspace = await editorSnapshot(editor);
    assert.equal(afterOrdinaryBackspace.textContent, '重新插入');
    assert.deepEqual(afterOrdinaryBackspace.attachmentIds, []);
    assert.equal(await page.locator('[data-testid="server-attachments"]').count(), 0);
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(protectedRequests, []);
    return {
      name: 'Ctrl+A Backspace deletes embedded message attachment',
      beforeMessageClear,
      selectedMessage,
      afterMessageClear,
      afterMessageReinsert,
      afterOrdinaryBackspace,
      pageErrors,
      protectedRequests,
    };
  } finally {
    await context.close();
  }
}

async function runCtrlADeleteUploadedAttachment() {
  const { context, page, pageErrors, protectedRequests } = await openPage();
  try {
    const editor = page.getByTestId('rich-text-composer');
    await page.getByRole('button', { name: '添加附件' }).click();
    await page.getByRole('button', { name: '客户端附件' }).click();
    await page.getByTestId('client-attachment-input').setInputFiles({
      name: 'standalone.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('mock upload'),
    });
    await page.getByTestId('attachment-upload-progress').waitFor({ state: 'visible' });
    await page.waitForFunction(() => document.querySelector('[data-testid="attachment-upload-progress"]')?.textContent?.includes('已完成'));
    const chip = page.getByTestId('draggable-attachment-chip').filter({ hasText: 'standalone.txt' }).first();
    await chip.waitFor({ state: 'visible' });

    // Ctrl+A while only ordinary text is embedded must preserve an unrelated
    // ready chip above the editor.
    const pendingText = '仅有文字';
    await editor.click();
    await page.keyboard.type(pendingText, { delay: 8 });
    await editor.focus();
    await page.keyboard.press('Control+A');
    await page.keyboard.press('Backspace');
    await page.waitForTimeout(160);
    const afterTextOnlyClear = await editorSnapshot(editor);
    assert.equal(afterTextOnlyClear.textContent, '');
    assert.deepEqual(afterTextOnlyClear.attachmentIds, []);
    assert.equal(await page.getByTestId('draggable-attachment-chip').filter({ hasText: 'standalone.txt' }).count(), 1);

    await page.getByRole('button', { name: 'Send' }).click();
    // ?mock=1 intercepts fetch in the page, so Playwright's network request
    // events cannot observe this request. Assert the user-visible queue item
    // instead, which proves the standalone chip remained sendable and was
    // serialized as a Markdown attachment link.
    await page.getByRole('button', { name: '发送队列' }).click();
    await page.waitForFunction(() => document.body.innerText.includes('[standalone.txt]('));
    const queueText = await page.locator('[data-testid="send-queue-anchor"]').innerText();
    const standaloneSendText = queueText.match(/\[standalone\.txt\]\([^\n]+\)/)?.[0];
    assert.ok(standaloneSendText);
    assert.match(standaloneSendText, /^\[standalone\.txt\]\(\/api\/attachments\/upload_[a-z0-9]{32}\.txt\?session_id=mock-alpha\)$/);
    await page.waitForTimeout(180);
    assert.equal(await page.getByTestId('draggable-attachment-chip').filter({ hasText: 'standalone.txt' }).count(), 0);

    // A fresh completed upload can still be embedded. Ctrl+A + Backspace is
    // deletion of that embedded attachment, so it must not return as a chip.
    await page.getByTestId('client-attachment-input').setInputFiles({
      name: 'embedded.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('embedded mock upload'),
    });
    await page.waitForFunction(() => document.querySelector('[data-testid="attachment-upload-progress"]')?.textContent?.includes('已完成'));
    const embeddedChip = page.getByTestId('draggable-attachment-chip').filter({ hasText: 'embedded.txt' }).first();
    await embeddedChip.waitFor({ state: 'visible' });
    const uploadText = '上传前后';
    await editor.click();
    await page.keyboard.type(uploadText, { delay: 8 });
    await page.waitForTimeout(80);
    const editorBox = await editor.boundingBox();
    const insertPoint = await editorPoint(editor, uploadText, 2);
    assert.ok(editorBox && insertPoint);
    await embeddedChip.dragTo(editor, {
      targetPosition: { x: insertPoint.x - editorBox.x, y: insertPoint.y - editorBox.y },
    });
    await page.waitForTimeout(180);
    const beforeUploadClear = await editorSnapshot(editor);
    assert.equal(beforeUploadClear.textContent, '上传embedded.txt前后');
    assert.deepEqual(beforeUploadClear.attachmentIds.length, 1);
    assert.equal(await page.getByTestId('draggable-attachment-chip').filter({ hasText: 'embedded.txt' }).count(), 0);
    await page.screenshot({ path: path.join(runtime, 'ctrl-a-upload-before.png'), fullPage: true });

    await editor.focus();
    await page.keyboard.press('Control+A');
    await page.keyboard.press('Backspace');
    await page.waitForTimeout(180);
    const afterUploadClear = await editorSnapshot(editor);
    assert.equal(afterUploadClear.textContent, '');
    assert.deepEqual(afterUploadClear.attachmentIds, []);
    assert.equal(await page.getByTestId('draggable-attachment-chip').filter({ hasText: 'embedded.txt' }).count(), 0);
    await page.screenshot({ path: path.join(runtime, 'ctrl-a-upload-after.png'), fullPage: true });
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(protectedRequests, []);
    return {
      name: 'standalone upload sends, embedded upload is deleted by Ctrl+A Backspace',
      afterTextOnlyClear,
      standaloneSendText,
      beforeUploadClear,
      afterUploadClear,
      pageErrors,
      protectedRequests,
    };
  } finally {
    await context.close();
  }
}

try {
  results.push(await runExternalMiddleDrop());
  results.push(await runInternalMiddleMove());
  results.push(await runCtrlADeleteAttachment());
  results.push(await runCtrlADeleteUploadedAttachment());
  results.push(await runNativeFileInput());
  results.push(await runNativeDropCancellation());
} finally {
  await browser.close();
}

await fs.writeFile(path.join(runtime, 'results.json'), JSON.stringify({
  baseURL: mockURL(),
  browser: browserVersion,
  results,
}, null, 2));
for (const result of results) console.log(`PASS ${result.name}`);
