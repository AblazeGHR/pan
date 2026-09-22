/* Shared helpers for the isolated browser audit harness.
 *
 * Test-only.  Starts the E2E FastAPI server (e2e/server.py) on a port that is
 * never 8768 and writes all runtime state under an evidence directory.  The
 * browser talks to real HTTP routes and the real /ws broadcast path; the
 * "provider" is the deterministic injection routes only.
 */
import fs from 'node:fs/promises';
import fsSync from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { chromium } from '@playwright/test';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
export const webRoot = path.resolve(scriptDir, '..');
export const repoRoot = path.resolve(webRoot, '..', '..');

export function makeEvidenceDir(name) {
  const dir = path.join(repoRoot, 'evidence', name);
  return dir;
}

export function assertHarnessSafety(port) {
  if (port === 8768) throw new Error('audit harness must not use protected port 8768');
  if (port === 8767 || port === 8793) throw new Error(`audit harness must use its own port, got ${port}`);
  if (repoRoot === 'D:\\project\\Pan' || repoRoot.endsWith('Pan-main')) {
    throw new Error(`audit harness must not run from a protected checkout: ${repoRoot}`);
  }
}

export async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

export function createServerRunner({ port, runtime }) {
  const python = process.env.PAN_E2E_PYTHON || 'E:/software/miniforge/python.exe';
  const baseURL = `http://127.0.0.1:${port}`;
  let serverProcess = null;
  let logIndex = 0;
  const pids = [];

  async function waitForHealth(expected = true, timeoutMs = 40000) {
    const deadline = Date.now() + timeoutMs;
    let lastError = null;
    while (Date.now() < deadline) {
      try {
        const response = await fetch(`${baseURL}/api/health`, { signal: AbortSignal.timeout(1500) });
        if (expected && response.ok) return;
        lastError = `HTTP ${response.status}`;
      } catch (error) {
        lastError = error instanceof Error ? error.message : String(error);
        if (!expected) return;
      }
      await sleep(100);
    }
    throw new Error(`health wait expected=${expected} last=${lastError}`);
  }

  async function start() {
    await fs.mkdir(runtime, { recursive: true });
    const stamp = `${Date.now()}-${logIndex++}`;
    const stdoutPath = path.join(runtime, `server-${stamp}.stdout.log`);
    const stderrPath = path.join(runtime, `server-${stamp}.stderr.log`);
    const stdout = fsSync.openSync(stdoutPath, 'a');
    const stderr = fsSync.openSync(stderrPath, 'a');
    serverProcess = spawn(python, [path.join(webRoot, 'e2e', 'server.py')], {
      cwd: repoRoot,
      env: {
        ...process.env,
        PAN_PORT: String(port),
        PAN_E2E_RUNTIME: runtime,
        PAN_E2E_BASE_URL: baseURL,
      },
      stdio: ['ignore', stdout, stderr],
    });
    pids.push(serverProcess.pid);
    await waitForHealth(true);
    return { stdoutPath, stderrPath, pid: serverProcess.pid };
  }

  async function stop() {
    const current = serverProcess;
    serverProcess = null;
    if (!current || current.exitCode !== null) return;
    const exited = once(current, 'exit');
    current.kill();
    await Promise.race([exited, sleep(10000)]);
    await waitForHealth(false, 10000).catch(() => {});
  }

  return { baseURL, start, stop, pids, get pid() { return serverProcess?.pid; } };
}

export function createApi(baseURL) {
  async function requestJSON(route, options = {}) {
    const response = await fetch(`${baseURL}${route}`, {
      ...options,
      headers: { 'content-type': 'application/json', ...(options.headers || {}) },
      signal: AbortSignal.timeout(20000),
    });
    const text = await response.text();
    let body;
    try { body = text ? JSON.parse(text) : null; } catch { body = text; }
    if (response.status !== 200) throw new Error(`${options.method || 'GET'} ${route}: ${response.status} ${text}`);
    return body;
  }
  const post = (route, body) => requestJSON(route, { method: 'POST', body: JSON.stringify(body) });
  return {
    post,
    stream: (sessionId, event, extra = {}) => post('/__e2e/stream', { sessionId, event, ...extra }),
    broadcast: (event) => post('/__e2e/broadcast', { event }),
    appendHistory: (sessionId, messages) => post('/__e2e/append-history', { sessionId, messages }),
    historyRaw: (sessionId, before = 0, limit = 50) =>
      requestJSON(`/api/sessions/${encodeURIComponent(sessionId)}/history?before=${before}&limit=${limit}`),
    sessionsRaw: () => requestJSON('/api/sessions?summary=1'),
    health: () => requestJSON('/api/health'),
  };
}

export async function launchBrowser(viewport = { width: 1440, height: 900 }) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  // src/index.css starts with `@import url('https://fonts.googleapis.com/...')`.
  // That external stylesheet is render-blocking: with no egress the request
  // never settles and DOMContentLoaded never fires. Block it so the audit can
  // drive the real app offline (documented in the report). Layout then uses the
  // Tailwind fallback font stack.
  await context.route('**://fonts.googleapis.com/**', (route) => route.abort());
  await context.route('**://fonts.gstatic.com/**', (route) => route.abort());
  return { browser, context };
}

/**
 * Read the store-derived message sequence from the rendered virtual rows.
 *
 * `data-index` is the store/grouped index; the React fiber `key` is exactly the
 * TanStack item key (`message:${getMessageIdentity(item)}`), which is derived
 * from messageId/blockId/queueItemId/nativeItemId.  Together they reconstruct
 * the store's logical message sequence and identity for every rendered row —
 * without touching product source.
 */
export const READ_ROWS_FN = `() => {
  const container = document.querySelector('main');
  if (!container) return { rows: [], scroll: null };
  const nodes = [...container.querySelectorAll('[data-index]')];
  const rows = nodes.map((n) => {
    const fiberKey = Object.keys(n).find((k) => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
    const fiber = fiberKey ? n[fiberKey] : null;
    const key = fiber ? fiber.key : null;
    const rect = n.getBoundingClientRect();
    const inner = n.innerText || '';
    return {
      index: Number(n.getAttribute('data-index')),
      key,
      text: inner.slice(0, 200).replace(/\\n/g, ' | '),
      top: Math.round(rect.top * 100) / 100,
      height: Math.round(rect.height * 100) / 100,
    };
  }).sort((a, b) => a.index - b.index);
  const scroller = container.querySelector('.overflow-auto');
  const scroll = scroller ? {
    scrollTop: Math.round(scroller.scrollTop),
    scrollHeight: Math.round(scroller.scrollHeight),
    clientHeight: Math.round(scroller.clientHeight),
    distanceFromBottom: Math.round(Math.max(0, scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight)),
  } : null;
  return { rows, scroll };
}`;

export async function readRows(page) {
  return page.evaluate(new Function(`return (${READ_ROWS_FN})();`));
}

/** Full DOM text order for the chat scroller (all rendered rows, in document order). */
export async function readDomTexts(page) {
  return page.evaluate(() => {
    const container = document.querySelector('main');
    if (!container) return [];
    const nodes = [...container.querySelectorAll('[data-index]')];
    return nodes.map((n) => (n.innerText || '').replace(/\n/g, ' | ').slice(0, 200));
  });
}

export function writeJSON(dir, name, value) {
  return fs.writeFile(path.join(dir, name), JSON.stringify(value, null, 2), 'utf8');
}
