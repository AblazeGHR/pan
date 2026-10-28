// Fresh production build of the actual MarkdownRenderer with the repository's
// Vite config, served over owned HTTP. No Pan backend, provider or saved profile.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build, preview } from 'vite';
import { chromium } from '@playwright/test';

const web = fileURLToPath(new URL('../..', import.meta.url));
const evidence = path.resolve(web, '../../artifacts/chat-katex');
const outDir = path.join(web, 'test-results/chat-katex-production');
await fs.mkdir(evidence, { recursive: true });
// Also inspect the normal application's fresh `pnpm build` output, not just
// the component harness: every KaTeX font referenced by its CSS must exist.
const applicationAssets = path.join(web, 'dist/assets');
const fontFiles = new Set();
for (const file of await fs.readdir(applicationAssets)) {
  if (!file.endsWith('.css')) continue;
  const css = await fs.readFile(path.join(applicationAssets, file), 'utf8');
  for (const match of css.matchAll(/url\(([^)]*KaTeX[^)]*)\)/g)) {
    const url = match[1].replace(/["']/g, '');
    const asset = path.join(applicationAssets, path.basename(url));
    assert((await fs.stat(asset)).size > 0, `missing application font: ${url}`);
    fontFiles.add(path.basename(url));
  }
}
assert(fontFiles.size > 0, 'normal application build has no KaTeX font assets');
await build({
  configFile: path.join(web, 'vite.config.ts'), mode: 'production',
  root: path.join(web, 'e2e/chat-katex'),
  build: { outDir, emptyOutDir: true, rollupOptions: { input: path.join(web, 'e2e/chat-katex/index.html') } },
});
const server = await preview({
  configFile: false, root: web, base: '/react/', build: { outDir },
  preview: { host: '127.0.0.1', port: 0, strictPort: true },
});
let browser;
const result = { browser: '', port: null, applicationFontAssets: [...fontFiles].sort(), checks: [], pageErrors: [], requestFailures: [], resources: [] };
try {
  const address = server.httpServer.address();
  assert(address && typeof address === 'object');
  assert(![8767, 8768].includes(address.port));
  result.port = address.port;
  const origin = `http://127.0.0.1:${address.port}`;
  browser = await chromium.launch({ headless: true });
  result.browser = browser.version();
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin || url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws/')) {
      result.requestFailures.push(`unexpected request: ${url}`);
      return route.abort();
    }
    return route.continue();
  });
  page.on('pageerror', error => result.pageErrors.push(error.message));
  page.on('requestfailed', request => result.requestFailures.push(`${request.url()}: ${request.failure()?.errorText}`));
  page.on('response', response => {
    if (/\.(css|woff2?|ttf)(?:\?|$)/.test(response.url())) result.resources.push({ url: response.url(), status: response.status() });
  });
  await page.goto(`${origin}/react/`, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  assert.equal(await page.locator('[data-testid="message"] .katex').count(), 4);
  assert.equal(await page.locator('[data-testid="message"] math').count(), 4);
  assert.equal(await page.locator('[data-testid="message"] .katex-display').count(), 3);
  assert.equal(await page.locator('.katex-error').count(), 1);
  assert.equal(await page.locator('code').first().textContent(), '$literal$');
  assert.equal(await page.locator('code.language-latex').textContent(), '$x^2$\n');
  assert.equal(await page.locator('[data-testid="stream"] .katex').count(), 0);
  assert.equal(await page.locator('[data-testid="message"] a').count(), 2);
  assert.equal(await page.locator('table').count(), 1);
  const fontInfo = await page.locator('.katex .mord.mathnormal').first().evaluate(element => ({
    family: getComputedStyle(element).fontFamily,
    loaded: [...document.fonts].filter(font => font.family.startsWith('KaTeX') && font.status === 'loaded').map(font => font.family),
    mathmlPosition: getComputedStyle(document.querySelector('.katex-mathml')).position,
  }));
  assert.match(fontInfo.family, /KaTeX_Math/);
  assert(fontInfo.loaded.includes('KaTeX_Math'));
  assert.equal(fontInfo.mathmlPosition, 'absolute');
  assert.equal(await page.locator('body').evaluate(element => getComputedStyle(element).backgroundColor), 'rgb(13, 17, 23)');
  result.fonts = fontInfo;
  result.checks.push('actual inline/block/fenced-math KaTeX + MathML, invalid containment, code/link/attachment/table preservation, CSS and loaded fonts');
  await page.getByRole('button', { name: 'Complete stream' }).click();
  await page.locator('[data-testid="stream"] math').waitFor();
  assert.equal(await page.locator('[data-testid="stream"] annotation').textContent(), '\\frac{a}{b}');
  await page.getByRole('button', { name: 'Toggle search' }).click();
  assert.equal(await page.locator('mark.history-search-word-active').textContent(), 'needle');
  assert.equal(await page.locator('[data-testid="message"] math').count(), 4);
  result.checks.push('stream completion and active search with formulas');
  for (const [name, width] of [['desktop', 1440], ['narrow', 390]]) {
    await page.setViewportSize({ width, height: 1100 });
    const layout = await page.evaluate(() => ({
      width: innerWidth, documentWidth: document.documentElement.scrollWidth,
      formulas: [...document.querySelectorAll('.katex-display')].map(node => ({
        width: node.clientWidth, scrollWidth: node.scrollWidth, overflow: getComputedStyle(node).overflowX,
      })),
    }));
    assert(layout.documentWidth <= width, `${name}: page overflow ${JSON.stringify(layout)}`);
    assert(layout.formulas.every(formula => formula.width > 0 && formula.overflow === 'auto'));
    if (name === 'narrow') {
      assert(layout.formulas.some(formula => formula.scrollWidth > formula.width));
      const wide = page.locator('.katex-display').nth(1);
      assert(await wide.evaluate(node => { node.scrollLeft = 100; return node.scrollLeft > 0; }));
      await wide.evaluate(node => { node.scrollLeft = 0; });
    }
    result[name] = layout;
    await page.screenshot({ path: path.join(evidence, `${name}.png`), fullPage: true });
    result.checks.push(`${name} readable layout and screenshot; wide display math scrolls locally`);
  }
  assert(result.resources.some(resource => /\.css$/.test(resource.url) && resource.status === 200));
  assert(result.resources.some(resource => /KaTeX.*\.woff2$/.test(resource.url) && resource.status === 200));
  assert(result.resources.every(resource => resource.status === 200));
  assert.deepEqual(result.pageErrors, []);
  assert.deepEqual(result.requestFailures, []);
  result.ok = true;
} catch (error) {
  result.ok = false;
  result.error = error.stack;
  throw error;
} finally {
  await fs.writeFile(path.join(evidence, 'browser.json'), JSON.stringify(result, null, 2) + '\n');
  await browser?.close();
  await new Promise((resolve, reject) => server.httpServer.close(error => error ? reject(error) : resolve()));
}
console.log(JSON.stringify(result, null, 2));
