/* global console */
/** Verify that served compressed assets contain the final Vite output. */
import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { brotliDecompressSync, gunzipSync } from 'node:zlib';

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const assets = path.join(webRoot, 'dist', 'assets');
const names = (await readdir(assets)).filter((name) => /\.(?:js|css)$/.test(name));
assert.ok(names.length > 0, 'production build has no JS/CSS assets');

let checked = 0;
for (const name of names) {
  const plain = await readFile(path.join(assets, name));
  if (name.endsWith('.js')) {
    assert.ok(!plain.includes('__VITE_PRELOAD__'), `${name} has an unresolved Vite preload marker`);
  }
  if (plain.length < 1024) continue;
  for (const [suffix, decompress] of [
    ['br', brotliDecompressSync],
    ['gz', gunzipSync],
  ]) {
    const compressed = await readFile(path.join(assets, `${name}.${suffix}`));
    const decoded = decompress(compressed);
    assert.deepEqual(decoded, plain, `${name}.${suffix} differs from the final asset`);
    checked += 1;
  }
}

assert.ok(checked > 0, 'production build has no compressed assets');

// Cold-load contract: no stylesheet may pull a render-blocking external
// @import, and the third-party webfont must be linked as non-blocking. A CSS
// @import gates CSSOM (hence module execution and the first API read) on that
// third-party origin.
for (const name of names.filter((entry) => entry.endsWith('.css'))) {
  const css = await readFile(path.join(assets, name), 'utf8');
  assert.ok(
    !/@import\s*(?:url\()?['"]https?:/i.test(css),
    `${name} contains a render-blocking external @import`,
  );
}
const indexHtml = await readFile(path.join(webRoot, 'dist', 'index.html'), 'utf8');
// The <noscript> fallback is allowed to be a plain blocking stylesheet: it only
// applies when scripts are disabled and nothing else is waiting on it.
const indexHtmlWithScripts = indexHtml.replace(/<noscript>[\s\S]*?<\/noscript>/g, '');
const fontLinks = [...indexHtmlWithScripts.matchAll(/<link\b[^>]*fonts\.googleapis\.com[^>]*>/g)]
  .map((match) => match[0])
  .filter((link) => /rel="stylesheet"/.test(link));
for (const link of fontLinks) {
  assert.ok(
    /media="print"/.test(link) || /rel="preload"/.test(link),
    `webfont link is render-blocking: ${link}`,
  );
}

// lucide-react resolves one module per icon. They must share one chunk,
// otherwise a dozen sub-3 KB requests occupy boot connections and delay the
// Session-list read on a constrained link.
const iconChunks = (await readdir(assets)).filter((name) => /^icon-vendor-.+\.js$/.test(name));
assert.equal(iconChunks.length, 1, 'icon modules are not grouped into one icon-vendor chunk');

console.log(`Verified ${checked} compressed production assets`);
