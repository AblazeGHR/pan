/* global Element, NodeFilter, URL, console, document, process, window */

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

const baseURL = process.env.PAN_ATTACHMENT_DND_BASE_URL || 'http://127.0.0.1:5173';
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

try {
  results.push(await runExternalMiddleDrop());
  results.push(await runInternalMiddleMove());
} finally {
  await browser.close();
}

await fs.writeFile(path.join(runtime, 'results.json'), JSON.stringify({
  baseURL: mockURL(),
  browser: browserVersion,
  results,
}, null, 2));
for (const result of results) console.log(`PASS ${result.name}`);
