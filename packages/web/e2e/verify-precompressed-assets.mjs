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
console.log(`Verified ${checked} compressed production assets`);
