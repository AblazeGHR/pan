import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { useMonacoCdnCache } from './monaco-cdn-cache.mjs';
const runtime = process.env.PAN_E2E_RUNTIME;
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await useMonacoCdnCache(page);
await page.context().tracing.start({ screenshots: true, snapshots: true });
page.on('pageerror', (error) => console.error('browser error', error.message));
const reads = [];
page.on('request', (r) => {
  if (r.url().includes('/api/fs/read?')) reads.push(new URL(r.url()).searchParams.get('path'));
});
async function clickSource(line, column = 8) {
  await page.waitForFunction((line) => {
    const editor = window.monaco?.editor?.getEditors()?.[0];
    return editor
      ?.getModel()
      ?.getAllDecorations()
      .some(
        (decoration) =>
          decoration.range.startLineNumber === line &&
          JSON.stringify(decoration.options).includes('link'),
      );
  }, line);
  const point = await page.evaluate(
    ({ line, column }) => {
      const editor = window.monaco.editor.getEditors().find((e) => e.getDomNode());
      editor.revealLine(line);
      const p = editor.getScrolledVisiblePosition({ lineNumber: line, column });
      const box = editor.getDomNode().getBoundingClientRect();
      return { x: box.x + p.left + 2, y: box.y + p.top + p.height / 2 };
    },
    { line, column },
  );
  await page.keyboard.down('Control');
  await page.mouse.move(point.x, point.y);
  await page.waitForTimeout(700);
  await page.mouse.click(point.x, point.y);
  console.log('clicked', line);
  await page.keyboard.up('Control');
}
async function edit() {
  const button = page.locator('button[title="Edit"]:visible');
  if (!(await page.locator('.monaco-editor').count())) {
    await button.waitFor();
    await button.click();
  }
  await page.locator('.monaco-editor').waitFor();
}
async function chatStream() {
  if (page.url().endsWith('/editor')) {
    await page.getByRole('link', { name: 'Chat', exact: true }).click();
  }
  await page.locator('[data-session-card-id]').filter({ hasText: 'Chat Stream' }).first().click();
}
async function position() {
  return page.evaluate(() => {
    const editor = window.monaco.editor.getEditors()[0];
    return { line: editor.getPosition()?.lineNumber, selection: editor.getSelection() };
  });
}
try {
  await page.goto(`${process.env.PAN_E2E_BASE_URL}/react/`);
  await chatStream();
  const lineLink = page.getByRole('link', { name: 'relative file', exact: true });
  const lineHref = await lineLink.getAttribute('href');
  assert.match(
    lineHref ?? '',
    /^\/api\/attachments\/editor\/att_[A-Za-z0-9]{32}\?session_id=[^#]+#L42$/,
    'Chat line link must resolve through the opaque editor attachment',
  );
  await lineLink.click();
  // No Edit click: a line link must reveal source even though .md defaults to preview.
  await page.locator('.monaco-editor').waitFor();
  await page.waitForFunction(
    () => window.monaco?.editor?.getEditors()?.[0]?.getPosition()?.lineNumber === 42,
  );
  const cold = await position();
  assert.equal(cold.selection?.startLineNumber, 42);
  assert.equal(cold.selection?.endLineNumber, 42);
  await page.screenshot({ path: path.join(runtime, 'chat-line-cold.png') });

  // Split is an existing source-capable preference; a second range link keeps it.
  await page.locator('button[title="Split"]:visible').click();
  await page.locator('.prose-kimi').waitFor();
  await chatStream();
  await page.getByRole('link', { name: 'relative range', exact: true }).click();
  await page.locator('.monaco-editor').waitFor();
  await page.waitForFunction(() => {
    const selection = window.monaco?.editor?.getEditors()?.[0]?.getSelection();
    return selection?.startLineNumber === 42 && selection.endLineNumber === 48;
  });
  await page.locator('.prose-kimi').waitFor();
  const split = await position();
  assert.equal(split.selection?.startLineNumber, 42);
  assert.equal(split.selection?.endLineNumber, 48);
  await page.screenshot({ path: path.join(runtime, 'chat-line-split-range.png') });
  await page.locator('button[title="Edit"]:visible').click();
  await page.locator('.monaco-editor').waitFor();
  await page.evaluate(() =>
    window.monaco.editor.getEditors()[0].setValue('[index](docs/index.md)'),
  );
  await page.waitForTimeout(2000);
  await clickSource(1, 12);
  await page.waitForFunction(() => document.body.textContent.includes('开发指南'));
  await edit();
  const source = path.join(runtime, 'session-workdir/docs/index.md').replaceAll('\\', '/');
  for (const [line, target] of [
    [1, 'docs/DEVELOPING.md'],
    [2, 'docs/CHECKLIST.md'],
    [3, 'examples/demo.py'],
    [4, 'design/Pan-Plugin-Capability.md'],
    [5, 'docs/中文 空格.md'],
    [6, 'docs/CHECKLIST.md'],
  ]) {
    if (line !== 1) {
      await page.getByText('index.md', { exact: true }).first().click();
      await page.locator('.monaco-editor').waitFor();
    }
    await page.waitForFunction(() => {
      const e = window.monaco?.editor?.getEditors()?.[0];
      return (
        e &&
        decodeURIComponent(e.getModel().uri.toString()).endsWith('index.md') &&
        e.getModel().getLineCount() === 8
      );
    });
    await page.waitForTimeout(1000);
    await clickSource(line, line === 3 ? 15 : 9);
    await page.waitForFunction(
      (t) => document.body.textContent.replaceAll('\u00a0', ' ').includes(`TARGET ${t}`),
      target,
    );
    assert.ok(
      reads.some((p) => p.replaceAll('\\', '/').endsWith(target)),
      target,
    );
    if (line === 6) {
      // `CHECKLIST.md:2` must reveal source and land on line 2 without Edit.
      await page.locator('.monaco-editor').waitFor();
      await page.waitForFunction(
        () => window.monaco?.editor?.getEditors()?.[0]?.getPosition()?.lineNumber === 2,
      );
    }
    await page.screenshot({ path: path.join(runtime, `click-${line}.png`) });
  }
  await page.getByText('index.md', { exact: true }).first().click();
  await page.locator('.monaco-editor').waitFor();
  await clickSource(7, 8);
  await page.getByText(/打开文件失败.*文件不存在/).waitFor();
  const popupPromise = page.waitForEvent('popup');
  await clickSource(8, 10);
  const popup = await popupPromise;
  await popup.waitForURL('https://example.com/editor-relative', { waitUntil: 'commit' });
  await popup.close();
  await page.locator('button[title="Preview"]:visible').click();
  await page.getByRole('link', { name: '交付检查表', exact: true }).click();
  await page.waitForFunction(() =>
    document.body.textContent.replaceAll('\u00a0', ' ').includes('TARGET docs/CHECKLIST.md'),
  );
  await page.screenshot({ path: path.join(runtime, 'preview-relative.png') });
  console.log(JSON.stringify({ passed: 11, source, reads }));
  await fs.writeFile(
    path.join(runtime, 'relative-links-result.json'),
    JSON.stringify({ passed: 11, reads }, null, 2),
  );
} finally {
  await fs.writeFile(path.join(runtime, 'read-paths.json'), JSON.stringify(reads));
  await page.screenshot({ path: path.join(runtime, 'final.png') });
  await page.context().tracing.stop({ path: path.join(runtime, 'relative-links-trace.zip') });
  await browser.close();
}
