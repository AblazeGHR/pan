/* global process, fetch, setTimeout, window, document, performance, innerHeight, requestAnimationFrame, console */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn, spawnSync } from 'node:child_process';
import { chromium } from '@playwright/test';
const root = path.resolve(import.meta.dirname, '../../..');
const port = 8799,
  base = `http://127.0.0.1:${port}`;
await new Promise((res, rej) => {
  const s = net.createServer();
  s.on('error', rej);
  s.listen(port, '127.0.0.1', () => s.close(res));
});
const output = path.resolve(
  import.meta.dirname,
  '../test-results',
  'session-click-latency-' + Date.now(),
);
await fs.mkdir(output, { recursive: true });
const runtime = path.join(output, 'runtime');
const server = spawn(
  process.env.PAN_E2E_PYTHON || 'D:/project/Pan-main/.venv/Scripts/python.exe',
  [path.join(import.meta.dirname, 'session-click-latency-server.py')],
  {
    cwd: root,
    windowsHide: true,
    env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime },
    stdio: ['ignore', 'pipe', 'pipe'],
  },
);
let logs = '';
server.stdout.on('data', (d) => (logs += d));
server.stderr.on('data', (d) => (logs += d));
let browser;
let ownedPid = server.pid;
const evidence = {
  samples: [],
  errors: [],
  note: 'Application cache cleared; OS file cache not cleared; production bundle and real isolated API.',
};
try {
  for (let i = 0; ; i++) {
    try {
      if ((await fetch(base + '/api/sessions?summary=1')).ok) break;
    } catch {
      /* The owned fixture may still be starting. */
    }
    if (i > 300 || server.exitCode !== null) throw Error(logs);
    await new Promise((r) => setTimeout(r, 200));
  }
  browser = await chromium.launch({ headless: true });
  evidence.chromium = browser.version();
  const identity = JSON.parse(
    await fs.readFile(path.join(runtime, 'server-identity.json'), 'utf8'),
  );
  ownedPid = identity.pid;
  if (ownedPid !== server.pid) {
    const parent = spawnSync(
      'powershell.exe',
      [
        '-NoProfile',
        '-Command',
        `(Get-CimInstance Win32_Process -Filter 'ProcessId = ${ownedPid}').ParentProcessId`,
      ],
      { encoding: 'utf8', windowsHide: true },
    );
    assert.equal(Number(parent.stdout.trim()), server.pid, 'venv child belongs to our launcher');
  }
  assert.equal(path.resolve(identity.checkout), root);
  evidence.identity = identity;
  const ids = JSON.parse(await fs.readFile(path.join(runtime, 'ids.json'), 'utf8'));
  for (const name of Object.keys(ids))
    for (let round = 0; round < 5; round++) {
      for (const baseline of round % 2 ? [false, true] : [true, false]) {
        await fetch(base + '/__e2e/profile/reset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ baseline }),
        });
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
        const page = await context.newPage();
        page.on('pageerror', (e) => evidence.errors.push(e.message));
        await page.goto(base + '/react/?panE2E=1');
        const card = page.locator(`[data-session-card-id="${ids[name]}"]`);
        await card.waitFor();
        await page.evaluate((id) => {
          window.__measure = { id };
          const store = window.__panSessionStore;
          document.addEventListener(
            'click',
            (e) => {
              if (!e.target.closest(`[data-session-card-id="${id}"]`)) return;
              window.__measure.click = performance.now();
              const unsub = store.subscribe((s) => {
                if (s.currentSessionId === id && !s.initialLoading && s.currentMessages.length) {
                  window.__measure.storeReady = performance.now();
                  unsub();
                }
              });
              let frames = 0;
              const tick = () => {
                const s = store.getState(),
                  rows = [...document.querySelectorAll('[data-scroll-anchor-key]')];
                const visible = rows.some((el) => {
                  const r = el.getBoundingClientRect();
                  return r.height > 0 && r.bottom > 0 && r.top < innerHeight;
                });
                if (
                  s.currentSessionId === id &&
                  !s.initialLoading &&
                  s.currentMessages.length &&
                  visible
                ) {
                  if (++frames >= 2) {
                    window.__measure.painted = performance.now();
                    return;
                  }
                } else frames = 0;
                requestAnimationFrame(tick);
              };
              requestAnimationFrame(tick);
            },
            { capture: true, once: true },
          );
        }, ids[name]);
        await card.click();
        await page.waitForFunction(() => window.__measure.painted, { timeout: 30000 });
        const sample = await page.evaluate(() => {
          const m = window.__measure;
          return {
            clickToStoreMs: m.storeReady - m.click,
            clickToVisibleMs: m.painted - m.click,
            history: performance
              .getEntriesByType('resource')
              .filter((e) => e.name.includes('/history?'))
              .map((e) => ({
                startMs: e.startTime - m.click,
                durationMs: e.duration,
                responseEndMs: e.responseEnd - m.click,
                bytes: e.decodedBodySize,
              })),
          };
        });
        sample.name = name;
        sample.round = round;
        sample.variant = baseline ? 'baseline' : 'fixed';
        sample.spans = await (await fetch(base + '/__e2e/profile/spans')).json();
        assert.equal(sample.history.length, 1, 'one foreground history request per click');
        evidence.samples.push(sample);
        console.log(JSON.stringify(sample));
        await context.close();
      }
    }
  assert.equal(evidence.errors.length, 0, 'no browser runtime errors');
} finally {
  await browser?.close();
  if (ownedPid !== server.pid) {
    spawnSync(
      'powershell.exe',
      [
        '-NoProfile',
        '-Command',
        `Stop-Process -Id ${ownedPid} -Force -ErrorAction SilentlyContinue`,
      ],
      { windowsHide: true },
    );
  }
  server.kill();
  await new Promise((r) => {
    if (server.exitCode !== null) r();
    else {
      server.once('exit', r);
      setTimeout(r, 5000);
    }
  });
  await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(evidence, null, 2));
  await fs.writeFile(path.join(output, 'server.log'), logs);
  await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', reject);
    probe.listen(port, '127.0.0.1', () => probe.close(resolve));
  });
  console.log(`Evidence: ${output}`);
}
