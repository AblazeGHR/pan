/* global process, console, document */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { useMonacoCdnCache } from './monaco-cdn-cache.mjs';

const runtime = process.env.PAN_E2E_RUNTIME;
const base = process.env.PAN_E2E_BASE_URL;
const sessions = JSON.parse(await fs.readFile(path.join(runtime, 'chat-sessions.json'), 'utf8'));
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await useMonacoCdnCache(page);
const reads = [];
const results = [];
page.on('request', (request) => {
  if (request.url().includes('/api/fs/read?'))
    reads.push(Object.fromEntries(new URL(request.url()).searchParams));
});
await page.context().tracing.start({ screenshots: true, snapshots: true });

async function chat(suffix) {
  if (page.url().endsWith('/editor'))
    await page.getByRole('link', { name: 'Chat', exact: true }).click();
  await page
    .locator('[data-session-card-id]')
    .filter({ hasText: `${suffix}-Relative-Chat` })
    .first()
    .click();
  await page.getByRole('heading', { name: `Chat cwd ${suffix}`, exact: true }).waitFor();
}

async function file(suffix, label, marker, target) {
  await chat(suffix);
  await page.getByRole('link', { name: label, exact: true }).click();
  await page.waitForURL(/\/react\/editor$/);
  await page.waitForFunction(
    (value) => document.body.textContent.replaceAll('\u00a0', ' ').includes(value),
    marker,
  );
  const read = reads.find(
    (item) =>
      item.session_id === sessions[suffix === 'A' ? 0 : 1].id &&
      item.path.replaceAll('\\', '/') === target.replaceAll('\\', '/'),
  );
  assert.ok(read, `expected ${suffix}: ${target}`);
  results.push(`${suffix} ${label}`);
  await page.screenshot({
    path: path.join(runtime, `chat-${suffix}-${label.replaceAll(' ', '-')}.png`),
  });
}

async function delayedSwitch(kind) {
  await chat('A');
  let release;
  const arrived = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return (
      url.pathname === (kind === 'directory' ? '/api/directories' : '/api/fs/read') &&
      (url.searchParams.get('path') || '')
        .replaceAll('\\', '/')
        .endsWith(kind === 'directory' ? '/slow-directory' : '/slow.md')
    );
  });
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const pattern = kind === 'directory' ? '**/api/directories?*' : '**/api/fs/read?*';
  const handler = async (route) => {
    const target = new URL(route.request().url()).searchParams.get('path') || '';
    if (
      target.replaceAll('\\', '/').endsWith(kind === 'directory' ? '/slow-directory' : '/slow.md')
    ) {
      await gate;
      await route.continue();
    } else await route.continue();
  };
  await page.route(pattern, handler);
  await page.getByRole('link', { name: `slow ${kind}`, exact: true }).click();
  await arrived;
  await chat('B');
  const response = page.waitForResponse((r) => {
    const url = new URL(r.url());
    return (
      url.pathname === (kind === 'directory' ? '/api/directories' : '/api/fs/read') &&
      (url.searchParams.get('path') || '')
        .replaceAll('\\', '/')
        .endsWith(kind === 'directory' ? '/slow-directory' : '/slow.md')
    );
  });
  release();
  await (await response).finished();
  await page.waitForTimeout(200);
  await page.getByRole('heading', { name: 'Chat cwd B', exact: true }).waitFor();
  assert.ok(!page.url().endsWith('/editor'), 'old completion must not navigate the new Session');
  await page.unroute(pattern, handler);
  results.push(`switch during ${kind}`);
}

try {
  await page.goto(`${base}/react/`);
  for (const suffix of ['A', 'B']) {
    const cwd = sessions[suffix === 'A' ? 0 : 1].cwd;
    await file(suffix, 'same file', `CHAT TARGET ${suffix}`, path.join(cwd, 'docs/同名 文件.md'));
    await file(suffix, 'dot file', `CHAT TARGET ${suffix}`, path.join(cwd, 'docs/同名 文件.md'));
    await file(suffix, 'parent file', 'CHAT TARGET shared', path.join(runtime, 'shared/guide.md'));
    for (const [label, expected] of [
      ['directory', path.join(cwd, '目录.v1 空格')],
      ['parent directory', path.join(runtime, 'shared')],
    ]) {
      await chat(suffix);
      await page.getByRole('link', { name: label, exact: true }).click();
      await page.waitForURL(/\/react\/editor$/);
      const root = page
        .getByTestId('editor-root-temp')
        .filter({ has: page.getByTitle(expected.replaceAll('\\', '/'), { exact: true }) });
      await root.waitFor();
      await root
        .getByText(label === 'directory' ? 'child.txt' : 'guide.md', { exact: true })
        .waitFor();
      assert.ok(
        (await root.textContent()).includes(label === 'directory' ? 'child.txt' : 'guide.md'),
      );
      assert.ok(
        !reads.some((read) => read.path.replaceAll('\\', '/') === expected.replaceAll('\\', '/')),
        'directory must not be read as a file',
      );
      results.push(`${suffix} ${label}`);
      await page.screenshot({
        path: path.join(runtime, `chat-${suffix}-${label.replaceAll(' ', '-')}.png`),
      });
    }
  }
  await file(
    'B',
    'absolute file',
    'CHAT TARGET A',
    path.join(sessions[0].cwd, 'docs/同名 文件.md'),
  );
  await delayedSwitch('directory');
  await delayedSwitch('file');
  await file('B', 'same file', 'CHAT TARGET B', path.join(sessions[1].cwd, 'docs/同名 文件.md'));
  assert.equal(await page.getByTestId('editor-tab').filter({ hasText: 'slow.md' }).count(), 0);
  assert.equal(
    await page
      .getByTestId('editor-root-temp')
      .filter({
        has: page.getByTitle(path.join(sessions[0].cwd, 'slow-directory').replaceAll('\\', '/'), {
          exact: true,
        }),
      })
      .count(),
    0,
  );
  await chat('B');
  await Promise.all([
    page.waitForURL('https://example.com/chat-relative', { waitUntil: 'commit' }),
    page.getByRole('link', { name: 'external', exact: true }).click(),
  ]);
  results.push('external HTTP');
  console.log(JSON.stringify({ chatPassed: results.length, results }));
} finally {
  await fs.writeFile(
    path.join(runtime, 'chat-relative-results.json'),
    JSON.stringify({ results, reads }, null, 2),
  );
  await page.screenshot({ path: path.join(runtime, 'chat-final.png') });
  await page.context().tracing.stop({ path: path.join(runtime, 'chat-relative-trace.zip') });
  await browser.close();
}
