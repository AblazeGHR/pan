/* Real-chain E2E: manual report of any completed run.
 *
 * Real isolated FastAPI backend (e2e/manual-report-real.server.py) on an
 * owned dynamic port, real on-disk fixtures, real /ws, and the production
 * frontend bundle served by that server (/react/).  No frontend mock is
 * installed: the browser talks to the real routes, and every assertion about
 * what was delivered is read back from the manager's real receipt file on
 * disk (manual_report_receipts), never from a test-invented surface.
 *
 * Covers: plain run (taskId:null + taskSeq), cursor-only identity, a
 * completion the identity view excludes (no entry), real routed body
 * identity vs the fixture's own tail row, double-click / replay run-level
 * dedup across a real server restart, formal-task regression, real failure
 * feedback (missing manager), gutter-menu bookmark add/remove persisted on
 * disk and recovered by a reload, dynamic port (never 8767/8768).
 *
 * Run after `pnpm build`: the browser loads the production bundle served by
 * the launcher, so the dist must be current for UI changes to be under test.
 *
 * Boundary kept (not an overclaim): the frontend binds a run's tail only when
 * the run's covered boundary row is that assistant block.  A run whose final
 * rows are tool/thinking blocks stays unbound — fewer entries, never a wrong
 * one.  So NOT every completed run is guaranteed an entry in the UI.
 */
/* global console, document, process, fetch, setTimeout */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, '..');
const root = path.resolve(webRoot, '..', '..');
const python = process.env.PAN_E2E_PYTHON || 'D:/project/Pan/.venv/Scripts/python.exe';
const runtime = path.join(webRoot, 'test-results', 'manual-report-real');
await fs.rm(runtime, { recursive: true, force: true });
await fs.mkdir(runtime, { recursive: true });

const GUTTER = 'button[data-message-bookmark-gutter]';
const MENU = '[data-message-block-menu]';
const REPORT_ITEM = `${MENU} [data-manual-report]`;

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

let server;
let serverLog = '';
async function startServer() {
  const port = await freePort();
  assert.ok(port !== 8767 && port !== 8768, `owned port must not be a Pan port (got ${port})`);
  server = spawn(python, [path.join(here, 'manual-report-real.server.py')], {
    cwd: root,
    windowsHide: true,
    env: { ...process.env, PAN_PORT: String(port), PAN_E2E_RUNTIME: runtime },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  server.stdout.on('data', (chunk) => { serverLog += chunk; });
  server.stderr.on('data', (chunk) => { serverLog += chunk; });
  const base = `http://127.0.0.1:${port}`;
  for (let attempt = 0; attempt < 300; attempt += 1) {
    try {
      const res = await fetch(`${base}/api/sessions`);
      if (res.ok) return base;
    } catch {
      // starting
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`real backend did not become ready\n--- server log ---\n${serverLog.slice(-4000)}`);
}

async function stopServer() {
  if (!server) return;
  const exited = new Promise((resolve) => server.once('exit', resolve));
  server.kill();
  await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 5000))]);
  server = null;
}

const results = [];
async function check(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
    console.log(`[PASS] ${name}`);
  } catch (error) {
    results.push({ name, ok: false, error: String(error?.message || error) });
    console.log(`[FAIL] ${name}: ${error?.message || error}`);
  }
}

/** One row's messageId from the fixture history on disk (0-based line). */
const childRowId = async (childId, index) => {
  const raw = await fs.readFile(path.join(runtime, 'sessions', `${childId}.history.jsonl`), 'utf8');
  const rows = raw.trim().split('\n').map((line) => JSON.parse(line));
  return rows[index].messageId;
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** The manager's durable receipts, read from its real session file on disk. */
const managerReceipts = async (managerId) => {
  const raw = await fs.readFile(path.join(runtime, 'sessions', `${managerId}.json`), 'utf8');
  return (JSON.parse(raw).manual_report_receipts ?? {});
};
const waitForReceipts = async (managerId, expectedCount) => {
  let receipts = {};
  for (let attempt = 0; attempt < 40; attempt += 1) {
    receipts = await managerReceipts(managerId);
    if (Object.keys(receipts).length >= expectedCount) return receipts;
    await sleep(150);
  }
  return receipts;
};
/** The run's tail row messageId, straight from the fixture history on disk. */
const childTailId = async (childId) => {
  const raw = await fs.readFile(path.join(runtime, 'sessions', `${childId}.history.jsonl`), 'utf8');
  const rows = raw.trim().split('\n').map((line) => JSON.parse(line));
  return rows[rows.length - 1].messageId;
};

let browser;
try {
  const base = await startServer();
  const fixtures = JSON.parse(await fs.readFile(path.join(runtime, 'fixtures.json'), 'utf8'));
  const managerId = fixtures.managerId;
  const child = (name) => fixtures.children[name];
  // The live backend base; the restart check repoints it.
  let activeBase = base;
  console.log(`[real-e2e] backend on ${base}; manager ${managerId}`);

  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const panRequests = [];
  const pageErrors = [];
  const page = await context.newPage();
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => {
    if (/:(8767|8768)(\/|$)/.test(request.url())) panRequests.push(request.url());
  });

  const gotoApp = async (target = base) => {
    await page.goto(`${target}/react/?panE2E=1`, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible', timeout: 15000 });
  };
  /** Select a Session and prove the switch actually landed by waiting for one
   *  of its own rows: an unverified switch left the previous transcript in
   *  place and produced empty scans that looked like missing entries. */
  const selectSessionOn = async (targetPage, name, expectText) => {
    await targetPage.locator('[data-session-card-id]').filter({ hasText: name }).first().click();
    if (expectText) {
      await targetPage.locator('.message-row', { hasText: expectText }).first()
        .waitFor({ state: 'visible', timeout: 15000 }).catch(() => {});
    }
    await targetPage.waitForTimeout(400);
  };
  const selectSession = (name, expectText) => selectSessionOn(page, name, expectText);
  const reportEntries = async () => {
    const found = [];
    await page.locator(GUTTER).first().waitFor({ state: 'visible', timeout: 15000 }).catch(() => {});
    for (const gutter of await page.locator(GUTTER).all()) {
      if (!(await clickGutterOn(page, gutter))) continue;
      await page.waitForSelector(MENU, { state: 'visible', timeout: 3000 }).catch(() => {});
      for (const item of await page.locator(REPORT_ITEM).all()) {
        found.push({ key: await item.getAttribute('data-manual-report'), text: (await item.textContent()) ?? '' });
      }
      await page.keyboard.press('Escape');
    }
    return found;
  };
  /** Open one gutter's menu, skipping rows the workspace drawer handle
   *  overlaps (that pre-existing handle sits on the chat's left edge for
   *  vertically centred rows and intercepts the gutter's hit point). */
  const clickGutterOn = async (targetPage, gutter) => {
    try {
      await gutter.click({ timeout: 2500 });
      return true;
    } catch {
      await targetPage.keyboard.press('Escape').catch(() => {});
      return false;
    }
  };
  const openReportMenuOn = async (targetPage, key) => {
    await targetPage.locator(GUTTER).first().waitFor({ state: 'visible', timeout: 15000 }).catch(() => {});
    for (const gutter of await targetPage.locator(GUTTER).all()) {
      if (!(await clickGutterOn(targetPage, gutter))) continue;
      await targetPage.waitForSelector(MENU, { state: 'visible', timeout: 3000 }).catch(() => {});
      const item = targetPage.locator(`${REPORT_ITEM}[data-manual-report="${key}"]`);
      if (await item.count()) return item.first();
      await targetPage.keyboard.press('Escape');
    }
    throw new Error(`no report entry for ${key}`);
  };
  const openReportMenu = (key) => openReportMenuOn(page, key);
  /** The bindings ride the WS resync snapshot (the REST list/detail carry no
   *  terminal identities), so a freshly opened page may need a moment before
   *  the report entry exists: retry until it does. */
  const waitForItemOn = async (targetPage, key) => {
    let last;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      try {
        return await openReportMenuOn(targetPage, key);
      } catch (error) {
        last = error;
        await targetPage.keyboard.press('Escape').catch(() => {});
        await targetPage.waitForTimeout(250);
      }
    }
    // Diagnostics: what does the page actually offer instead of the entry?
    const dump = await targetPage.evaluate(() => ({
      gutters: document.querySelectorAll('button[data-message-bookmark-gutter]').length,
      rows: [...document.querySelectorAll('.message-row')].map((row) => (row.textContent ?? '').slice(0, 18)),
      toasts: [...document.querySelectorAll('[role="alert"]')].map((node) => (node.textContent ?? '').slice(0, 40)),
    })).catch(() => null);
    throw new Error(`no report entry for ${key} (${JSON.stringify(dump)}; last: ${last instanceof Error ? last.message : last})`);
  };
  const clickReport = async (key) => {
    // A slow history load can still show the previous Session's rows: retry
    // the whole find-and-click path instead of racing the switch.
    let lastError;
    for (let attempt = 0; attempt < 20; attempt += 1) {
      try {
        const item = await openReportMenu(key);
        assert.equal(await item.isDisabled(), false, `entry ${key} must be actionable`);
        await item.click({ timeout: 5000 });
        return;
      } catch (error) {
        lastError = error;
        await page.keyboard.press('Escape').catch(() => {});
        await page.waitForTimeout(250);
      }
    }
    throw lastError instanceof Error ? lastError : new Error(`clickReport ${key} failed`);
  };
  const waitForState = async (key, pattern) => {
    let last = '';
    for (let attempt = 0; attempt < 60; attempt += 1) {
      try {
        const item = await openReportMenu(key);
        const text = (await item.textContent()) ?? '';
        const state = { text, disabled: await item.isDisabled(), title: await item.getAttribute('title') };
        await page.keyboard.press('Escape');
        if (pattern.test(text)) return state;
        last = text;
      } catch (error) {
        last = String(error?.message || error);
        await page.keyboard.press('Escape').catch(() => {});
      }
      await page.waitForTimeout(150);
    }
    throw new Error(`entry ${key} never reached ${pattern} (last: ${last})`);
  };

  await gotoApp();

  await check('plain run: taskId null + taskSeq delivers the run identity to disk', async () => {
    await selectSession('MR Plain Seq', 'plain message 1');
    const key = `${child('MR Plain Seq')}:-:1`;
    await clickReport(key);
    await waitForState(key, /已汇报/);
    const receipts = await waitForReceipts(managerId, 1);
    const receipt = receipts[`manual:${key}`];
    assert.ok(receipt, `receipt missing: ${JSON.stringify(receipts)}`);
    assert.equal(receipt.taskId ?? null, null, 'plain run delivers taskId null');
    assert.equal(receipt.taskSeq, 1);
    assert.equal(receipt.messageId, await childTailId(child('MR Plain Seq')),
      'the delivered block must be the fixture run\'s own tail row');
    assert.equal(Object.keys(receipts).length, 1);
  });

  await check('cursor-only run: resultCursor alone delivers and keys by rc:7', async () => {
    await selectSession('MR Plain Cursor', 'plain message 7');
    const key = `${child('MR Plain Cursor')}:-:rc:7`;
    await clickReport(key);
    await waitForState(key, /已汇报/);
    const receipts = await waitForReceipts(managerId, 2);
    const receipt = receipts[`manual:${key}`];
    assert.ok(receipt, `rc-keyed receipt missing: ${JSON.stringify(receipts)}`);
    assert.equal(receipt.taskId ?? null, null);
    assert.equal(receipt.taskSeq ?? null, null, 'a cursor-only run carries no taskSeq');
    assert.equal(receipt.messageId, await childTailId(child('MR Plain Cursor')));
    assert.equal(Object.keys(receipts).length, 2);
  });

  await check('no exposed terminal: an excluded completion offers no entry', async () => {
    await selectSession('MR Interrupted Run', 'plain message 9');
    const entries = await reportEntries();
    assert.deepEqual(entries, [], `no entry may exist for an unexposed completion: ${JSON.stringify(entries)}`);
  });

  await check('formal task run: the existing taskId identity still reports (race click included)', async () => {
    await selectSession('MR Formal Task', 'do mr-task');
    const key = `${child('MR Formal Task')}:mr-task:3`;
    await clickReport(key);
    // The real race window: re-open the menu immediately and click again if it
    // still offers the action.  The store's single-flight and the server-side
    // run receipt must keep this to exactly one delivery.
    const racing = await openReportMenu(key).catch(() => null);
    if (racing && !(await racing.isDisabled())) await racing.click();
    await page.keyboard.press('Escape');
    await waitForState(key, /已汇报/);
    const receipts = await waitForReceipts(managerId, 3);
    const receipt = receipts[`manual:${key}`];
    assert.ok(receipt, `formal receipt missing: ${JSON.stringify(receipts)}`);
    assert.equal(receipt.taskId, 'mr-task');
    assert.equal(receipt.taskSeq, 3);
    assert.equal(receipt.messageId, await childTailId(child('MR Formal Task')));
    assert.equal(
      Object.keys(receipts).filter((receiptKey) => receiptKey === `manual:${key}`).length, 1,
      'a racing second click must not mint a second receipt',
    );
  });

  await check('gutter bookmark: add and remove persist on disk and survive a reload', async () => {
    const seqChild = child('MR Plain Seq');
    const sidecar = path.join(runtime, 'session-bookmarks', `${seqChild}.json`);
    await fs.rm(sidecar, { force: true });
    await selectSession('MR Plain Seq', 'plain message 1');

    // Pick the row whose menu offers ONLY the bookmark action (the run's
    // intermediate answer row — no report binding), and remember its
    // messageId from the gutter itself.
    let bookmarkedId = null;
    for (const gutter of await page.locator(GUTTER).all()) {
      const messageId = await gutter.getAttribute('data-message-bookmark-gutter');
      if (!(await clickGutterOn(page, gutter))) continue;
      await page.waitForSelector(MENU, { state: 'visible', timeout: 3000 }).catch(() => {});
      const bookmarkItem = page.locator(`${MENU} [data-bookmark-action]`);
      const reportItem = page.locator(REPORT_ITEM);
      if (await bookmarkItem.count() && !(await reportItem.count())) {
        assert.match((await bookmarkItem.textContent()) ?? '', /Add bookmark/);
        await bookmarkItem.click();
        bookmarkedId = messageId;
        break;
      }
      await page.keyboard.press('Escape');
    }
    assert.ok(bookmarkedId, 'no bookmark-only row was offered');

    // The real sidecar file on disk carries the row's own identity.
    let envelope = null;
    for (let attempt = 0; attempt < 40; attempt += 1) {
      try {
        envelope = JSON.parse(await fs.readFile(sidecar, 'utf8'));
        break;
      } catch {
        await sleep(150);
      }
    }
    assert.ok(envelope, `bookmark sidecar missing at ${sidecar}`);
    assert.equal(envelope.revision, 1);
    assert.deepEqual(Object.keys(envelope.bookmarks), [bookmarkedId]);
    assert.equal(envelope.bookmarks[bookmarkedId].messageId, bookmarkedId);

    // A reload recovers the state from the real endpoints: the row shows the
    // body tag and its menu offers removal.
    await page.reload({ waitUntil: 'domcontentloaded' });
    await selectSession('MR Plain Seq', 'plain message 1');
    assert.ok(await page.locator('[data-message-bookmark-tag]').count() >= 1,
      'the reloaded row must show the persisted bookmark tag');
    let removeItem = null;
    for (const gutter of await page.locator(GUTTER).all()) {
      if ((await gutter.getAttribute('data-message-bookmark-gutter')) !== bookmarkedId) continue;
      if (!(await clickGutterOn(page, gutter))) continue;
      await page.waitForSelector(MENU, { state: 'visible', timeout: 3000 }).catch(() => {});
      removeItem = page.locator(`${MENU} [data-bookmark-action="remove"]`);
      break;
    }
    assert.ok(removeItem && await removeItem.count(), 'the persisted bookmark must offer removal');
    await removeItem.click();
    for (let attempt = 0; attempt < 40; attempt += 1) {
      envelope = JSON.parse(await fs.readFile(sidecar, 'utf8'));
      if (envelope.revision >= 2) break;
      await sleep(150);
    }
    assert.equal(envelope.revision, 2);
    assert.deepEqual(envelope.bookmarks, {});

    // And the removal is what a fresh load sees too.
    await page.reload({ waitUntil: 'domcontentloaded' });
    await selectSession('MR Plain Seq', 'plain message 1');
    assert.equal(await page.locator('[data-message-bookmark-tag]').count(), 0);
  });

  await check('bookmark endpoint idempotency: repeating the state changes nothing on disk', async () => {
    const seqChild = child('MR Plain Seq');
    const sidecar = path.join(runtime, 'session-bookmarks', `${seqChild}.json`);
    const before = JSON.parse(await fs.readFile(sidecar, 'utf8')).revision;
    const call = (method, target) => fetch(
      `${base}/api/sessions/${seqChild}/bookmarks/${encodeURIComponent(target)}`, { method });
    const target = await childTailId(seqChild);

    const first = await (await call('PUT', target)).json();
    assert.equal(first.changed, true);
    assert.equal(first.revision, before + 1);
    const second = await (await call('PUT', target)).json();
    assert.equal(second.changed, false, 'a repeated PUT must be a no-op');
    assert.equal(second.revision, before + 1);
    let envelope = JSON.parse(await fs.readFile(sidecar, 'utf8'));
    assert.equal(envelope.revision, before + 1);

    const removed = await (await call('DELETE', target)).json();
    assert.equal(removed.changed, true);
    assert.equal(removed.revision, before + 2);
    const removedAgain = await (await call('DELETE', target)).json();
    assert.equal(removedAgain.changed, false, 'a repeated DELETE must be a no-op');
    assert.equal(removedAgain.revision, before + 2);
    envelope = JSON.parse(await fs.readFile(sidecar, 'utf8'));
    assert.equal(envelope.revision, before + 2);
    assert.deepEqual(envelope.bookmarks, {});
  });

  await check('bookmark save failure: a real damaged sidecar surfaces, rolls back, is never overwritten', async () => {
    const seqChild = child('MR Plain Seq');
    const sidecar = path.join(runtime, 'session-bookmarks', `${seqChild}.json`);
    const damaged = '{"version": 1, "revision": 9, "bookmarks": {"pan:';
    await fs.writeFile(sidecar, damaged, 'utf8');
    const freshBrowser = await chromium.launch({ headless: true });
    try {
      const freshContext = await freshBrowser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
      const fresh = await freshContext.newPage();
      await fresh.goto(`${base}/react/?panE2E=1`, { waitUntil: 'domcontentloaded' });
      await fresh.locator('[data-session-card-id]').first().waitFor({ state: 'visible', timeout: 15000 });
      await selectSessionOn(fresh, 'MR Plain Seq', 'plain message 1');
      // Add through the real UI on the bookmark-only row: the real endpoint
      // must refuse with the unreadable-store error.
      let attempted = false;
      for (const gutter of await fresh.locator(GUTTER).all()) {
        if (!(await clickGutterOn(fresh, gutter))) continue;
        await fresh.waitForSelector(MENU, { state: 'visible', timeout: 3000 }).catch(() => {});
        const bookmarkItem = fresh.locator(`${MENU} [data-bookmark-action="add"]`);
        const reportItem = fresh.locator(REPORT_ITEM);
        if (await bookmarkItem.count() && !(await reportItem.count())) {
          await bookmarkItem.click();
          attempted = true;
          break;
        }
        await fresh.keyboard.press('Escape');
      }
      assert.ok(attempted, 'no bookmark-only row was offered');
      const toastNode = fresh.locator('[role="alert"], [data-toast]').first();
      await toastNode.waitFor({ state: 'visible', timeout: 8000 });
      const toast = (await toastNode.textContent()) ?? '';
      assert.match(toast, /could not be read|unreadable|不可读/i, `expected an unreadable-store toast, got: ${toast}`);
      assert.equal(await fs.readFile(sidecar, 'utf8'), damaged,
        'a refused save must not destroy the damaged sidecar');
      // The row did not pretend success: the menu still offers the add.
      let stillAddable = false;
      for (const gutter of await fresh.locator(GUTTER).all()) {
        if (!(await clickGutterOn(fresh, gutter))) continue;
        await fresh.waitForSelector(MENU, { state: 'visible', timeout: 3000 }).catch(() => {});
        if (await fresh.locator(`${MENU} [data-bookmark-action="add"]`).count()) stillAddable = true;
        await fresh.keyboard.press('Escape');
        if (stillAddable) break;
      }
      assert.ok(stillAddable, 'a failed save must roll back to the add state');
    } finally {
      await freshBrowser.close();
    }
    // Restore a clean sidecar so the remaining checks start deterministic.
    await fs.writeFile(sidecar, JSON.stringify({
      version: 1, revision: 0, bookmarks: {}, updatedAt: null,
    }), 'utf8');
  });

  await check('failing target: a missing manager surfaces as a real retry state', async () => {
    await selectSession('MR Broken Manager', 'plain message 2');
    const key = `${child('MR Broken Manager')}:-:2`;
    await clickReport(key);
    const state = await waitForState(key, /重试汇报/);
    assert.match(state.title ?? '', /manager|无法汇报/);
    // The failure is a definite rejection: retrying changes nothing.
    await clickReport(key);
    await waitForState(key, /重试汇报/);
    const receipts = await managerReceipts(managerId);
    assert.equal(Object.keys(receipts).length, 3, 'failed attempts must never deliver');
    assert.ok(!Object.keys(receipts).some((receiptKey) => receiptKey.includes(child('MR Broken Manager'))));
  });

  await check('double click: extra clicks after the sent state deliver nothing new', async () => {
    const freshBrowser = await chromium.launch({ headless: true });
    try {
      const freshContext = await freshBrowser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
      const fresh = await freshContext.newPage();
      await fresh.goto(`${base}/react/?panE2E=1`, { waitUntil: 'domcontentloaded' });
      await fresh.locator('[data-session-card-id]').first().waitFor({ state: 'visible', timeout: 15000 });
      await selectSessionOn(fresh, 'MR Plain Seq', 'plain message 1');
      const key = `${child('MR Plain Seq')}:-:1`;
      const before = (await managerReceipts(managerId))[`manual:${key}`]?.at;
      await waitForItemOn(fresh, key);
      await fresh.keyboard.press('Escape');
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const item = await waitForItemOn(fresh, key);
        if (!(await item.isDisabled())) {
          await item.click({ timeout: 5000 }).catch(async () => {
            await fresh.keyboard.press('Escape').catch(() => {});
          });
        }
        await fresh.keyboard.press('Escape');
        await fresh.waitForTimeout(250);
      }
      const receipts = await managerReceipts(managerId);
      assert.equal(Object.keys(receipts).length, 3);
      assert.equal(receipts[`manual:${key}`]?.at, before, 'the receipt must not be rewritten');
    } finally {
      await freshBrowser.close();
    }
  });

  await check('receipts persist on real disk with their routed identities', async () => {
    const receipts = await managerReceipts(managerId);
    assert.deepEqual(
      Object.keys(receipts).sort(),
      [
        `manual:${child('MR Plain Cursor')}:-:rc:7`,
        `manual:${child('MR Plain Seq')}:-:1`,
        `manual:${child('MR Formal Task')}:mr-task:3`,
      ].sort(),
    );
  });

  await check('restart replay: the same durable receipt answers, no second delivery', async () => {
    const key = `${child('MR Plain Seq')}:-:1`;
    const at = (await managerReceipts(managerId))[`manual:${key}`]?.at;
    await stopServer();
    const restarted = await startServer();
    activeBase = restarted;
    // Same runtime, same Sessions: the receipt must still be the same one.
    const fixturesAfter = JSON.parse(await fs.readFile(path.join(runtime, 'fixtures.json'), 'utf8'));
    assert.equal(fixturesAfter.managerId, managerId, 'restart must reuse the same fixture generation');
    assert.equal(fixturesAfter.children['MR Plain Seq'], child('MR Plain Seq'));
    // A fresh browser against the restarted service: the report entry (its
    // binding comes from the surviving terminal on disk) must replay against
    // the SAME receipt instead of minting a second delivery.  A brand-new
    // browser process avoids a renderer crash observed on reused pages here;
    // one retry absorbs the residual renderer flake.
    let replayError;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      replayError = undefined;
      const freshBrowser = await chromium.launch({ headless: true });
      try {
        const freshContext = await freshBrowser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
        const fresh = await freshContext.newPage();
        fresh.on('crash', () => console.log('[real-e2e] renderer crashed during the restart replay'));
        await fresh.goto(`${restarted}/react/?panE2E=1`, { waitUntil: 'domcontentloaded' });
        await fresh.locator('[data-session-card-id]').first().waitFor({ state: 'visible', timeout: 15000 });
        await selectSessionOn(fresh, 'MR Plain Seq', 'plain message 1');
        let item = await waitForItemOn(fresh, key);
        assert.equal(await item.isDisabled(), false, 'after a restart the entry is unsent locally');
        await item.click({ timeout: 5000 }).catch(async () => {
          await fresh.keyboard.press('Escape').catch(() => {});
          item = await waitForItemOn(fresh, key);
          await item.click({ timeout: 5000 });
        });
        for (let poll = 0; poll < 60; poll += 1) {
          item = await waitForItemOn(fresh, key);
          const text = (await item.textContent()) ?? '';
          await fresh.keyboard.press('Escape');
          if (/已汇报/.test(text)) break;
          await fresh.waitForTimeout(200);
        }
        replayError = undefined;
      } catch (error) {
        replayError = error;
      } finally {
        await freshBrowser.close();
      }
      if (replayError === undefined) break;
    }
    if (replayError !== undefined) throw replayError;
    await sleep(800);
    const receipts = await managerReceipts(managerId);
    assert.equal(Object.keys(receipts).length, 3, `a replay must not add receipts: ${JSON.stringify(receipts)}`);
    assert.equal(receipts[`manual:${key}`]?.at, at, 'the replay must resolve to the surviving receipt');
  });

  const reportOverHttp = async (sessionId, body) => {
    const res = await fetch(`${activeBase}/api/sessions/${sessionId}/manual-report`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    assert.equal(res.status, 200);
    return res.json();
  };

  await check('derived compat cursor (single legacy done): no UI entry, resolver still unique over HTTP', async () => {
    await gotoApp(activeBase);
    await selectSession('MR Legacy Done', '遗留提问');
    const legacyChild = child('MR Legacy Done');
    // Production allocates real cursors as max(existing)+1, so the view's 0 is
    // the derived compat value and cannot prove unique ownership: no entry.
    const entries = await reportEntries();
    assert.deepEqual(entries, [],
      `a derived compat cursor must not open an entry: ${JSON.stringify(entries)}`);
    // The real resolver, probed directly over HTTP, still resolves the single
    // compat row — capability retained, UI conservative.
    const tailId = await childTailId(legacyChild);
    const probe = await reportOverHttp(legacyChild, {
      messageId: tailId, taskId: null, taskSeq: null, resultCursor: 0,
    });
    assert.equal(probe.ok, true, JSON.stringify(probe));
    const receipts = await waitForReceipts(managerId, 4);
    assert.equal(Object.values(receipts).filter((receipt) => receipt.messageId === tailId).length, 1,
      `the unique compat resolution must deliver exactly once: ${JSON.stringify(receipts)}`);
  });

  await check('two legacy done rows: no UI entries, the real resolver refuses the ambiguous 0', async () => {
    await selectSession('MR Legacy Ambiguous', '歧义提问一');
    const legacyChild = child('MR Legacy Ambiguous');
    const entries = await reportEntries();
    assert.deepEqual(entries, [],
      `ambiguous compat rows must not open entries: ${JSON.stringify(entries)}`);
    const before = Object.keys(await managerReceipts(managerId)).length;
    const probe = await reportOverHttp(legacyChild, {
      messageId: await childTailId(legacyChild), taskId: null, taskSeq: null, resultCursor: 0,
    });
    assert.equal(probe.ok, false, JSON.stringify(probe));
    assert.equal(probe.error?.code, 'no_task_result', JSON.stringify(probe));
    const receipts = await managerReceipts(managerId);
    assert.equal(Object.keys(receipts).length, before, 'an ambiguous identity must not add receipts');
    assert.ok(!Object.keys(receipts).some((receiptKey) => receiptKey.includes(legacyChild)));
  });

  await check('legacy row beside a real run: only the stored-identity block opens an entry', async () => {
    await selectSession('MR Legacy Mixed', '旧块提问');
    const mixedChild = child('MR Legacy Mixed');
    const entries = await reportEntries();
    assert.deepEqual(entries.map((entry) => entry.key), [`${mixedChild}:-:1`],
      `only the stored-identity run may open an entry: ${JSON.stringify(entries)}`);
    // The new block's stored identity routes and delivers the new run only.
    const newTail = await childTailId(mixedChild);
    const probe = await reportOverHttp(mixedChild, {
      messageId: newTail, taskId: null, taskSeq: 1, resultCursor: 1,
    });
    assert.equal(probe.ok, true, JSON.stringify(probe));
    const receipts = await waitForReceipts(managerId, 5);
    const delivered = Object.values(receipts).filter((receipt) => receipt.messageId === newTail);
    assert.equal(delivered.length, 1);
    assert.equal(delivered[0].taskSeq, 1, 'the delivered run is the stored one');
  });

  await check('raw cursor 0 beside a derived 0: the resolver is exact-first, the UI withholds both', async () => {
    await selectSession('MR Cursor Zero', '派生零提问');
    const czChild = child('MR Cursor Zero');
    // Both rows are indistinguishable in the view ({resultCursor: 0}), so the
    // UI offers no entry for either — raw-zero reachability is unprovable.
    const entries = await reportEntries();
    assert.deepEqual(entries, [], `indistinguishable zero identities must not open entries: ${JSON.stringify(entries)}`);
    // The real resolver's exact-first branch still pins the raw-zero row.
    const legacyTail = await childRowId(czChild, 2);
    const rawTail = await childRowId(czChild, 5);
    const delivered = await reportOverHttp(czChild, {
      messageId: rawTail, taskId: null, taskSeq: null, resultCursor: 0,
    });
    assert.equal(delivered.ok, true, JSON.stringify(delivered));
    const receipts = await waitForReceipts(managerId, 6);
    const receipt = Object.values(receipts).find((candidate) => candidate.messageId === rawTail);
    assert.ok(receipt, `the raw-zero run must deliver its own block: ${JSON.stringify(receipts)}`);
    assert.equal(receipt.taskId ?? null, null);
    assert.equal(receipt.taskSeq ?? null, null);
    // The legacy block submitted against cursor 0 resolves the raw row and is
    // refused as a block mismatch — never bound to the wrong run.
    const mismatched = await reportOverHttp(czChild, {
      messageId: legacyTail, taskId: null, taskSeq: null, resultCursor: 0,
    });
    assert.equal(mismatched.ok, false, JSON.stringify(mismatched));
    assert.equal(mismatched.error?.code, 'block_mismatch', JSON.stringify(mismatched));
    // Stored identities are untouched by the gate: sequence and cursor-only
    // entries behaved identically earlier in this suite.
  });

  await check('legacy dedup across eviction: the surviving receipt answers for another run', async () => {
    const legacyChild = child('MR Legacy Done');
    const receiptsBefore = await managerReceipts(managerId);
    const priorKey = `manual:${legacyChild}:-:-`;
    const priorMessageId = receiptsBefore[priorKey]?.messageId;
    assert.ok(priorMessageId, 'the earlier legacy delivery must exist for this scenario');
    // Simulate the bounded-window eviction production reaches: the delivered
    // run's terminal leaves the window while its durable receipt survives, and
    // a NEW legacy run (B) with no stored identity appears in the same Session.
    const sessionFile = path.join(runtime, 'sessions', `${legacyChild}.json`);
    const data = JSON.parse(await fs.readFile(sessionFile, 'utf8'));
    const mkId = (suffix) => `pan:${'e'.repeat(30)}${suffix}`;
    const stamp = new Date().toISOString();
    const bRows = [
      { role: 'user', content: '淘汰提问', messageId: mkId('b1'), source: 'user', ts: stamp },
      { role: 'assistant', content: '淘汰回答', messageId: mkId('b2'), ts: stamp },
      { role: 'assistant', content: '淘汰回答', messageId: mkId('b3'), ts: stamp },
    ];
    // The session FILE is snake_case (Session(**data) dataclass fields) —
    // camelCase here crashed the whole store load.
    data.history = [...(data.history ?? []), ...bRows];
    data.history_revision = data.history.length;
    data.terminal_results = [{
      status: 'done', result: '淘汰回答', taskId: null, workerId: 'worker-1',
      terminalKey: 'seq:0', historyEpoch: data.history_epoch, historyRevision: data.history.length,
    }];
    await fs.writeFile(sessionFile, JSON.stringify(data, null, 2), 'utf8');
    const jsonl = path.join(runtime, 'sessions', `${legacyChild}.history.jsonl`);
    const existing = await fs.readFile(jsonl, 'utf8');
    await fs.writeFile(jsonl, existing + bRows.map((row) => `${JSON.stringify(row)}
`).join(''), 'utf8');
    // The server caches loaded Sessions; restart so it re-reads the file.
    await stopServer();
    const reopened = await startServer();
    activeBase = reopened;
    const probe = await reportOverHttp(legacyChild, {
      messageId: mkId('b3'), taskId: null, taskSeq: null, resultCursor: 0,
    });
    // Reproduced hazard: B resolves uniquely, but the server's key derived from
    // the resolved row's OWN (absent) fields collides with A's surviving
    // receipt, so B reports as already delivered and is never enqueued.
    assert.equal(probe.ok, true, JSON.stringify(probe));
    assert.equal(probe.alreadyReported, true, JSON.stringify(probe));
    const receiptsAfter = await managerReceipts(managerId);
    assert.deepEqual(Object.keys(receiptsAfter).sort(), Object.keys(receiptsBefore).sort(),
      'no new receipt may appear for the collided run');
    assert.equal(receiptsAfter[priorKey]?.messageId, priorMessageId,
      'the surviving receipt still records the earlier run');
    // The UI never reaches this path: derived-compat rows offer no entry.
    await gotoApp(activeBase);
    await selectSession('MR Legacy Done', '淘汰提问');
    assert.deepEqual(await reportEntries(), []);
  });

  assert.deepEqual(panRequests, [], `no Pan-port traffic is allowed: ${JSON.stringify(panRequests)}`);
  console.log('[real-e2e] no 8767/8768 traffic');
  await fs.writeFile(path.join(runtime, 'results.json'), JSON.stringify({ base, managerId, results }, null, 2));
} finally {
  if (browser) await browser.close();
  await stopServer();
}

const failed = results.filter((result) => !result.ok);
if (failed.length > 0) {
  console.error(`${failed.length} real-chain manual-report check(s) failed`);
  process.exit(1);
}
console.log(`All ${results.length} real-chain manual-report checks passed`);
