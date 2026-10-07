import { fileURLToPath } from 'node:url';
// Original config expects Vite's bundle-time __dirname substitution. Native
// loading avoids writing bundled config caches into shared node_modules.
globalThis.__dirname = fileURLToPath(new URL('../../packages/web', import.meta.url));
const original = (await import('../../packages/web/vite.config.ts')).default;
export default (env) => ({
  ...original(env),
  root: fileURLToPath(new URL('../../packages/web/src', import.meta.url)),
  cacheDir: fileURLToPath(new URL('./runtime/vite-cache', import.meta.url)),
  server: { host: '127.0.0.1', port: 18766, strictPort: true,
    fs: {allow:[fileURLToPath(new URL('../../',import.meta.url))]},
    proxy: { '/api': 'http://127.0.0.1:18765', '/ws': {target:'ws://127.0.0.1:18765',ws:true} } },
});
