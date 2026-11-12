#!/usr/bin/env node
/**
 * Run Vitest and never hide its exit code.
 *
 * Piping `vitest` into `grep`/`sort`/`head` makes the shell report the status of
 * the last pipeline stage, so a failing suite looks like exit 0. This wrapper
 * spawns Vitest with inherited stdio (no pipe of its own) and exits with the
 * child's status, or re-raises its terminating signal.
 *
 * Usage: node scripts/run-vitest.mjs [vitest args]   (defaults to one `run`)
 */
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const cli = fileURLToPath(new URL('../node_modules/vitest/vitest.mjs', import.meta.url));
const forwarded = process.argv.slice(2);
const argv = forwarded.length ? forwarded : ['run'];

const child = spawn(process.execPath, [cli, ...argv], { stdio: 'inherit' });
child.on('error', (error) => {
  console.error(`run-vitest: ${error.message}`);
  process.exit(1);
});
child.on('exit', (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code === null ? 1 : code);
});
