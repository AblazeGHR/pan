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

// Cold-load contract: no emitted stylesheet may pull an external @import, and
// no cross-origin stylesheet in index.html may be render-blocking. Either one
// gates CSSOM - and therefore module execution and the first API read - on a
// third-party round trip, whatever host it is.
for (const name of names.filter((entry) => entry.endsWith('.css'))) {
  const css = await readFile(path.join(assets, name), 'utf8');
  assert.ok(
    !/@import\s*(?:url\()?['"]https?:/i.test(css),
    `${name} contains a render-blocking external @import`,
  );
}
const indexHtml = await readFile(path.join(webRoot, 'dist', 'index.html'), 'utf8');
// The <noscript> fallback may be a plain blocking stylesheet: it only applies
// when scripts are disabled and nothing else is waiting on it.
const indexHtmlWithScripts = indexHtml.replace(/<noscript>[\s\S]*?<\/noscript>/g, '');
for (const link of indexHtmlWithScripts.match(/<link\b[^>]*>/g) ?? []) {
  if (!/rel="stylesheet"/.test(link)) continue;
  if (!/href="https?:\/\//.test(link)) continue;
  // Async patterns only: a non-matching media query, or an inert stylesheet.
  // media="all"/absent still blocks rendering.
  assert.ok(
    /media="(print|not all)"/.test(link) || /disabled/.test(link),
    `cross-origin stylesheet is render-blocking: ${link}`,
  );
}

console.log(`Verified ${checked} compressed production assets`);
