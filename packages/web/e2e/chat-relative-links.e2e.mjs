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

const returnAction = () => page.getByRole('button', { name: '返回引用位置', exact: true });

/** The return action belongs to the file operation row next to the file
 * buttons; the tab strip is a different row and must never carry it. */
async function assertReturnPlacement(stage) {
  const bar = page.getByTestId('editor-file-topbar');
  await bar.waitFor();
  assert.equal(
    await bar.getByRole('button', { name: '返回引用位置', exact: true }).count(),
    1,
    `${stage}: the return action must render inside the file operation bar`,
  );
  const box = await returnAction().boundingBox();
  const barBox = await bar.boundingBox();
  assert.ok(box && barBox, `${stage}: the return action and the bar must be laid out`);
  assert.ok(
    box.x >= barBox.x - 0.5 &&
      box.x + box.width <= barBox.x + barBox.width + 0.5 &&
      box.y >= barBox.y - 0.5 &&
      box.y + box.height <= barBox.y + barBox.height + 0.5,
    `${stage}: the return action must sit inside the file operation bar: ${JSON.stringify({ box, barBox })}`,
  );
  const centerY = (rect) => rect.y + rect.height / 2;
  const save = await bar.getByRole('button', { name: '保存文件' }).boundingBox();
  assert.ok(save, `${stage}: the save button must be laid out`);
  assert.ok(
    Math.abs(centerY(box) - centerY(save)) <= 2,
    `${stage}: the return action must share the file operation row`,
  );
  const previous = await returnAction().evaluate((element) => {
    let sibling = element.previousElementSibling;
    while (sibling && sibling.tagName !== 'BUTTON') sibling = sibling.previousElementSibling;
    if (!sibling) return null;
    return { right: sibling.getBoundingClientRect().right, label: sibling.getAttribute('aria-label') };
  });
  assert.ok(previous, `${stage}: the return action must sit inside the button group`);
  const operations = ['保存文件', '复制完整路径', '用系统默认方式打开', '在系统资源管理器中定位', '下载当前文件', '加入聊天'];
  assert.ok(
    operations.includes(previous.label),
    `${stage}: the return action must follow a file operation button, got ${previous.label}`,
  );
  assert.ok(
    box.x - previous.right >= -1 && box.x - previous.right <= 20,
    `${stage}: the return action must be adjacent to ${previous.label}, gap ${box.x - previous.right}`,
  );
  const tabStrip = page.locator('[data-testid="editor-tab"]').first().locator('xpath=..');
  assert.equal(
    await tabStrip.getByRole('button', { name: '返回引用位置' }).count(),
    0,
    `${stage}: the tab strip must not carry the return action`,
  );
  const stripBox = await tabStrip.boundingBox();
  assert.ok(stripBox, `${stage}: the tab strip must be laid out`);
  assert.ok(stripBox.y + stripBox.height <= box.y + 1, `${stage}: the action must be below the tab strip`);
  return box;
}

const saveAction = () => page.getByTestId('editor-file-topbar').getByRole('button', { name: '保存文件' });
const dirtySaveVisible = () => page.waitForFunction(() => {
  const button = document.querySelector('[data-testid="editor-file-topbar"] button[aria-label="保存文件"]');
  return button instanceof HTMLButtonElement && !button.disabled;
});

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
  // Return-to-source placement (desktop + narrow), dirty retention across the
  // async return boundary, and hiding for a file that has no source message.
  await page.setViewportSize({ width: 1440, height: 900 });
  await chat('A');
  await page.getByRole('link', { name: 'same file', exact: true }).click();
  await page.waitForURL(/\/react\/editor$/);
  await assertReturnPlacement('desktop');
  await page.screenshot({ path: path.join(runtime, 'return-placement-desktop.png') });
  await page.setViewportSize({ width: 420, height: 820 });
  await assertReturnPlacement('narrow');
  await page.screenshot({ path: path.join(runtime, 'return-placement-narrow.png') });
  await page.setViewportSize({ width: 1440, height: 900 });
  results.push('return placement desktop+narrow');

  await page.locator('button[title="Edit"]:visible').click();
  await page.locator('.monaco-editor').waitFor();
  const edited = `RETURN DIRTY ${Date.now()}`;
  await page.evaluate((value) => window.monaco.editor.getEditors()[0].setValue(value), edited);
  await dirtySaveVisible();
  assert.equal(await saveAction().isEnabled(), true, 'editing must mark the tab dirty');
  await assertReturnPlacement('dirty');
  await returnAction().click();
  await page.waitForURL(/\/react\/?$/, { waitUntil: 'commit' });
  await page.waitForFunction(() =>
    document.querySelector('.chat-message-jump-highlight')?.textContent?.includes('Chat cwd A'),
  );
  await page.screenshot({ path: path.join(runtime, 'return-source-highlight.png') });
  results.push('return lands on the source message');

  await page.getByRole('link', { name: 'Editor', exact: true }).click();
  await page.locator('.monaco-editor').waitFor();
  await dirtySaveVisible();
  assert.equal(
    await page.evaluate(() => window.monaco.editor.getEditors()[0].getValue()),
    edited,
    'the return trip must keep the dirty buffer',
  );
  await page.screenshot({ path: path.join(runtime, 'return-dirty-retained.png') });
  results.push('dirty buffer kept');

  await page.getByTestId('editor-root-cwd').getByText('slow.md', { exact: true }).click();
  await page.waitForFunction(() =>
    document.querySelector('[data-testid="editor-file-topbar"]')?.textContent?.includes('slow.md'),
  );
  assert.equal(await returnAction().count(), 0, 'a tree-opened file has no source message to return to');
  await page.screenshot({ path: path.join(runtime, 'return-absent-tree.png') });
  results.push('no source hidden');

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
