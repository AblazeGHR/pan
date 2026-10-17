/* global URL, console, process, setTimeout */
/**
 * Composer URL-node Chromium acceptance.
 *
 * Real: Chromium, the React app (ChatView + RichTextComposer), Vite, the
 * Markdown renderer, real mouse drag (Playwright dragTo), real clicks,
 * selection, draft PUT/GET bodies, and session switching through the real
 * session store action.
 *
 * Mock boundary: every `/api/**` response is fulfilled by Playwright routes.
 * No Pan server, provider, agent, or real user data is involved.  The draft
 * PUT/GET and queue POST bodies are produced by the real client code.
 *
 * Run from packages/web: node e2e/composerUrlNode.e2e.mjs
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';

const web = path.resolve(import.meta.dirname, '..');
const output = path.join(web, 'test-results/composer-url-node');
await fs.mkdir(output, { recursive: true });

const port = await new Promise((resolve, reject) => {
  const probe = net.createServer();
  probe.once('error', reject);
  probe.listen(0, '127.0.0.1', () => {
    const value = probe.address().port;
    probe.close(() => resolve(value));
  });
});
assert.ok(port !== 8767 && port !== 8768, 'must not use a practical Pan port');

const server = await createServer({
  configFile: false,
  root: web,
  cacheDir: path.join(output, 'vite-cache'),
  resolve: { alias: { '@': path.join(web, 'src') } },
  server: { host: '127.0.0.1', port, strictPort: true, proxy: {} },
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'composer-url-node-fixture',
      configureServer(vite) {
        vite.middlewares.use('/__fixture', async (_req, res) => {
          res.setHeader('Content-Type', 'text/html');
          res.end(
            await vite.transformIndexHtml(
              '/__fixture',
              '<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/e2e/composerUrlNodeFixture.tsx"></script></body></html>',
            ),
          );
        });
      },
    },
  ],
});
await server.listen();

const evidence = {
  port,
  mockBoundary:
    'All /api/** responses are Playwright route mocks. Chromium, Vite, React, the composer, real mouse drag/clicks, session switching, and the Markdown renderer are real. No Pan server/provider/agent or real user data is involved.',
  checks: {},
  drafts: [],
  queue: [],
  errors: [],
};
const browser = await chromium.launch({ headless: true });

const LONG_URL =
  'https://example.test/' + 'very-long-path-segment/'.repeat(8)
  + 'report?q=' + 'query-value&'.repeat(6) + 'last=1#section-' + 'x'.repeat(40);

function installRoutes(page, state) {
  page.on('pageerror', (error) => evidence.errors.push(String(error)));
  return page.route('**/api/**', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === '/api/sessions' && request.method() === 'GET') {
      const session = (id, name) => ({
        id,
        name,
        adapter: 'codex',
        workerId: `w-${id}`,
        workerStatus: 'running',
        nativeControl: true,
        alwaysThinkingEnabled: false,
        effort: '',
        workdir: '/fixture',
        historyTotal: 0,
      });
      await route.fulfill({ json: { sessions: [session('s1', 'Composer URL node'), session('s2', 'Other draft')] } });
      return;
    }
    if (/^\/api\/sessions\/s[12]\/history$/.test(pathname)) {
      await route.fulfill({ json: { history: [], total: 0, hasMore: false, start: 0 } });
      return;
    }
    if (pathname.endsWith('/draft')) {
      const sessionId = pathname.match(/^\/api\/sessions\/([^/]+)\/draft$/)?.[1] || 'unknown';
      if (request.method() === 'PUT') {
        const body = request.postDataJSON();
        state.draftsBySession[sessionId] = body.draft;
        state.draftWrites.push({ sessionId, body });
        evidence.drafts.push(body);
        state.revisions[sessionId] = (state.revisions[sessionId] || 0) + 1;
        await route.fulfill({
          json: {
            revision: state.revisions[sessionId],
            draft: body.draft,
            updatedAt: new Date().toISOString(),
          },
        });
      } else {
        await route.fulfill({
          json: {
            revision: state.revisions[sessionId] || 0,
            draft: state.draftsBySession[sessionId] ?? null,
            updatedAt: state.draftsBySession[sessionId] ? new Date().toISOString() : null,
          },
        });
      }
      return;
    }
    if (pathname.endsWith('/queue') && request.method() === 'POST') {
      const body = request.postDataJSON();
      evidence.queue.push(body);
      await route.fulfill({
        json: {
          ok: true,
          item: {
            id: body.clientMessageId,
            text: body.text,
            source: 'user',
            kind: 'task',
            createdAt: 1,
            meta: { dispatchState: 'queued', revision: 1 },
          },
          queueRevision: 1,
          queuePaused: true,
        },
      });
      return;
    }
    if (pathname.endsWith('/queue')) {
      await route.fulfill({
        json: { items: [], queueRevision: 0, queuePaused: true, agentReportsPaused: false },
      });
      return;
    }
    await route.fulfill({ json: {} });
  });
}

async function openPage(viewport) {
  const context = await browser.newContext({ viewport });
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], {
    origin: `http://127.0.0.1:${port}`,
  });
  await context.tracing.start({ screenshots: true, snapshots: true });
  const page = await context.newPage();
  const state = { draftsBySession: {}, revisions: {}, draftWrites: [] };
  await installRoutes(page, state);
  await page.goto(`http://127.0.0.1:${port}/__fixture`);
  const editor = page.getByTestId('rich-text-composer');
  await editor.waitFor({ state: 'visible' });
  await page.waitForLoadState('networkidle');
  return { context, page, editor, state };
}

function setCaret(page, at) {
  return page.evaluate((value) => {
    const element = document.querySelector('[data-testid="rich-text-composer"]');
    const range = document.createRange();
    if (value === 'end') {
      range.selectNodeContents(element);
      range.collapse(false);
    } else {
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      let remaining = value;
      let node = walker.nextNode();
      while (node) {
        const length = node.textContent.length;
        if (remaining <= length) {
          range.setStart(node, remaining);
          break;
        }
        remaining -= length;
        node = walker.nextNode();
      }
      range.collapse(true);
    }
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    element.focus();
  }, at);
}

function paste(page, text, html) {
  return page.evaluate(({ value, markup }) => {
    const element = document.querySelector('[data-testid="rich-text-composer"]');
    const dataTransfer = new DataTransfer();
    if (value !== undefined) dataTransfer.setData('text/plain', value);
    if (markup !== undefined) dataTransfer.setData('text/html', markup);
    const event = new ClipboardEvent('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'clipboardData', { value: dataTransfer });
    element.dispatchEvent(event);
  }, { value: text, markup: html });
}

const editorState = (page) =>
  page.evaluate(() => {
    const element = document.querySelector('[data-testid="rich-text-composer"]');
    return {
      urls: Array.from(element.querySelectorAll('[data-composer-url]')).map((node) => ({
        id: node.getAttribute('data-composer-url-id'),
        url: node.getAttribute('data-composer-url'),
        text: node.textContent,
      })),
      order: Array.from(element.children).map((child) =>
        child.hasAttribute('data-composer-url') ? 'url' : 'text',
      ),
      text: element.textContent,
    };
  });

try {
  // ── A. Desktop: real mouse drag, delete, undo audit, duplicate intent ──
  {
    const { context, page, editor } = await openPage({ width: 1440, height: 900 });

    // Real renderer link from the seeded history message.
    const anchor = page.locator('a[href="https://example.test/history?q=1#h"]');
    await expect(anchor).toBeVisible();
    evidence.checks.messageLink = {
      href: await anchor.getAttribute('href'),
      text: await anchor.textContent(),
    };

    await editor.fill('before after');
    await setCaret(page, 7);
    await paste(page, 'https://example.test/a?q=1#h');
    await expect(editor.locator('[data-composer-url]')).toHaveCount(1);
    const pasted = await editorState(page);
    assert.deepEqual(pasted.order, ['text', 'url', 'text']);
    evidence.checks.paste = pasted;
    const idBeforeDrag = pasted.urls[0].id;
    await page.screenshot({ path: path.join(output, '01-paste-node.png') });

    // Real mouse drag: Playwright moves the mouse over the node, presses,
    // moves to the end of the editor and releases.  No DragEvent dispatch.
    const editorBox = await editor.boundingBox();
    assert.ok(editorBox);
    await editor.locator('[data-composer-url]').dragTo(editor, {
      targetPosition: { x: editorBox.width - 16, y: editorBox.height - 16 },
    });
    await page.waitForTimeout(150);
    const dragged = await editorState(page);
    assert.equal(dragged.urls.length, 1);
    assert.equal(dragged.urls[0].id, idBeforeDrag);
    assert.equal(dragged.order.at(-1), 'url');
    assert.equal(dragged.urls[0].url, 'https://example.test/a?q=1#h');
    evidence.checks.mouseDrag = dragged;
    await page.screenshot({ path: path.join(output, '02-mouse-drag-node.png') });

    // Real click on the node's delete button.
    await page.locator('[data-composer-url-delete]').click();
    await expect(editor.locator('[data-composer-url]')).toHaveCount(0);
    evidence.checks.delete = await editorState(page);
    await page.screenshot({ path: path.join(output, '03-delete-node.png') });

    // Undo audit: compare the URL node with the existing attachment node.
    // Both are composer-model transactions, not contentEditable typing.
    await setCaret(page, 'end');
    await paste(page, 'https://undo.test/a');
    const urlBeforeUndo = (await editorState(page)).urls.length;
    await editor.press('Control+Z');
    await page.waitForTimeout(120);
    const urlAfterUndo = (await editorState(page)).urls.length;

    // A deterministic attachment node for an apples-to-apples comparison.
    await page.evaluate(() => {
      const element = document.querySelector('[data-testid="rich-text-composer"]');
      const dataTransfer = new DataTransfer();
      dataTransfer.setData(
        'application/x-pan-attachment',
        JSON.stringify({
          displayName: 'undo.txt',
          href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.txt?session_id=s1',
        }),
      );
      const event = new ClipboardEvent('paste', { bubbles: true, cancelable: true });
      Object.defineProperty(event, 'clipboardData', { value: dataTransfer });
      element.dispatchEvent(event);
    });
    await page.waitForTimeout(100);
    const attachmentBeforeUndo = await page.locator('[data-composer-attachment]').count();
    await editor.press('Control+Z');
    await page.waitForTimeout(120);
    const attachmentAfterUndo = await page.locator('[data-composer-attachment]').count();
    evidence.checks.undo = {
      urlBeforeUndo,
      urlAfterUndo,
      attachmentBeforeUndo,
      attachmentAfterUndo,
    };

    await context.tracing.stop({ path: path.join(output, 'trace-desktop.zip') });
    await context.close();
  }

  // ── A2. Fresh composer: duplicate paste intent + outbound payload ──
  {
    const { context, page, editor } = await openPage({ width: 1440, height: 900 });

    // Same URL pasted twice stays two nodes with distinct identities.
    await setCaret(page, 'end');
    await paste(page, 'https://dup.test/a');
    await setCaret(page, 'end');
    await paste(page, 'https://dup.test/a');
    const duplicates = await editorState(page);
    assert.equal(duplicates.urls.length, 2);
    assert.notEqual(duplicates.urls[0].id, duplicates.urls[1].id);
    evidence.checks.duplicates = duplicates;

    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect.poll(() => evidence.queue.length).toBe(1);
    assert.equal(
      evidence.queue[0].text,
      '[https://dup.test/a](https://dup.test/a)[https://dup.test/a](https://dup.test/a)',
    );
    assert.deepEqual(evidence.queue[0].parts, [
      { type: 'text', text: '[https://dup.test/a](https://dup.test/a)' },
      { type: 'text', text: '[https://dup.test/a](https://dup.test/a)' },
    ]);
    evidence.checks.send = evidence.queue[0];

    await context.tracing.stop({ path: path.join(output, 'trace-desktop-send.zip') });
    await context.close();
  }

  // ── A3. Real clipboard: copy writes Markdown; paste recreates the node ──
  {
    const { context, page, editor } = await openPage({ width: 1440, height: 900 });
    await setCaret(page, 'end');
    await paste(page, undefined, '<a href="https://clip.test/a?q=1#h">Clip Docs</a>');
    await expect(editor.locator('[data-composer-url]')).toHaveCount(1);

    await page.evaluate(() => {
      const node = document.querySelector('[data-composer-url]');
      const range = document.createRange();
      range.setStartBefore(node);
      range.setEndAfter(node);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      document.querySelector('[data-testid="rich-text-composer"]').focus();
    });
    await page.keyboard.press('Control+C');
    const clipboardText = await page.evaluate(() => navigator.clipboard.readText());
    assert.equal(clipboardText, '[Clip Docs](https://clip.test/a?q=1#h)');

    await editor.click();
    await page.keyboard.press('Control+A');
    await page.keyboard.press('Delete');
    await expect(editor.locator('[data-composer-url]')).toHaveCount(0);
    await page.keyboard.press('Control+V');
    await expect(editor.locator('[data-composer-url]')).toHaveCount(1);
    const copied = await editorState(page);
    assert.equal(copied.urls[0].url, 'https://clip.test/a?q=1#h');
    assert.match(copied.urls[0].text, /Clip Docs/);
    evidence.checks.copyPaste = { clipboard: clipboardText, ...copied };
    await page.screenshot({ path: path.join(output, '07-clipboard-roundtrip.png') });

    await context.tracing.stop({ path: path.join(output, 'trace-clipboard.zip') });
    await context.close();
  }

  // ── B. Mobile 390px: long node, delete, draft/session switch, send ──
  {
    const { context, page, editor, state } = await openPage({ width: 390, height: 780 });

    await setCaret(page, 'end');
    await paste(page, LONG_URL);
    await expect(editor.locator('[data-composer-url]')).toHaveCount(1);
    const node = editor.locator('[data-composer-url]');
    const nodeBox = await node.boundingBox();
    const editorBox = await editor.boundingBox();
    const deleteButton = page.locator('[data-composer-url-delete]');
    const deleteBox = await deleteButton.boundingBox();
    assert.ok(nodeBox && editorBox && deleteBox);
    assert.ok(nodeBox.x >= editorBox.x - 1, 'node must not overflow left');
    assert.ok(
      nodeBox.x + nodeBox.width <= editorBox.x + editorBox.width + 1,
      'node must not overflow right',
    );
    assert.ok(deleteBox.x + deleteBox.width <= editorBox.x + editorBox.width + 1);
    assert.equal(await deleteButton.isVisible(), true);
    evidence.checks.mobileLayout = {
      viewport: { width: 390, height: 780 },
      nodeBox,
      editorBox,
      deleteBox,
    };
    await page.screenshot({ path: path.join(output, '04-mobile-long-node.png') });

    await deleteButton.click();
    await expect(editor.locator('[data-composer-url]')).toHaveCount(0);

    // Draft + session switch: paste again, wait for the real draft PUT,
    // switch to s2 and back, then reload and restore from the server draft.
    await setCaret(page, 'end');
    await paste(page, LONG_URL);
    const longId = (await editorState(page)).urls[0].id;
    await expect
      .poll(() => state.draftWrites.some((write) => write.sessionId === 's1'))
      .toBe(true);
    const stored = state.draftsBySession.s1;

    // Switch Sessions through the production store entry point used by every
    // session card.  The App-level mobile drawer is not mounted in this
    // ChatView fixture; the action itself is the real `selectSession`.
    await page.evaluate(() => window.__panSelectSession?.('s2'));
    await expect(editor.locator('[data-composer-url]')).toHaveCount(0);
    const s2State = await editorState(page);
    assert.equal(s2State.urls.length, 0);
    assert.equal(s2State.text, '');

    // Give s2 its own draft, then verify both directions keep their content.
    await editor.fill('s2 draft');
    await expect.poll(() => state.draftsBySession.s2?.text).toBe('s2 draft');

    await page.evaluate(() => window.__panSelectSession?.('s1'));
    await expect(editor.locator('[data-composer-url]')).toHaveCount(1);
    const backState = await editorState(page);
    assert.equal(backState.urls[0].id, longId);
    assert.equal(backState.urls[0].url, LONG_URL);

    await page.evaluate(() => window.__panSelectSession?.('s2'));
    await expect.poll(async () => (await editorState(page)).text).toBe('s2 draft');
    const s2BackState = await editorState(page);
    assert.equal(s2BackState.urls.length, 0);

    await page.evaluate(() => window.__panSelectSession?.('s1'));
    await expect(editor.locator('[data-composer-url]')).toHaveCount(1);
    evidence.checks.sessionSwitch = {
      s2: s2State,
      s1: backState,
      s2Draft: s2BackState,
    };

    await page.reload();
    await editor.waitFor({ state: 'visible' });
    await expect(editor.locator('[data-composer-url]')).toHaveCount(1);
    const reloaded = await editorState(page);
    assert.equal(reloaded.urls[0].id, longId);
    assert.equal(reloaded.urls[0].url, LONG_URL);
    assert.ok(stored && stored.parts.some((part) => part.type === 'url'));
    evidence.checks.draftReload = { draft: stored, restored: reloaded.urls[0] };
    await page.screenshot({ path: path.join(output, '05-mobile-draft-restored.png') });

    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect.poll(() => evidence.queue.length).toBe(2);
    const sent = evidence.queue[1];
    assert.equal(sent.text, `[${LONG_URL}](${LONG_URL})`);
    assert.deepEqual(sent.parts, [{ type: 'text', text: `[${LONG_URL}](${LONG_URL})` }]);
    evidence.checks.mobileSend = sent;
    await page.screenshot({ path: path.join(output, '06-mobile-sent.png') });

    await context.tracing.stop({ path: path.join(output, 'trace-mobile.zip') });
    await context.close();
  }
} finally {
  await browser.close();
  await server.close();
}

assert.deepEqual(evidence.errors, []);
await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(evidence, null, 2));
console.log(JSON.stringify(evidence, null, 2));
