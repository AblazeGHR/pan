/**
 * Dependency-free Chrome DevTools recorder for a dedicated Pan browser.
 * Node 24+ supplies the WebSocket client. Browser data stays in the local
 * ignored test-results directory; no Pan service is started or stopped.
 */
import { spawn } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import readline from 'node:readline';
import { setTimeout as delay } from 'node:timers/promises';

const options = new Map();
for (let i = 2; i < process.argv.length; i += 2) {
  options.set(process.argv[i], process.argv[i + 1] ?? true);
}
const chromePath = options.get('--chrome');
const output = options.get('--out');
const includeContent = options.has('--full-content');
if (!chromePath || !output) throw new Error('--chrome and --out are required');
const sourceUrl = String(options.get('--url') || 'about:blank');
const panUrl = sourceUrl === 'about:blank' ? sourceUrl : (() => {
  const url = new URL(sourceUrl);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Pan URL must be http(s)');
  url.searchParams.set('panE2E', '1');
  return url.toString();
})();
await fs.mkdir(output, { recursive: true });
const profile = path.join(output, 'chrome-profile');
await fs.mkdir(profile, { recursive: true });
const stream = createWriteStream(path.join(output, 'trace.ndjson'), { flags: 'wx' });
let count = 0;
function emit(kind, data = {}) {
  count += 1;
  stream.write(JSON.stringify({ n: count, at: new Date().toISOString(), kind, ...data }) + '\n');
}
function digest(text) {
  return crypto.createHash('sha256').update(text).digest('hex').slice(0, 16);
}
function pickFrame(frame) {
  const raw = String(frame ?? '');
  let value;
  try { value = JSON.parse(raw); } catch { return { bytes: raw.length, sha256: digest(raw), malformed: true }; }
  // Some Pan WS paths wrap the JSON event in a payload string.
  if (typeof value?.payload === 'string') {
    try { value = JSON.parse(value.payload); } catch { /* retain envelope */ }
  }
  const event = value?.event ?? {};
  const content = event?.message?.content ?? event?.content;
  const blocks = Array.isArray(content) ? content.map((block, index) => ({
    index, type: block?.type, id: block?.id, name: block?.name,
    textLength: typeof block?.text === 'string' ? block.text.length : undefined,
    textSha256: typeof block?.text === 'string' ? digest(block.text) : undefined,
  })) : undefined;
  return {
    bytes: raw.length, sha256: digest(raw),
    type: value?.type, sessionId: value?.sessionId, workerId: value?.workerId,
    taskId: value?.taskId, taskSeq: value?.taskSeq,
    queueItemId: value?.queueItemId ?? event?.queueItemId,
    sourceSessionId: value?.sourceSessionId,
    generation: value?.generation, serverEpoch: value?.serverEpoch,
    eventEpoch: value?.eventEpoch, eventSeq: value?.eventSeq,
    deliverySeq: value?.deliverySeq, sourceCursorStart: value?.sourceCursorStart,
    sourceCursorEnd: value?.sourceCursorEnd,
    eventType: event?.type, role: event?.role, itemId: event?.item_id,
    completedItemId: event?.item?.id, completedItemType: event?.item?.type,
    blocks,
    turnId: event?.turn_id, final: event?.final, delta: event?.delta,
    streamTextLength: typeof event?.stream_text === 'string' ? event.stream_text.length : undefined,
    streamTextSha256: typeof event?.stream_text === 'string' ? digest(event.stream_text) : undefined,
    ...(includeContent ? { raw } : {}),
  };
}

class Cdp {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
  }
  async open() {
    await new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, { once: true });
      this.socket.addEventListener('error', reject, { once: true });
    });
    this.socket.addEventListener('message', ({ data }) => {
      let packet;
      try { packet = JSON.parse(data); } catch { return; }
      if (packet.id) {
        const pending = this.pending.get(packet.id);
        if (!pending) return;
        this.pending.delete(packet.id);
        clearTimeout(pending.timer);
        if (packet.error) pending.reject(new Error(`${pending.method}: ${packet.error.message}`));
        else pending.resolve(packet.result);
      } else {
        for (const listener of this.listeners.get(packet.method) ?? []) listener(packet.params ?? {});
      }
    });
    this.socket.addEventListener('close', () => {
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer);
        pending.reject(new Error('CDP socket closed'));
      }
      this.pending.clear();
    });
  }
  on(method, callback) {
    if (!this.listeners.has(method)) this.listeners.set(method, new Set());
    this.listeners.get(method).add(callback);
  }
  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} timed out`));
      }, 10000);
      this.pending.set(id, { resolve, reject, timer, method });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
  close() { this.socket.close(); }
}

async function waitForPort(child) {
  const file = path.join(profile, 'DevToolsActivePort');
  for (let i = 0; i < 200; i++) {
    if (child.exitCode !== null) throw new Error(`Chrome exited with ${child.exitCode}`);
    try {
      const [portText] = (await fs.readFile(file, 'utf8')).split(/\r?\n/);
      const port = Number(portText);
      if (port > 0) return port;
    } catch { /* Chrome has not opened the debugging endpoint yet */ }
    await delay(100);
  }
  throw new Error('Chrome did not create DevToolsActivePort within 20 seconds');
}
async function waitForPage(port) {
  for (let i = 0; i < 100; i++) {
    try {
      const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = pages.find(p => p.type === 'page' && p.webSocketDebuggerUrl);
      if (page) return page;
    } catch { /* browser endpoint still starting */ }
    await delay(100);
  }
  throw new Error('Chrome has no debuggable page');
}

// This runs only after ?panE2E=1 exposes the existing read-only store seam.
function installPageTrace(fullContent) {
  if (window.__panBrowserTraceInstalled || !window.__panSessionStore || !window.__panTraceEmit) return false;
  const store = window.__panSessionStore;
  const hash = (input) => {
    let h = 2166136261;
    for (let i = 0; i < input.length; i++) h = Math.imul(h ^ input.charCodeAt(i), 16777619);
    return (h >>> 0).toString(16).padStart(8, '0');
  };
  const body = (input) => {
    const text = String(input ?? '');
    // Hash bounded samples so diagnostics do not slow each streaming delta
    // when an earlier tool result or answer is very large.
    const sample = `${text.length}:${text.slice(0, 128)}:${text.slice(-128)}`;
    return { length: text.length, sampleHash: hash(sample), ...(fullContent ? { text } : {}) };
  };
  const row = (message, index) => ({
    index, role: message.role, messageId: message.messageId,
    nativeItemId: message.nativeItemId, queueItemIds: message.queueItemIds,
    taskId: message.taskId, turnId: message.turnId, content: body(message.content),
  });
  const rows = (messages) => {
    const list = Array.isArray(messages) ? messages : [];
    const start = Math.max(0, list.length - 120);
    return { total: list.length, start, rows: list.slice(start).map((message, offset) => row(message, start + offset)) };
  };
  const snapshot = (state) => ({
    sessionId: state.currentSessionId,
    current: rows(state.currentMessages),
    live: rows(state.liveStreamBuffers?.[state.currentSessionId]?.messages),
    runtime: rows(state.sessionTranscripts?.[state.currentSessionId]?.runtime),
    historyEpoch: state.sessions?.find(s => s.id === state.currentSessionId)?.historyEpoch,
  });
  const emit = (kind, value) => {
    try { window.__panTraceEmit(JSON.stringify({ kind, ...value })); } catch { /* debugger detached */ }
  };
  let lastSnapshot = snapshot(store.getState());
  store.subscribe((state, previous) => {
    if (state.currentSessionId === previous.currentSessionId
        && state.currentMessages === previous.currentMessages
        && state.liveStreamBuffers === previous.liveStreamBuffers
        && state.sessionTranscripts === previous.sessionTranscripts) return;
    const nextSnapshot = snapshot(state);
    const changedIds = new Set([
      ...Object.keys(previous.liveStreamBuffers ?? {}),
      ...Object.keys(state.liveStreamBuffers ?? {}),
      ...Object.keys(previous.sessionTranscripts ?? {}),
      ...Object.keys(state.sessionTranscripts ?? {}),
    ]);
    const changedSessions = [...changedIds]
      .filter(id => previous.liveStreamBuffers?.[id] !== state.liveStreamBuffers?.[id]
        || previous.sessionTranscripts?.[id] !== state.sessionTranscripts?.[id])
      .slice(0, 8)
      .map(id => ({
        sessionId: id,
        beforeLive: rows(previous.liveStreamBuffers?.[id]?.messages),
        afterLive: rows(state.liveStreamBuffers?.[id]?.messages),
        beforeRuntime: rows(previous.sessionTranscripts?.[id]?.runtime),
        afterRuntime: rows(state.sessionTranscripts?.[id]?.runtime),
      }));
    emit('store', { before: lastSnapshot, after: nextSnapshot, changedSessions });
    lastSnapshot = nextSnapshot;
  });
  let scheduled = false;
  const dom = () => {
    scheduled = false;
    const rendered = Array.from(document.querySelectorAll('[data-index]')).map(element => ({
      index: Number(element.getAttribute('data-index')),
      content: body(element.textContent),
    }));
    emit('dom', { sessionId: store.getState().currentSessionId, rows: rendered });
  };
  new MutationObserver(() => {
    if (!scheduled) { scheduled = true; requestAnimationFrame(dom); }
  }).observe(document.body, { childList: true, subtree: true, characterData: true });
  window.__panBrowserTraceInstalled = true;
  emit('attached', { snapshot: lastSnapshot, url: location.href });
  requestAnimationFrame(dom);
  return true;
}

const chrome = spawn(chromePath, [
  `--user-data-dir=${profile}`, '--remote-debugging-address=127.0.0.1',
  '--remote-debugging-port=0', '--no-first-run', '--no-default-browser-check',
  'about:blank',
], { windowsHide: false, stdio: 'ignore' });
emit('browser_start', { pid: chrome.pid, profile, panUrl, includeContent });
let page;
let browser;
let installTimer;
let input;
let closed = false;
let requestedStopReason;
let finishing;
function finish(reason) {
  if (finishing) return finishing;
  closed = true;
  finishing = (async () => {
    clearInterval(installTimer);
    emit('finished', { reason, browserPid: chrome.pid });
    await fs.writeFile(path.join(output, 'summary.json'), JSON.stringify({
      output, browserPid: chrome.pid, events: count, reason, includeContent,
      finishedAt: new Date().toISOString(),
    }, null, 2));
    await fs.writeFile(path.join(output, 'active.json'), JSON.stringify({ active: false, output, browserPid: chrome.pid }, null, 2));
    await new Promise(resolve => stream.end(resolve));
    page?.close();
    browser?.close();
    input?.close();
  })();
  return finishing;
}
chrome.on('exit', () => { void finish(requestedStopReason ?? 'browser_exited'); });
process.on('SIGINT', () => { void stop('ctrl_c'); });
async function stop(reason) {
  if (closed) return;
  requestedStopReason = reason;
  try { await browser?.send('Browser.close'); } catch { /* already closed */ }
  await finish(reason);
}

try {
  const port = await waitForPort(chrome);
  const target = await waitForPage(port);
  const browserInfo = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  browser = new Cdp(browserInfo.webSocketDebuggerUrl);
  page = new Cdp(target.webSocketDebuggerUrl);
  await Promise.all([browser.open(), page.open()]);
  await page.send('Page.enable');
  await page.send('Runtime.enable');
  await page.send('Network.enable');
  await page.send('Runtime.addBinding', { name: '__panTraceEmit' });
  page.on('Network.webSocketFrameReceived', params => emit('ws_received', {
    requestId: params.requestId, timestamp: params.timestamp,
    frame: pickFrame(params.response?.payloadData),
  }));
  page.on('Network.webSocketFrameSent', params => emit('ws_sent', {
    requestId: params.requestId, timestamp: params.timestamp,
    frame: pickFrame(params.response?.payloadData),
  }));
  page.on('Network.webSocketCreated', params => emit('ws_created', {
    requestId: params.requestId, url: params.url,
  }));
  page.on('Runtime.bindingCalled', params => {
    if (params.name !== '__panTraceEmit') return;
    try { const value = JSON.parse(params.payload); emit(value.kind, value); }
    catch { emit('page_trace_parse_error', { length: params.payload?.length }); }
  });
  page.on('Page.frameNavigated', params => emit('navigated', { url: params.frame?.url }));
  await fs.writeFile(path.join(output, 'active.json'), JSON.stringify({
    active: true, output, port, targetId: target.id, browserPid: chrome.pid,
    pageUrl: panUrl, startedAt: new Date().toISOString(),
  }, null, 2));
  emit('browser_ready', { port, targetId: target.id, browserPid: chrome.pid });
  installTimer = setInterval(() => {
    if (closed) return;
    void page.send('Runtime.evaluate', {
      expression: `(${installPageTrace.toString()})(${includeContent})`,
      returnByValue: true,
    }).catch(error => emit('attach_retry', { error: error.message }));
  }, 500);
  if (panUrl !== 'about:blank') await page.send('Page.navigate', { url: panUrl });
  console.log(`Chrome PID ${chrome.pid}; DevTools 127.0.0.1:${port}`);
  console.log(`Trace: ${path.join(output, 'trace.ndjson')}`);
  console.log('Press Enter to mark the current state; type q and Enter to finish.');
  input = readline.createInterface({ input: process.stdin, output: process.stdout });
  input.on('line', line => {
    if (line.trim().toLowerCase() === 'q') { void stop('user_finished'); return; }
    void (async () => {
      const screenshot = `marker-${Date.now()}.png`;
      const image = await page.send('Page.captureScreenshot', { format: 'png' });
      await fs.writeFile(path.join(output, screenshot), Buffer.from(image.data, 'base64'));
      emit('marker', { screenshot });
      console.log(`Marked: ${screenshot}`);
    })().catch(error => console.error(`Marker failed: ${error.message}`));
  });
} catch (error) {
  emit('setup_error', { message: error.message });
  console.error(error);
  try { await browser?.send('Browser.close'); } catch { /* no endpoint */ }
  if (chrome.exitCode === null) chrome.kill();
  await finish('setup_error');
  process.exitCode = 1;
}
