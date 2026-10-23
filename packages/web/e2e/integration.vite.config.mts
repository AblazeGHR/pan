import base from '../vite.config.ts';
import { defineConfig } from 'vite';
import path from 'node:path';
// Existing dependency files are read-only; cache belongs to this worktree.
export default defineConfig(env => ({ ...base(env), cacheDir: path.resolve('../../.cache/eta-report-vite') }));
