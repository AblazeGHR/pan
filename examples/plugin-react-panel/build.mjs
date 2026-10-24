/* Minimal real Vite + React build.
 *
 * Resolution order:
 *   1. this plugin's own node_modules (run `pnpm install` for a standalone copy);
 *   2. the Pan checkout's packages/web/node_modules (offline example in this repo).
 * No network access or shared dependency mutation is performed.
 */
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const root = path.dirname(fileURLToPath(import.meta.url));
const candidates = [path.join(root, 'node_modules'),
  path.resolve(root, '../../packages/web/node_modules')];

function packageDir(name) {
  for (const base of candidates) {
    try {
      return path.dirname(createRequire(path.join(base, 'anchor.cjs')).resolve(`${name}/package.json`));
    } catch {
      // Try the next installed dependency root.
    }
  }
  throw new Error(`${name} is not installed; run pnpm install in this plugin or keep packages/web installed`);
}

const vite = packageDir('vite');
const { build } = await import(pathToFileURL(path.join(vite, 'dist/node/index.js')));
await build({
  root,
  base: './',
  configFile: false,
  cacheDir: path.join(root, '.vite-cache'),
  resolve: { alias: { react: packageDir('react'), 'react-dom': packageDir('react-dom') } },
  esbuild: { jsx: 'transform' },
  build: { outDir: 'dist', emptyOutDir: true },
});
console.log('react panel built');
