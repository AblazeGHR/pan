/* global process:readonly, fetch:readonly, console:readonly, Buffer:readonly */
/** Run the real file-link/Editor browser suite against an owned fixture server. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn, spawnSync } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';

const root = path.resolve(import.meta.dirname, '../../..');
const web = path.resolve(import.meta.dirname, '..');
const runtime = path.join(web, 'test-results', `editor-browser-${Date.now()}`);
const port = 8766;
const python = process.env.PAN_E2E_PYTHON || 'D:/project/Pan/.venv/Scripts/python.exe';
const env = {
  ...process.env,
  PAN_PORT: String(port),
  PAN_E2E_RUNTIME: runtime,
  PAN_E2E_BASE_URL: `http://127.0.0.1:${port}`,
};
const listenerPid = () => {
  if (process.platform !== 'win32') return null;
  const result = spawnSync('powershell.exe', [
    '-NoProfile', '-Command',
    `(Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)`,
  ], { windowsHide: true, encoding: 'utf8' });
  return Number(result.stdout.trim()) || null;
};

await fs.mkdir(runtime, { recursive: true });
await new Promise((resolve, reject) => {
  const socket = net.createServer();
  socket.once('error', reject);
  socket.listen(port, '127.0.0.1', () => socket.close(resolve));
});
const server = spawn(python, [path.join(import.meta.dirname, 'server.py')], {
  cwd: root,
  env,
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
const output = [];
let identity;
server.stdout.on('data', (chunk) => output.push(chunk));
server.stderr.on('data', (chunk) => output.push(chunk));

try {
  let ready = false;
  for (const deadline = Date.now() + 30_000; Date.now() < deadline;) {
    if (server.exitCode !== null) throw new Error(`fixture exited ${server.exitCode}`);
    try {
      identity = JSON.parse(await fs.readFile(path.join(runtime, 'server-identity.json'), 'utf8'));
      const response = await fetch(`${env.PAN_E2E_BASE_URL}/api/sessions?summary=1`);
      if (response.ok) {
        ready = true;
        break;
      }
    } catch { /* fixture is still starting */ }
    await sleep(100);
  }
  assert.ok(ready, 'editor fixture did not become ready');
  assert.equal(path.resolve(identity?.checkout || ''), root);
  assert.equal(identity?.port, port);
  assert.equal(identity?.pid, process.platform === 'win32' ? listenerPid() : server.pid);
  console.log('editor fixture', { checkout: root, port, pid: server.pid, runtime });

  const browser = spawn(process.execPath, [path.join(import.meta.dirname, 'run-browser.mjs')], {
    cwd: web,
    env,
    windowsHide: true,
    stdio: 'inherit',
  });
  const code = await new Promise((resolve, reject) => {
    browser.once('error', reject);
    browser.once('exit', resolve);
  });
  if (code !== 0) throw new Error(`browser suite exited ${code}`);
} finally {
  await fs.writeFile(path.join(runtime, 'server.log'), Buffer.concat(output));
  if (server.exitCode === null) {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], { windowsHide: true });
      if (identity?.pid === listenerPid()) {
        spawnSync('taskkill', ['/PID', String(identity.pid), '/T', '/F'], { windowsHide: true });
      }
    } else {
      server.kill('SIGTERM');
    }
  }
}
