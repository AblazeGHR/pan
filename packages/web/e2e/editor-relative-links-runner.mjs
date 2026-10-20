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
const port = await new Promise((resolve, reject) => {
  const socket = net.createServer();
  socket.once('error', reject);
  socket.listen(0, '127.0.0.1', () => {
    const port = socket.address().port;
    socket.close(() => resolve(port));
  });
});
const python = process.env.PAN_E2E_PYTHON || 'D:/project/Pan-main/.venv/Scripts/python.exe';
const env = {
  ...Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('PAN_'))),
  PAN_PORT: String(port),
  PAN_E2E_RUNTIME: runtime,
  PAN_E2E_BASE_URL: `http://127.0.0.1:${port}`,
  ...(process.env.PAN_E2E_MONACO_CACHE
    ? { PAN_E2E_MONACO_CACHE: process.env.PAN_E2E_MONACO_CACHE }
    : {}),
};
const listenerPid = () => {
  if (process.platform !== 'win32') return null;
  const result = spawnSync(
    'powershell.exe',
    [
      '-NoProfile',
      '-Command',
      `(Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)`,
    ],
    { windowsHide: true, encoding: 'utf8' },
  );
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
    } catch {
      /* fixture is still starting */
    }
    await sleep(100);
  }
  assert.ok(ready, 'editor fixture did not become ready');
  assert.equal(path.resolve(identity?.checkout || ''), root);
  assert.equal(identity?.port, port);
  assert.equal(identity?.pid, process.platform === 'win32' ? listenerPid() : server.pid);
  const fixture = path.join(runtime, 'session-workdir');
  await fs.mkdir(path.join(fixture, 'docs'), { recursive: true });
  await fs.mkdir(path.join(fixture, 'examples'), { recursive: true });
  await fs.mkdir(path.join(fixture, 'design'), { recursive: true });
  const targets = [
    'docs/DEVELOPING.md',
    'docs/CHECKLIST.md',
    'examples/demo.py',
    'design/Pan-Plugin-Capability.md',
    'docs/中文 空格.md',
    'CHECKLIST.md',
  ];
  for (const target of targets)
    await fs.writeFile(path.join(fixture, target), `TARGET ${target}\nsecond line\nthird line`);
  await fs.writeFile(
    path.join(fixture, 'docs/index.md'),
    [
      '[开发指南](DEVELOPING.md#已有-功能)',
      '[交付检查表](CHECKLIST.md)',
      '[可运行API示例](../examples/demo.py)',
      '[能力协议](../design/Pan-Plugin-Capability.md)',
      '[中文](./中文 空格.md)',
      `[绝对](${fixture.replaceAll('\\', '/')}/docs/CHECKLIST.md:2)`,
      '[缺失](missing.md)',
      '[外部](https://example.com/editor-relative)',
    ].join('\n'),
  );
  console.log('editor fixture', { checkout: root, port, pid: server.pid, runtime });

  await fs.mkdir(path.join(runtime, 'shared'), { recursive: true });
  await fs.writeFile(path.join(runtime, 'shared/guide.md'), 'CHAT TARGET shared');
  const chatSessions = [];
  for (const suffix of ['A', 'B']) {
    const cwd = path.join(runtime, `chat-${suffix.toLowerCase()}`);
    for (const dir of ['docs', '目录.v1 空格', 'slow-directory'])
      await fs.mkdir(path.join(cwd, dir), { recursive: true });
    await fs.writeFile(path.join(cwd, 'docs/同名 文件.md'), `CHAT TARGET ${suffix}`);
    await fs.writeFile(path.join(cwd, 'slow.md'), `SLOW TARGET ${suffix}`);
    await fs.writeFile(path.join(cwd, '目录.v1 空格/child.txt'), `CHILD ${suffix}`);
    const created = await fetch(`${env.PAN_E2E_BASE_URL}/api/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: `${suffix}-Relative-Chat`,
        workdir: cwd,
        adapter: 'codex',
        mcpServers: [],
      }),
    });
    const session = await created.json();
    assert.ok(session.id, JSON.stringify(session));
    const absolute = path.join(runtime, 'chat-a/docs/同名 文件.md').replaceAll('\\', '/');
    const seeded = await fetch(`${env.PAN_E2E_BASE_URL}/__e2e/append-history`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sessionId: session.id,
        messages: [
          {
            role: 'assistant',
            content: [
              `## Chat cwd ${suffix}`,
              '[same file](docs/同名%20文件.md#标题)',
              '[dot file](./docs/同名%20文件.md)',
              '[parent file](../shared/guide.md)',
              '[directory](./目录.v1%20空格)',
              '[parent directory](../shared/)',
              `[absolute file](${encodeURI(absolute)})`,
              '[slow directory](slow-directory/)',
              '[slow file](slow.md)',
              '[external](https://example.com/chat-relative)',
            ].join('\n\n'),
          },
        ],
      }),
    });
    assert.equal((await seeded.json()).ok, true);
    chatSessions.push({ id: session.id, cwd });
  }
  await fs.writeFile(path.join(runtime, 'chat-sessions.json'), JSON.stringify(chatSessions));

  for (const script of ['editor-relative-links.e2e.mjs', 'chat-relative-links.e2e.mjs']) {
    const browser = spawn(process.execPath, [path.join(import.meta.dirname, script)], {
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
  }
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
  await new Promise((resolve) =>
    server.exitCode !== null ? resolve() : server.once('exit', resolve),
  );
  assert.equal(listenerPid(), null, 'owned listener must be gone');
  console.log('cleanup verified', { port, runtime });
}
