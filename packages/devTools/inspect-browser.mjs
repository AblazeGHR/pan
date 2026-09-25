/** Read the live Pan tab opened by Start-Pan-Browser-Trace. No dependencies. */
import fs from 'node:fs/promises';
import path from 'node:path';

const resultsRoot = path.resolve(import.meta.dirname, '../web/test-results');
const dirs = (await fs.readdir(resultsRoot, { withFileTypes: true }))
  .filter(entry => entry.isDirectory() && entry.name.startsWith('browser-trace-'))
  .map(entry => entry.name).sort().reverse();
let active;
for (const name of dirs) {
  try {
    const value = JSON.parse(await fs.readFile(path.join(resultsRoot, name, 'active.json'), 'utf8'));
    if (value.active) { active = value; break; }
  } catch { /* a run may not have reached browser_ready */ }
}
if (!active) throw new Error('No active browser trace. Start-Pan-Browser-Trace.cmd first.');
const targets = await (await fetch(`http://127.0.0.1:${active.port}/json/list`)).json();
const target = targets.find(item => item.id === active.targetId);
if (!target?.webSocketDebuggerUrl) throw new Error('The original Pan tab is no longer open.');
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true });
  socket.addEventListener('error', reject, { once: true });
});
let nextId = 1;
function send(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`${method} timed out`)), 10000);
    const handler = ({ data }) => {
      const value = JSON.parse(data);
      if (value.id !== id) return;
      socket.removeEventListener('message', handler);
      clearTimeout(timer);
      if (value.error) reject(new Error(value.error.message));
      else resolve(value.result);
    };
    socket.addEventListener('message', handler);
    socket.send(JSON.stringify({ id, method, params }));
  });
}
try {
  await send('Runtime.enable');
  const expression = `(() => {
    const state = window.__panSessionStore?.getState();
    const row = (m, index) => ({ index, role: m.role, messageId: m.messageId,
      nativeItemId: m.nativeItemId, queueItemIds: m.queueItemIds,
      contentLength: String(m.content ?? '').length });
    const messages = state?.currentMessages ?? [];
    return { url: location.href, storeAvailable: Boolean(state),
      sessionId: state?.currentSessionId,
      storeTotal: messages.length,
      storeTail: messages.slice(-40).map((m, i) => row(m, Math.max(0, messages.length - 40) + i)),
      domRows: Array.from(document.querySelectorAll('[data-index]')).map(e => ({
        index: Number(e.getAttribute('data-index')), textLength: e.textContent?.length ?? 0,
      })) };
  })()`;
  const result = await send('Runtime.evaluate', { expression, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  console.log(JSON.stringify({ traceDirectory: active.output, ...result.result.value }, null, 2));
  if (process.argv.includes('--screenshot')) {
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    const file = path.join(active.output, `inspect-${Date.now()}.png`);
    await fs.writeFile(file, Buffer.from(shot.data, 'base64'));
    console.log(`Screenshot: ${file}`);
  }
} finally {
  socket.close();
}
