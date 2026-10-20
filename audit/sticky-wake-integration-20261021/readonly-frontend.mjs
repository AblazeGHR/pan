// Execute from packages/web with: Get-Content ../../audit/.../readonly-frontend.mjs -Raw | node --input-type=module
import { readFileSync, writeFileSync, statSync, existsSync, realpathSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import ts from 'typescript';
import path from 'node:path';
import assert from 'node:assert/strict';
import { build } from 'vite';
import { startVitest } from 'vitest/node';

const root = path.resolve('../..');
const out = path.join(root, 'audit/sticky-wake-integration-20261021');
const ownedCache = path.join(out, 'owned-cache');
const shared = realpathSync('node_modules');
const state = () => {
  const cache = path.join(shared, '.vite/vitest/results.json');
  const temp = path.join(shared, '.vite-temp');
  return { resultFile: cache, sha256: existsSync(cache) ? createHash('sha256').update(readFileSync(cache)).digest('hex') : null,
    mtimeMs: existsSync(cache) ? statSync(cache).mtimeMs : null, configTempMtimeMs: existsSync(temp) ? statSync(temp).mtimeMs : null };
};
const before = state();
const previous = JSON.parse(readFileSync(path.join(out, 'commands.json'), 'utf8')).find(row => row.id === 'frontend').argv;
const files = previous.slice(previous.indexOf('run') + 1, previous.indexOf('--reporter=dot'));
const ctx = await startVitest('test', files, {
  run: true, watch: false, cache: { dir: path.join(ownedCache, 'vitest') },
  reporters: ['dot', 'json'], outputFile: path.join(out, 'frontend-readonly.json'),
}, { configLoader: 'runner', cacheDir: path.join(ownedCache, 'vite-test') });
assert(ctx, 'Vitest context missing');
// startVitest(run=true) already closes its Vite server before returning.
const testCacheDir = path.join(ownedCache, 'vitest');
assert(existsSync(path.join(testCacheDir, 'results.json')), 'Owned Vitest results cache missing');
assert.equal(process.exitCode || 0, 0, 'Vitest failure');
// Preserve the tracked config exactly, supplying the same __dirname that Vite's
// default bundle loader injects. Its temporary module lives only in owned dist.
// configFile:false avoids the shared node_modules/.vite-temp loader directory.
process.env.NODE_ENV = 'production';
delete process.env.VITEST;
delete process.env.TEST;
const configSource = readFileSync('vite.config.ts', 'utf8');
const moduleSource = ts.transpileModule(configSource.replace(/\b__dirname\b/g, JSON.stringify(process.cwd())), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const configModule = path.resolve('dist/.pan-readonly-vite.config.mjs');
writeFileSync(configModule, moduleSource);
const { default: makeConfig } = await import(pathToFileURL(configModule).href);
const production = await makeConfig({ command: 'build', mode: 'production', isSsrBuild: false, isPreview: false });
let productionProof;
await build({ ...production, configFile: false, mode: 'production', cacheDir: path.join(ownedCache, 'vite-build'),
  plugins: [...production.plugins, { name: 'verify-owned-production-build', configResolved(config) {
    assert(config.isProduction && config.mode === 'production');
    assert(path.resolve(config.cacheDir).startsWith(ownedCache));
    productionProof = { isProduction: config.isProduction, mode: config.mode, cacheDir: config.cacheDir,
      sourceConfigSha256: createHash('sha256').update(configSource).digest('hex') };
  } }],
});
const after = state();
assert.deepEqual(after, before, 'Shared cache changed during controlled validation');
const proof = { before, after, identical: true, productionProof, testCacheDir, buildCacheDir: path.join(ownedCache, 'vite-build'),
  note: 'Initial default CLI invocation wrote the shared Vitest result cache; this rerun uses owned cache directories and owned transpiled config loading, with no further shared cache writes.' };
writeFileSync(path.join(out, 'readonly-cache-proof.json'), JSON.stringify(proof, null, 2));
console.log('READONLY_CACHE_PROOF', JSON.stringify(proof));
