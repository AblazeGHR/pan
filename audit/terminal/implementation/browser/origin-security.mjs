// Actual Chromium-generated Origin/Fetch headers; no forged header overrides.
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { createServer } from 'node:http';
import path from 'node:path';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const directory = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(directory, '../../../..');
const label = process.argv[3];
if (!label || !/^[a-z0-9-]{1,60}$/.test(label)) throw new Error('new evidence label required');
const output = path.join(directory, label);
await mkdir(output);
const require = createRequire(path.join(repo, 'packages/web/package.json'));
const { chromium } = require('@playwright/test');
const reserve = net.createServer();
await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
const port = reserve.address().port;
await new Promise(resolve => reserve.close(resolve));
assert.notEqual(port, 8768);
const root = await mkdtemp(path.join(tmpdir(), 'pan-origin-security-'));
const child = spawn(process.argv[2] || 'python', [path.join(directory, 'harness.py'), '--root', root, '--port', String(port)],
  { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
let logs = '';
child.stdout.on('data', chunk => { logs += chunk; });
child.stderr.on('data', chunk => { logs += chunk; });
const exit = new Promise(resolve => child.once('exit', resolve));
const attacker = createServer((_request, response) => {
  response.writeHead(200, { 'Content-Type': 'text/html' });
  response.end('<!doctype html><title>Owned cross-origin test fixture</title>');
});
await new Promise(resolve => attacker.listen(0, '127.0.0.1', resolve));
const attackerPort = attacker.address().port;
assert.notEqual(attackerPort, 8768);
const origin = `http://127.0.0.1:${port}`;
const report = { passed: false, browser: 'chromium', cases: [] };
let browser;
try {
  for (let attempt = 0; attempt < 150; attempt++) {
    try { if ((await fetch(`${origin}/react/terminals`)).ok) break; } catch { /* starting */ }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto(`${origin}/react/terminals`);
  const created = await page.evaluate(async () => {
    const response = await fetch('/api/terminals', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    return { status: response.status, body: await response.json() };
  });
  assert.equal(created.status, 200);
  const id = created.body.result.terminal_id;
  const identity = [created.body.result.pid, created.body.result.process_created_at_filetime];
  const target = `${origin}/api/terminals/${id}/close`;
  const socketUrl = `ws://127.0.0.1:${port}/ws/terminal/${id}`;
  async function wireStatuses(observedPage) {
    const session = await observedPage.context().newCDPSession(observedPage);
    await session.send('Network.enable');
    const matching = new Set(), statuses = new Map();
    session.on('Network.requestWillBeSent', event => {
      if (event.request.url === target) matching.add(event.requestId);
    });
    // CORS can suppress Playwright's response event and the JS Response object.
    // ExtraInfo still witnesses the HTTP status without exposing any headers.
    session.on('Network.responseReceivedExtraInfo', event => statuses.set(event.requestId, event.statusCode));
    return () => [...matching].map(id => statuses.get(id)).filter(status => status !== undefined);
  }
  async function attack({ target, socketUrl }) {
    let rest;
    try {
      const response = await fetch(target, { method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: '{}' });
      rest = { status: response.status };
    } catch { rest = { unreadableByCrossOriginJS: true }; }
    const ws = await new Promise(resolve => {
      const socket = new WebSocket(socketUrl);
      let opened = false, frames = 0;
      socket.onopen = () => { opened = true; socket.close(); };
      socket.onmessage = () => { frames++; };
      socket.onclose = event => resolve({ opened, frames, code: event.code });
      socket.onerror = () => {};
      setTimeout(() => { socket.close(); resolve({ opened, frames, timeout: true }); }, 5000);
    });
    return { rest, ws };
  }
  for (const [name, url] of [['same-site-different-port', `http://127.0.0.1:${attackerPort}`],
                             ['cross-site-hostname', `http://localhost:${attackerPort}`]]) {
    const attackPage = await browser.newPage();
    const observedStatuses = await wireStatuses(attackPage);
    await attackPage.goto(url);
    const result = await attackPage.evaluate(attack, { target, socketUrl });
    const responses = observedStatuses();
    assert.equal(result.ws.opened, false, name);
    assert.equal(result.ws.frames, 0, name);
    assert(responses.includes(403), `${name}: wire statuses ${JSON.stringify(responses)}`);
    report.cases.push({ name, ...result, wireRestStatuses: responses });
    await attackPage.close();
  }
  // Sandboxed iframe generates literal Origin:null, not a spoofed request header.
  const opaquePage = await browser.newPage();
  const observedOpaqueStatuses = await wireStatuses(opaquePage);
  await opaquePage.goto(`http://127.0.0.1:${attackerPort}`);
  const opaque = await opaquePage.evaluate(({ source, target, socketUrl }) => new Promise(resolve => {
    const frame = document.createElement('iframe');
    frame.sandbox = 'allow-scripts';
    window.addEventListener('message', event => {
      if (event.source === frame.contentWindow && event.data?.ownedOriginWitness) resolve(event.data.result);
    });
    frame.srcdoc = `<script>(${source})(${JSON.stringify({ target, socketUrl })}).then(result=>parent.postMessage({ownedOriginWitness:true,result},'*'))<\/script>`;
    document.body.append(frame);
  }), { source: attack.toString(), target, socketUrl });
  assert.equal(opaque.ws.opened, false);
  assert.equal(opaque.ws.frames, 0);
  const opaqueStatuses = observedOpaqueStatuses();
  assert(opaqueStatuses.includes(403));
  report.cases.push({ name: 'opaque-sandbox-null-origin', ...opaque, wireRestStatuses: opaqueStatuses });
  await opaquePage.close();
  const after = await page.evaluate(async id => (await (await fetch(`/api/terminals/${id}`)).json()).result, id);
  assert.deepEqual([after.pid, after.process_created_at_filetime], identity);
  assert.equal(after.status, 'running');
  // Positive same-origin socket must receive hello and a real claim result.
  report.positive = await page.evaluate(({ id, socketUrl }) => new Promise((resolve, reject) => {
    const socket = new WebSocket(socketUrl);
    const types = [];
    socket.onerror = () => reject(new Error('positive connection rejected'));
    socket.onmessage = event => {
      const data = JSON.parse(event.data);
      types.push(data.type);
      if (data.type === 'hello') socket.send(JSON.stringify({ v: 1, type: 'command', terminal_id: id, op: 'claim' }));
      if (data.type === 'claim-result') { socket.close(); resolve({ types, generation: data.generation }); }
    };
    setTimeout(() => reject(new Error('positive handshake timeout')), 5000);
  }), { id, socketUrl });
  assert(report.positive.types.includes('hello') && report.positive.types.includes('claim-result'));
  let close;
  for (let attempt = 0; attempt < 80; attempt++) {
    close = await page.evaluate(async id => {
      const response = await fetch(`/api/terminals/${id}/close`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      return { status: response.status, body: await response.json() };
    }, id);
    if (close.status === 200) break;
    assert.equal(close.status, 409);
    await page.waitForTimeout(200);
  }
  assert.equal(close.status, 200);
  assert.equal(close.body.result.status, 'exited');
  report.sameRunnerIdentity = true;
  report.confirmedClose = true;
  report.passed = true;
} catch (error) {
  report.error = String(error);
  process.exitCode = 1;
} finally {
  await browser?.close();
  await new Promise(resolve => attacker.close(resolve));
  child.stdin.end('\n');
  report.harnessExit = await Promise.race([exit, new Promise(resolve => setTimeout(() => resolve('timeout'), 30000))]);
  if (report.harnessExit !== 0) { report.passed = false; report.retainedRoot = root; process.exitCode = 1; }
  report.logs = logs;
  await writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2));
  if (report.harnessExit === 0) await rm(root, { recursive: true });
  console.log(JSON.stringify({ passed: report.passed, harnessExit: report.harnessExit, error: report.error }));
}
