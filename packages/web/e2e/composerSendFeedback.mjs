/* global URL:readonly, console:readonly, KeyboardEvent:readonly, Buffer:readonly */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';

const web = path.resolve(import.meta.dirname, '..');
const output = path.join(web, 'test-results/composer-send-feedback');
await fs.mkdir(output, { recursive: true });
const port = await new Promise((resolve, reject) => {
  const probe = net.createServer();
  probe.once('error', reject);
  probe.listen(0, '127.0.0.1', () => {
    const value = probe.address().port;
    probe.close(() => resolve(value));
  });
});
assert.ok(port !== 8767 && port !== 8768);
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
      name: 'composer-fixture',
      configureServer(vite) {
        vite.middlewares.use('/__fixture', async (_req, res) => {
          res.setHeader('Content-Type', 'text/html');
          res.end(
            await vite.transformIndexHtml(
              '/__fixture',
              '<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/e2e/composerSendFixture.tsx"></script></body></html>',
            ),
          );
        });
      },
    },
  ],
});
await server.listen();
const browser = await chromium.launch({ headless: true });
const evidence = { port, tests: [], requests: [], errors: [], transactions: [] };
let active;
try {
  for (const mobile of [false, true]) {
    const context = await browser.newContext({
      viewport: mobile ? { width: 390, height: 780 } : { width: 1440, height: 900 },
      isMobile: mobile,
      hasTouch: mobile,
    });
    await context.tracing.start({ screenshots: true, snapshots: true });
    const page = await context.newPage();
    active = page;
    page.on('pageerror', (error) => evidence.errors.push(String(error)));
    page.on('request', (request) => evidence.requests.push(request.url()));
    let pending,
      resolveRequest,
      uploads = 0,
      revision = 0;
    let mode = 'pan';
    let releaseUpload;
    await page.route('**/api/**', async (route) => {
      const request = route.request(),
        url = new URL(request.url()),
        pathname = url.pathname;
      if (
        (pathname.endsWith('/queue') || pathname.endsWith('/worker/steer')) &&
        request.method() === 'POST'
      ) {
        pending = { route, body: request.postDataJSON(), pathname };
        evidence.transactions.push({ ...pending, route: undefined });
        await new Promise((resolve) => {
          resolveRequest = resolve;
        });
        return;
      }
      if (pathname.endsWith('/attachments') && request.method() === 'POST') {
        uploads++;
        await new Promise((resolve) => {
          releaseUpload = resolve;
        });
        await route.fulfill({
          json: {
            ok: true,
            filename: 'note.txt',
            displayName: 'note.txt',
            storageFilename: `upload_${'a'.repeat(32)}.txt`,
            attachmentId: `att_${'b'.repeat(32)}`,
            href: `/api/attachments/upload_${'a'.repeat(32)}.txt?session_id=s1`,
            path: '/fixture/note.txt',
            size: 5,
          },
        });
        return;
      }
      if (pathname.includes('/native-control')) {
        if (pathname.endsWith('/mode')) mode = request.postDataJSON().mode;
        const result = {
          workerId: `w-${pathname.includes('/s2/') ? 's2' : 's1'}`,
          mode,
          generation: 1,
          enabled: true,
          pendingEnabled: null,
          error: null,
          cursor: '0',
          data: '',
          gap: false,
        };
        await route.fulfill({ json: { ok: true, result } });
        return;
      }
      if (pathname.endsWith('/draft')) {
        await route.fulfill({
          json: {
            revision: request.method() === 'PUT' ? ++revision : 0,
            draft: request.method() === 'PUT' ? request.postDataJSON().draft : null,
            updatedAt: null,
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
      if (pathname.endsWith('/workspaces')) {
        await route.fulfill({ json: { workspaces: [] } });
        return;
      }
      await route.fulfill({ json: {} });
    });
    const open = async () => {
      pending = undefined;
      await page.goto(`http://127.0.0.1:${port}/__fixture`);
      await page.getByTestId('rich-text-composer').waitFor({ state: 'visible' });
      await page.waitForLoadState('networkidle');
    };
    const settle = async (failure = false) => {
      if (failure)
        await pending.route.fulfill({ status: failure === 'application' ? 200 : 409, json: { error: 'explicit rejection' } });
      else if (pending.pathname.endsWith('/worker/steer'))
        await pending.route.fulfill({ json: { workerId: 'w-s1', status: 'steer sent' } });
      else
        await pending.route.fulfill({
          json: {
            ok: true,
            item: {
              id: pending.body.clientMessageId,
              text: pending.body.text,
              source: 'user',
              kind: 'task',
              createdAt: 1,
              meta: { dispatchState: 'queued', revision: 1 },
            },
            queueRevision: 1,
            queuePaused: true,
          },
        });
      resolveRequest();
      pending = undefined;
    };
    for (const action of ['Send', 'Steer']) {
      await open();
      const editor = page.getByTestId('rich-text-composer');
      await editor.fill(`${action} original`);
      await editor.evaluate((element) =>
        element.dispatchEvent(
          new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }),
        ),
      );
      assert.equal(pending, undefined);
      const button = page.getByRole('button', { name: action, exact: true });
      await button.click();
      await expect(editor).toHaveText('');
      await expect(button).toHaveAttribute('aria-busy', 'true');
      await expect(button).toBeDisabled();
      await expect(button.locator('svg.lucide-loader-circle')).toHaveCount(1);
      await expect.poll(() => pending?.body.text).toBe(`${action} original`);
      const before = evidence.transactions.length;
      await editor.fill('new input');
      await editor.press('Enter');
      assert.equal(evidence.transactions.length, before);
      await page.screenshot({
        path: path.join(
          output,
          `${mobile ? 'mobile' : 'desktop'}-${action.toLowerCase()}-pending.png`,
        ),
      });
      await settle(action === 'Steer' ? 'application' : true);
      await expect(editor).toContainText('new input');
      await expect(editor).toContainText(`${action} original`);
      await page
        .getByText(/explicit rejection/)
        .first()
        .waitFor({ state: 'visible' });
      await expect(button).toBeEnabled();
      await editor.fill(`${action} retry`);
      await button.click();
      await expect.poll(() => pending?.body.text).toBe(`${action} retry`);
      await settle();
      await expect(button).toHaveAttribute('aria-busy', 'false');
      await expect(editor).toHaveText('');
      const projected = await page.evaluate(async () => {
        const { useSessionStore } = await import('/src/stores/sessionStore.ts');
        return useSessionStore.getState().currentMessages.map((message) => message.content);
      });
      if (action === 'Send') assert.deepEqual(projected, []);
      else assert.ok(projected.includes('Steer retry'));
    }
    await open();
    const editor = page.getByTestId('rich-text-composer');
    await editor.fill('attachment message');
    await page
      .locator('input[type="file"]')
      .first()
      .setInputFiles({ name: 'note.txt', mimeType: 'text/plain', buffer: Buffer.from('hello') });
    await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled();
    await expect.poll(() => typeof releaseUpload).toBe('function');
    releaseUpload();
    await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect
      .poll(() => pending?.body.parts?.some((part) => part.type === 'attachment'))
      .toBe(true);
    const attachment = pending.body.parts.find((part) => part.type === 'attachment');
    const uploaded = uploads;
    await settle(true);
    await expect(editor).toContainText('attachment message');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect
      .poll(() =>
        pending?.body.parts?.some((part) => part.attachmentId === attachment.attachmentId),
      )
      .toBe(true);
    assert.equal(uploads, uploaded);
    await settle();
    await open();
    await editor.fill('Steer attachment');
    releaseUpload = undefined;
    await page.locator('input[type="file"]').first().setInputFiles({ name: 'note.txt', mimeType: 'text/plain', buffer: Buffer.from('hello') });
    await expect(page.getByRole('button', { name: 'Steer', exact: true })).toBeDisabled();
    await expect.poll(() => typeof releaseUpload).toBe('function');
    releaseUpload();
    await expect(page.getByRole('button', { name: 'Steer', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: 'Steer', exact: true }).click();
    await expect(editor).toHaveText('');
    await expect(page.getByTestId('server-attachments')).toHaveCount(0);
    await expect.poll(() => pending?.body.text.includes('/api/attachments/upload_')).toBe(true);
    const steerUploads = uploads;
    await settle('application');
    await expect(editor).toHaveText('Steer attachment');
    await expect(page.getByTestId('server-attachments')).toContainText('note.txt');
    await page.getByRole('button', { name: 'Steer', exact: true }).click();
    await expect.poll(() => pending?.body.text.includes('/api/attachments/upload_')).toBe(true);
    assert.equal(uploads, steerUploads);
    await settle();
    for (const action of ['Send', 'Steer']) {
      await open();
      await editor.fill(`${action} belongs to s1`);
      await page.getByRole('button', { name: action, exact: true }).click();
      await expect.poll(() => pending?.body.text).toBe(`${action} belongs to s1`);
      await page.evaluate(async () => {
        const { useSessionStore } = await import('/src/stores/sessionStore.ts');
        useSessionStore.getState().setInputDraft('s2', 's2 newer draft');
        useSessionStore.setState({ currentSessionId: 's2', currentMessages: [] });
      });
      await expect(editor).toHaveText('s2 newer draft');
      await settle(true);
      await expect(editor).toHaveText('s2 newer draft');
      await expect
        .poll(() =>
          page.evaluate(async () => {
            const { useSessionStore } = await import('/src/stores/sessionStore.ts');
            return useSessionStore.getState().inputDrafts.s1;
          }),
        )
        .toContain(`${action} belongs to s1`);
    }
    await open();
    await editor.fill('ordinary pending draft');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect.poll(() => pending?.body.text).toBe('ordinary pending draft');
    await page.evaluate(async () => {
      const { useQueueStore } = await import('/src/stores/queueStore.ts');
      useQueueStore.setState({
        edits: {
          s1: {
            id: 'q-edit',
            text: 'queue edit ownership',
            originalText: 'queued',
            index: 0,
            createdAt: 1,
            revision: 1,
            kind: 'task',
          },
        },
      });
    });
    await expect(page.getByTestId('queue-rich-text-composer')).toHaveText('queue edit ownership');
    await settle(true);
    await expect(page.getByTestId('queue-rich-text-composer')).toHaveText('queue edit ownership');
    await open();
    await editor.fill('uncertain');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect.poll(() => pending?.body.text).toBe('uncertain');
    await pending.route.abort();
    resolveRequest();
    await page
      .getByText(/结果未确认/)
      .first()
      .waitFor({ state: 'visible' });
    await expect(editor).toHaveText('');
    await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled();
    const uncertainCount = evidence.transactions.length;
    await editor.fill('new draft after unknown');
    await editor.press('Enter');
    assert.equal(evidence.transactions.length, uncertainCount);
    await expect(editor).toHaveText('new draft after unknown');
    evidence.tests.push(
      `${mobile ? 'mobile touch' : 'desktop'} real ChatView/InputRow: Send+Steer immediate clear, spinner, no duplicate, IME, explicit rejection merges new draft, success semantics, attachment upload blocks and identity retained without reupload, Session switch and queue-edit ownership, ambiguous outcome blocks resend`,
    );
    await context.tracing.stop({
      path: path.join(output, `${mobile ? 'mobile' : 'desktop'}-trace.zip`),
    });
    await context.close();
  }
  assert.deepEqual(evidence.errors, []);
  assert.equal(
    evidence.requests.some((url) => /:(8767|8768)(\/|$)/.test(url)),
    false,
  );
  console.log(JSON.stringify(evidence.tests));
} catch (error) {
  evidence.failure = String(error.stack || error);
  evidence.body = await active?.locator('body').innerText();
  await active?.screenshot({ path: path.join(output, 'failure.png') });
  throw error;
} finally {
  await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(evidence, null, 2));
  await browser.close();
  await server.close();
}
