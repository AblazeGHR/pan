/* global window, URL, process, fetch, console, setTimeout, clearTimeout */
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { mkdirSync, writeFileSync } from 'node:fs';
import { chromium, expect } from '@playwright/test';

// Isolated Chromium verification of the real RenameSessionModal (production
// component + production store, wiring replicated from Sidebar.tsx:808-811).
// Usage: node e2e/session-rename.e2e.mjs [baseline|candidate]
//   baseline  = run against the unfixed modal (the draft-overwrite and
//               submit-guard scenarios must FAIL, proving the bug reproduces)
//   candidate = run against the fixed modal (every scenario must pass)
// Only the dynamic-port vite dev server and the isolated headless Chromium are
// contacted; the route guard asserts ports 8767/8768 are never requested.
// Every scenario starts from a fresh page + fresh mock backend so a failure
// cannot cascade into the next scenario's preconditions.
const tag = process.argv[2] === 'baseline' ? 'baseline' : 'candidate';

const cwd = fileURLToPath(new URL('..', import.meta.url));
const port = await new Promise((resolve) => {
  const server = net.createServer();
  server.listen(0, '127.0.0.1', () => {
    const allocated = server.address().port;
    server.close(() => resolve(allocated));
  });
});
const base = `http://127.0.0.1:${port}`;
const output = fileURLToPath(new URL('../evidence/session-rename/', import.meta.url));
mkdirSync(output, { recursive: true });
const server = spawn(
  process.execPath,
  ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
  { cwd, windowsHide: true, stdio: 'ignore' },
);

const seedSessions = () => [
  { id: 'a', name: 'Session A', alwaysThinkingEnabled: false, effort: '', history: [] },
  { id: 'b', name: 'Session B', alwaysThinkingEnabled: false, effort: '', history: [] },
];

// Mock backend mirror (Node side). Seeded identically to the harness store.
let mockSessions = seedSessions();
let holdSessionsGet = null; // when set, GET /api/sessions waits on this promise
let sessionsGetOverride = null; // when set, GET /api/sessions replies with this body
let holdRename = null; // when set, POST .../rename waits on this promise
let failNextRename = false;

const evidence = { tag, port, results: [], requests: [], errors: [] };
let browser;

// Watchdog: a wiring mistake must fail fast instead of burning a slot in the
// background. It flushes whatever evidence exists before exiting non-zero.
const watchdog = setTimeout(() => {
  writeFileSync(`${output}/results-${tag}.json`, JSON.stringify(evidence, null, 2));
  console.error(`watchdog: ${tag} run exceeded its time budget`);
  process.exit(1);
}, 300000);

function record(name, ok, detail) {
  evidence.results.push({ name, ok, detail: detail ?? null });
  console.log(`${ok ? 'PASS' : 'FAIL'} [${tag}] ${name}${detail ? ` — ${detail}` : ''}`);
}

try {
  for (let attempt = 0; ; attempt++) {
    if (attempt > 80 || server.exitCode !== null) throw new Error('Vite startup failed');
    try {
      if ((await fetch(`${base}/session-rename-harness.html`)).ok) break;
    } catch {
      /* startup */
    }
    await delay(100);
  }
  browser = await chromium.launch({ headless: true });

  for (const width of [1280, 390]) {
    const context = await browser.newContext({
      viewport: { width, height: 844 },
      isMobile: width < 768,
      hasTouch: width < 768,
    });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.on('pageerror', (error) => evidence.errors.push(`${width}: ${error.message}`));
    await page.route('**/*', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      // Kept outside the try: a protected-port request must always fail loudly.
      assert.ok(![8767, 8768].includes(Number(url.port)), 'Protected service request');
      try {
        if (url.origin !== base)
          return await route.fulfill({ status: 200, contentType: 'text/css', body: '' });
        if (!url.pathname.startsWith('/api/')) return await route.continue();
        const reply = (data, status = 200) =>
          route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
        if (url.pathname === '/api/sessions') {
          evidence.requests.push({ width, path: url.pathname });
          if (holdSessionsGet) await holdSessionsGet;
          return await reply(sessionsGetOverride ?? { sessions: mockSessions });
        }
        const renameMatch = url.pathname.match(/^\/api\/sessions\/([^/]+)\/rename$/);
        if (renameMatch) {
          const body = request.postDataJSON();
          evidence.requests.push({ width, path: url.pathname, id: renameMatch[1], body });
          if (holdRename) await holdRename;
          if (failNextRename) {
            failNextRename = false;
            return await reply({ error: 'Rename rejected by server' }, 409);
          }
          mockSessions = mockSessions.map((s) =>
            s.id === renameMatch[1] ? { ...s, name: body.name } : s);
          return await reply({ sessionId: renameMatch[1], name: body.name, status: 'renamed' });
        }
        return await reply({ ok: true });
      } catch {
        // The page reloaded (or the request aborted) while a gate was open;
        // there is nothing left to fulfil.
        return undefined;
      }
    });

    await page.goto(`${base}/session-rename-harness.html`);
    await page.getByRole('button', { name: 'Rename A', exact: true }).waitFor();

    const dialogState = () => page.evaluate(() => window.dialogState());
    const storeState = () => page.evaluate(() => window.storeState());
    const openRename = async (label) => {
      const button = page.getByRole('button', { name: label, exact: true });
      if (width < 768) await button.tap();
      else await button.click();
      await page.locator('[role="dialog"]').waitFor();
    };
    const renamePosts = () =>
      evidence.requests.filter((r) => r.width === width && r.path.endsWith('/rename'));
    // Fresh page + fresh mock backend for every scenario.
    const resetPage = async () => {
      mockSessions = seedSessions();
      failNextRename = false;
      holdRename = null;
      holdSessionsGet = null;
      sessionsGetOverride = null;
      await page.reload();
      await page.getByRole('button', { name: 'Rename A', exact: true }).waitFor();
    };
    // Every scenario runs even if an earlier one failed; the run exits
    // non-zero at the end when any scenario failed.
    const run = async (name, fn) => {
      try {
        const detail = await fn();
        record(`${width}/${name}`, true, detail);
      } catch (error) {
        record(`${width}/${name}`, false, error.message);
      }
    };

    // 1. Open: focus + select-all of the current name. Selection is polled so
    //    a late animation frame is not mistaken for a missing selection.
    await run('open-focus-select', async () => {
      await resetPage();
      await openRename('Rename A');
      await expect.poll(async () => {
        const state = await dialogState();
        return `${state.dialogOpen}/${state.activeIsInput}/${state.selectionStart}/${state.selectionEnd}/${state.value}`;
      }).toBe(`true/true/0/${'Session A'.length}/Session A`);
      await page.screenshot({ path: `${output}/${tag}-${width}-open.png`, animations: 'disabled' });
      return 'focused, select-all 0..9';
    });

    // 2. THE BUG: a store refresh replaces the session object identity while
    //    the user types. The draft must survive; the caret must not be reset.
    await run('draft-survives-refresh', async () => {
      await resetPage();
      await openRename('Rename A');
      await page.keyboard.type('Session A v2');
      await expect.poll(async () => (await dialogState()).value).toBe('Session A v2');
      await page.evaluate(() =>
        window.simulateRefresh('a', { lastMessage: 'stream preview tick' }));
      const store = await storeState();
      assert.equal(store[0].lastMessage, 'stream preview tick', 'refresh did not land');
      await expect.poll(async () => (await dialogState()).value)
        .toBe('Session A v2'); // baseline reverts to 'Session A' and never recovers
      const state = await dialogState();
      assert.equal(state.selectionStart, 'Session A v2'.length, 'caret was reset by the refresh');
      assert.equal(state.selectionEnd, 'Session A v2'.length, 'selection was reset by the refresh');
      await page.screenshot({
        path: `${output}/${tag}-${width}-draft-survives-refresh.png`,
        animations: 'disabled',
      });
      return 'draft + caret preserved across identity-changing refresh';
    });

    // 3. Enter submits the draft; reopening the same session reseeds from the
    //    store (repeated entry into the same session).
    await run('submit-enter-and-reopen', async () => {
      await resetPage();
      const before = renamePosts().length;
      await openRename('Rename A');
      await page.keyboard.type('Session A v2');
      await page.keyboard.press('Enter');
      await expect.poll(async () => (await dialogState()).dialogOpen).toBe(false);
      await expect.poll(async () => (await storeState())[0].name).toBe('Session A v2');
      const posts = renamePosts();
      assert.equal(posts.length, before + 1, 'expected exactly one rename POST');
      assert.equal(posts.at(-1).body.name, 'Session A v2');
      await page.getByRole('status').filter({ hasText: 'Session renamed' }).waitFor();
      await openRename('Rename A');
      await expect.poll(async () => {
        const state = await dialogState();
        return `${state.value}/${state.selectionStart}/${state.selectionEnd}/${state.activeIsInput}`;
      }).toBe(`Session A v2/0/${'Session A v2'.length}/true`);
      return 'draft submitted; reopen reseeded + select-all';
    });

    // 4. Escape cancels; reopening reseeds from the store, not the old draft.
    await run('cancel-and-reseed', async () => {
      await resetPage();
      const before = renamePosts().length;
      await openRename('Rename A');
      await page.keyboard.type('Session A v2');
      await page.keyboard.press('Escape');
      await expect.poll(async () => (await dialogState()).dialogOpen).toBe(false);
      await openRename('Rename A');
      await expect.poll(async () => (await dialogState()).value).toBe('Session A');
      assert.equal(renamePosts().length, before, 'cancel must not submit');
      return 'escape closed without submit; reopen reseeded';
    });

    // 5. Fast typing, deleting everything, then cancel; reopen must reseed.
    await run('fast-type-delete-cancel', async () => {
      await resetPage();
      await openRename('Rename A');
      await page.keyboard.type('XYZ');
      await expect.poll(async () => (await dialogState()).value).toBe('XYZ');
      for (let i = 0; i < 3; i += 1) await page.keyboard.press('Backspace');
      await expect.poll(async () => (await dialogState()).value).toBe('');
      await page.keyboard.press('Escape');
      await expect.poll(async () => (await dialogState()).dialogOpen).toBe(false);
      await openRename('Rename A');
      await expect.poll(async () => (await dialogState()).value).toBe('Session A');
      return 'empty draft discarded on cancel; reopen reseeded';
    });

    // 6. Alternating sessions (close, then open the other): no draft may leak
    //    from one session into the other.
    await run('alternating-sessions-no-leak', async () => {
      await resetPage();
      await openRename('Rename A');
      await page.keyboard.type('LEAK');
      await expect.poll(async () => (await dialogState()).value).toBe('LEAK');
      await page.keyboard.press('Escape');
      await openRename('Rename B');
      await expect.poll(async () => {
        const state = await dialogState();
        return `${state.value}/${state.selectionStart}/${state.selectionEnd}`;
      }).toBe(`Session B/0/${'Session B'.length}`);
      await page.keyboard.press('Escape');
      await openRename('Rename A');
      await expect.poll(async () => (await dialogState()).value).toBe('Session A');
      return 'B reseeded after A draft; no leak in either direction';
    });

    // 7. Submit-by-click plus pre-submit validation (whitespace-only name).
    await run('submit-click-and-empty-rejection', async () => {
      await resetPage();
      const before = renamePosts().length;
      await openRename('Rename B');
      await page.keyboard.type('   ');
      await page.getByRole('button', { name: 'Rename', exact: true }).click();
      await page.getByRole('status').filter({ hasText: 'Session name cannot be empty' }).waitFor();
      const state = await dialogState();
      assert.equal(state.dialogOpen, true, 'dialog must stay open on empty name');
      assert.equal(state.value, '   ');
      assert.equal(renamePosts().length, before, 'no POST may be sent for an empty name');
      await page.keyboard.press('Control+a');
      await page.keyboard.type('B v3');
      await page.getByRole('button', { name: 'Rename', exact: true }).click();
      await expect.poll(async () => (await dialogState()).dialogOpen).toBe(false);
      await expect.poll(async () => (await storeState())[1].name).toBe('B v3');
      const posts = renamePosts();
      assert.equal(posts.length, before + 1, 'exactly one rename POST expected');
      assert.equal(posts.at(-1).body.name, 'B v3');
      return 'empty rejected; click-submit carried the draft';
    });

    // 8. In-flight submit guard: a refresh must not re-enable the input while
    //    the rename request is still pending (double-submit window).
    await run('in-flight-guard-across-refresh', async () => {
      await resetPage();
      let releaseRename;
      holdRename = new Promise((resolve) => { releaseRename = resolve; });
      try {
        await openRename('Rename A');
        await page.keyboard.type('A v4');
        await page.keyboard.press('Enter');
        await expect.poll(async () => (await dialogState()).inputDisabled).toBe(true);
        await page.evaluate(() =>
          window.simulateRefresh('a', { lastMessage: 'preview while submitting' }));
        await expect.poll(async () => (await dialogState()).inputDisabled)
          .toBe(true); // baseline resets submitting=false and re-arms the input
        assert.equal((await dialogState()).value, 'A v4', 'refresh overwrote the draft mid-flight');
      } finally {
        releaseRename();
        holdRename = null;
      }
      await expect.poll(async () => (await dialogState()).dialogOpen).toBe(false);
      await expect.poll(async () => (await storeState())[0].name).toBe('A v4');
      return 'input stayed disabled + draft intact until the POST settled';
    });

    // 9. Server-side rename failure: toast, dialog stays open, draft intact.
    //    Observed (pre-existing, unchanged by this fix): the input loses DOM
    //    focus while the request is in flight because it is `disabled` during
    //    submit, so the retry is a real user click on the Rename button.
    await run('rename-failure-error-path', async () => {
      await resetPage();
      failNextRename = true;
      await openRename('Rename A');
      await page.keyboard.type('A v5');
      await page.keyboard.press('Enter');
      await page.getByRole('status').filter({ hasText: 'Rename rejected by server' }).waitFor();
      const state = await dialogState();
      assert.equal(state.dialogOpen, true, 'dialog must stay open on server error');
      assert.equal(state.value, 'A v5', 'draft must survive a failed rename');
      await page.getByRole('button', { name: 'Rename', exact: true }).click();
      await expect.poll(async () => (await dialogState()).dialogOpen).toBe(false);
      await expect.poll(async () => (await storeState())[0].name).toBe('A v5');
      return 'error toast; draft preserved; click-retry succeeded';
    });

    // 10. Late response: a stale GET /api/sessions issued before the rename
    //     lands after it (plus the production WS session.renamed patch) and
    //     must not roll the committed name back.
    await run('late-stale-sessions-get', async () => {
      await resetPage();
      const stale = { sessions: JSON.parse(JSON.stringify(mockSessions)) };
      let releaseGet;
      holdSessionsGet = new Promise((resolve) => { releaseGet = resolve; });
      sessionsGetOverride = stale;
      try {
        // The held request must not be awaited here: page.evaluate awaits a
        // returned promise, and this one only settles once the gate opens.
        await page.evaluate(() => { void window.refreshSessions(); });
        await openRename('Rename A');
        await page.keyboard.type('A v6');
        await page.keyboard.press('Enter');
        await expect.poll(async () => (await storeState())[0].name).toBe('A v6');
        await page.evaluate(() => window.simulateWsRenamed('a', 'A v6'));
      } finally {
        releaseGet();
        holdSessionsGet = null;
        sessionsGetOverride = null;
      }
      await expect.poll(async () => (await storeState())[0].name).toBe('A v6');
      await page.evaluate(() => { void window.refreshSessions(); });
      await expect.poll(async () => (await storeState())[0].name).toBe('A v6');
      return 'stale snapshot did not roll back the committed name';
    });

    await page.screenshot({
      path: `${output}/${tag}-${width}-final.png`,
      fullPage: true,
      animations: 'disabled',
    });
    await context.close();
  }
  assert.deepEqual(evidence.errors, []);
} finally {
  clearTimeout(watchdog);
  writeFileSync(`${output}/results-${tag}.json`, JSON.stringify(evidence, null, 2));
  await browser?.close();
  if (process.platform === 'win32')
    execFileSync('taskkill', ['/pid', String(server.pid), '/t', '/f'], {
      windowsHide: true,
      stdio: 'ignore',
    });
  else server.kill('SIGTERM');
}

const failed = evidence.results.filter((result) => !result.ok);
console.log(JSON.stringify(evidence.results, null, 2));
if (failed.length > 0) {
  console.error(`${failed.length} scenario(s) failed on the ${tag} run`);
  process.exitCode = 1;
}
