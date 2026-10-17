/* global URL, console, process, setTimeout */
/**
 * Composer URL-node Chromium acceptance.
 *
 * Real: Chromium, the React app (ChatView + RichTextComposer), Vite, the
 * Markdown renderer, and the browser's native Selection/DragEvent plumbing.
 *
 * Mock boundary: every `/api/**` response is fulfilled by Playwright routes.
 * No Pan server, provider, agent, or real user data is involved.  The draft
 * PUT and queue POST bodies are captured from the real client code, which is
 * what this harness is meant to verify.
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
    'All /api/** responses are Playwright route mocks. Chromium, Vite, React, the composer, drag/selection, and the Markdown renderer are real. No Pan server/provider/agent or real user data is involved.',
  checks: {},
  drafts: [],
  queue: [],
  errors: [],
};
const browser = await chromium.launch({ headless: true });
let page;
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.tracing.start({ screenshots: true, snapshots: true });
  page = await context.newPage();
  page.on('pageerror', (error) => evidence.errors.push(String(error)));

  let revision = 0;
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname.endsWith('/draft')) {
      if (request.method() === 'PUT') {
        const body = request.postDataJSON();
        evidence.drafts.push(body);
        revision += 1;
        await route.fulfill({
          json: { revision, draft: body.draft, updatedAt: new Date().toISOString() },
        });
      } else {
        await route.fulfill({ json: { revision, draft: null, updatedAt: null } });
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

  await page.goto(`http://127.0.0.1:${port}/__fixture`);
  const editor = page.getByTestId('rich-text-composer');
  await editor.waitFor({ state: 'visible' });
  await page.waitForLoadState('networkidle');

  const setCaret = (at) =>
    page.evaluate((value) => {
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

  const paste = (text) =>
    page.evaluate((value) => {
      const element = document.querySelector('[data-testid="rich-text-composer"]');
      const dataTransfer = new DataTransfer();
      dataTransfer.setData('text/plain', value);
      const event = new ClipboardEvent('paste', { bubbles: true, cancelable: true });
      Object.defineProperty(event, 'clipboardData', { value: dataTransfer });
      element.dispatchEvent(event);
    }, text);

  // 1. Mixed text + URL paste becomes one inline node between the text runs.
  await editor.fill('before after');
  await setCaret(7);
  await paste('https://example.test/a?q=1#h');
  await expect(editor.locator('[data-composer-url]')).toHaveCount(1);
  const pasted = await page.evaluate(() => {
    const element = document.querySelector('[data-testid="rich-text-composer"]');
    const node = element.querySelector('[data-composer-url]');
    return {
      url: node.getAttribute('data-composer-url'),
      text: node.textContent,
      order: Array.from(element.children).map((child) =>
        child.hasAttribute('data-composer-url') ? 'url' : 'text',
      ),
    };
  });
  assert.equal(pasted.url, 'https://example.test/a?q=1#h');
  assert.match(pasted.text, /https:\/\/example\.test\/a\?q=1#h/);
  assert.deepEqual(pasted.order, ['text', 'url', 'text']);
  evidence.checks.paste = pasted;
  await page.screenshot({ path: path.join(output, '01-paste-node.png') });

  // 2. Drag the node to the end of the editor (same HTML5 drag contract as an
  // inline attachment: dragstart writes the transfer, dragover/drop place it).
  const draggedDispatch = await page.evaluate(() => {
    const element = document.querySelector('[data-testid="rich-text-composer"]');
    const node = element.querySelector('[data-composer-url]');
    const dataTransfer = new DataTransfer();
    node.dispatchEvent(
      new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer }),
    );
    const rect = element.getBoundingClientRect();
    const clientX = rect.right - 12;
    const clientY = rect.bottom - 12;
    element.dispatchEvent(
      new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer, clientX, clientY }),
    );
    element.dispatchEvent(
      new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer, clientX, clientY }),
    );
    return { mime: dataTransfer.getData('application/x-pan-composer-url') };
  });
  await page.waitForTimeout(80);
  const dragged = await page.evaluate(() => {
    const element = document.querySelector('[data-testid="rich-text-composer"]');
    return {
      count: element.querySelectorAll('[data-composer-url]').length,
      lastIsUrl: element.lastElementChild?.hasAttribute('data-composer-url') || false,
      text: element.textContent,
    };
  });
  assert.match(draggedDispatch.mime, /https:\/\/example\.test\/a\?q=1#h/);
  assert.equal(dragged.count, 1);
  assert.equal(dragged.lastIsUrl, true);
  evidence.checks.drag = { ...draggedDispatch, ...dragged };
  await page.screenshot({ path: path.join(output, '02-drag-node.png') });

  // 3. Delete with the node's own button.
  await page.waitForTimeout(80); // release the drag-click suppression timer
  await page.evaluate(() => {
    const element = document.querySelector('[data-testid="rich-text-composer"]');
    element.querySelector('[data-composer-url-delete]').click();
  });
  await page.waitForTimeout(80);
  const deleted = await page.evaluate(() => {
    const element = document.querySelector('[data-testid="rich-text-composer"]');
    return {
      count: element.querySelectorAll('[data-composer-url]').length,
      text: element.textContent,
    };
  });
  assert.equal(deleted.count, 0);
  assert.equal(deleted.text, 'before after');
  evidence.checks.delete = deleted;
  await page.screenshot({ path: path.join(output, '03-delete-node.png') });

  // 4. A real send posts the Markdown link (and structured text part).
  await setCaret('end');
  await paste('https://example.test/out?q=1#h');
  await expect(editor.locator('[data-composer-url]')).toHaveCount(1);
  await expect
    .poll(() => evidence.drafts.some((draft) => JSON.stringify(draft).includes('"type":"url"')))
    .toBe(true);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect.poll(() => evidence.queue.length).toBe(1);
  const queued = evidence.queue[0];
  assert.equal(
    queued.text,
    'before after[https://example.test/out?q=1#h](https://example.test/out?q=1#h)',
  );
  assert.deepEqual(queued.parts, [
    { type: 'text', text: 'before after' },
    {
      type: 'text',
      text: '[https://example.test/out?q=1#h](https://example.test/out?q=1#h)',
    },
  ]);
  evidence.checks.send = queued;
  evidence.checks.draft = evidence.drafts.find((draft) =>
    JSON.stringify(draft).includes('"type":"url"'),
  );

  // 5. The real Markdown renderer turns a user message into a clickable link.
  const anchor = page.locator('a[href="https://example.test/history?q=1#h"]');
  await expect(anchor).toBeVisible();
  assert.equal(await anchor.textContent(), 'Docs');
  evidence.checks.messageLink = {
    href: await anchor.getAttribute('href'),
    text: await anchor.textContent(),
  };
  await page.screenshot({ path: path.join(output, '04-message-link.png') });

  await context.tracing.stop({ path: path.join(output, 'trace.zip') });
} finally {
  if (page) await page.close().catch(() => {});
  await browser.close();
  await server.close();
}

assert.deepEqual(evidence.errors, []);
await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(evidence, null, 2));
console.log(JSON.stringify(evidence, null, 2));
