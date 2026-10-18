/* global process, console */
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';

/** Optional byte-for-byte CDN asset cache; never intercept application APIs. */
export async function useMonacoCdnCache(page) {
  const directory = process.env.PAN_E2E_MONACO_CACHE;
  if (!directory) return;
  const manifest = JSON.parse(await fs.readFile(path.join(directory, 'manifest.json'), 'utf8'));
  await page.route('https://cdn.jsdelivr.net/npm/monaco-editor@*/**', async (route) => {
    const entry = manifest[route.request().url()];
    if (!entry) return route.continue();
    const body = await fs.readFile(path.join(directory, entry.file));
    if (createHash('sha256').update(body).digest('hex') !== entry.sha256)
      throw new Error('CDN cache hash mismatch');
    await route.fulfill({ status: 200, body, contentType: entry.contentType });
  });
  console.log('Using original Monaco CDN bytes from', directory);
}
