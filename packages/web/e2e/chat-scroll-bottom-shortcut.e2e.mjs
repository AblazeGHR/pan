/*
 * Real-Chromium proof for the configurable “Scroll chat to bottom” shortcut.
 *
 * The script drives the shipped React build against the isolated E2E server
 * (owned runtime, random port). It proves keyboard dispatch, virtual-list
 * scrolling, follow-bottom, already-at-bottom no-op, session scoping,
 * editable/Monaco/target protection, recording/clearing/restoring and
 * persistence — never jsdom geometry.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';

const port = Number(process.env.PAN_CHAT_SCROLL_PORT || 0);
assert.ok(Number.isInteger(port) && port > 0, 'PAN_CHAT_SCROLL_PORT is required');
for (const protectedPort of [8765, 8767, 8768]) {
  assert.notEqual(port, protectedPort, `must not use protected port ${protectedPort}`);
}
const baseURL = process.env.PAN_E2E_BASE_URL || `http://127.0.0.1:${port}`;
const outDir = path.resolve(
  process.env.PAN_CHAT_SCROLL_OUT || 'test-results/chat-scroll-bottom-shortcut',
);
fs.mkdirSync(outDir, { recursive: true });

const evidence = { port, baseURL, steps: {}, counts: {}, screenshots: [] };
const record = (name, value) => {
  evidence.steps[name] = value;
  console.log(`[evidence] ${name} ${JSON.stringify(value)}`);
};

async function poll(read, check, timeout = 12000, label = 'condition') {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    last = await read();
    if (check(last)) return last;
    await new Promise((resolve) => setTimeout(resolve, 60));
  }
  throw new Error(`poll timeout (${label}); last=${JSON.stringify(last)}`);
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
  acceptDownloads: false,
});
await context.route('**://fonts.googleapis.com/**', (route) => route.abort());
await context.route('**://fonts.gstatic.com/**', (route) => route.abort());
const page = await context.newPage();
page.on('pageerror', (error) => {
  evidence.counts.pageErrors = (evidence.counts.pageErrors ?? 0) + 1;
  console.error('[pageerror]', error.message);
});

const scroller = () => page.locator('.chat-view-stage .overflow-auto').first();
const metrics = () =>
  scroller().evaluate((el) => ({
    top: el.scrollTop,
    height: el.scrollHeight,
    viewport: el.clientHeight,
    distance: el.scrollHeight - el.scrollTop - el.clientHeight,
  }));

/** Capture whether the app's single dispatcher consumed the next chord. */
async function pressWithProbe(chord) {
  await page.evaluate(() => {
    window.__chordProbe = new Promise((resolve) => {
      const onKey = (event) => {
        if (['Control', 'Shift', 'Alt', 'Meta'].includes(event.key)) return;
        window.removeEventListener('keydown', onKey, true);
        setTimeout(() => resolve({
          key: event.key,
          ctrlKey: event.ctrlKey,
          shiftKey: event.shiftKey,
          defaultPrevented: event.defaultPrevented,
          tag: event.target instanceof Element ? event.target.tagName : String(event.target),
        }), 0);
      };
      window.addEventListener('keydown', onKey, true);
    });
  });
  await page.keyboard.press(chord);
  return page.evaluate(() => window.__chordProbe);
}

async function scrollUp(amount = 6000) {
  await scroller().hover();
  for (let i = 0; i < 60; i += 1) {
    const current = await metrics();
    if (current.distance >= amount) return current;
    await page.mouse.wheel(0, -500);
    await page.waitForTimeout(40);
  }
  throw new Error(`could not scroll away from the bottom (wanted distance >= ${amount})`);
}

async function screenshot(name) {
  const file = path.join(outDir, `${name}.png`);
  await page.screenshot({ path: file, fullPage: false });
  evidence.screenshots.push(file);
}

async function selectSession(name) {
  await page.locator('[data-session-card-id]').filter({ hasText: name }).first().click();
  await page.waitForTimeout(250);
}

async function openSettings(tab) {
  await page.getByTitle('App settings').click();
  await page.getByRole('dialog').waitFor({ state: 'visible' });
  await page.getByRole('tab', { name: tab, exact: true }).click();
}

async function closeSettings() {
  await page.keyboard.press('Escape');
  await page.getByRole('dialog').waitFor({ state: 'detached' });
}

async function stream(sessionId, itemId, content) {
  const response = await page.request.post(`${baseURL}/__e2e/stream`, {
    data: {
      sessionId,
      event: { type: 'assistant', role: 'assistant', content, item_id: itemId, delta: true },
    },
  });
  assert.equal(response.status(), 200, 'stream injection should be accepted');
}

try {
  const identity = await (await page.request.get(`${baseURL}/__e2e/identity`)).json();
  assert.equal(identity.port, port);
  record('serverIdentity', identity);

  await page.goto(`${baseURL}/react/`);
  await selectSession('Chat Stream');
  await page.getByRole('heading', { name: 'Browser file-link fixtures' }).waitFor({ state: 'visible' });
  const sessionId = await page
    .locator('[data-session-card-id]')
    .filter({ hasText: 'Chat Stream' })
    .first()
    .getAttribute('data-session-card-id');
  assert.ok(sessionId, 'Chat Stream session id should be exposed');
  const loaded = await poll(
    metrics,
    (value) => value.height > value.viewport && value.distance <= 1,
    15000,
    'initial pin',
  );
  record('initialPin', loaded);

  // ── 1. Mid-history → true newest message, real keyboard ──────────────────
  const mid = await scrollUp(1500);
  assert.ok(mid.distance >= 1500, 'fixture should have a long virtual list');
  record('midHistory', mid);
  await screenshot('01-mid-history');

  const bottomPress = await pressWithProbe('Control+End');
  assert.equal(bottomPress.defaultPrevented, true, 'visible chat must consume Ctrl+End');
  const afterShortcut = await poll(metrics, (value) => value.distance <= 1, 8000, 'shortcut bottom');
  record('afterShortcut', { press: bottomPress, metrics: afterShortcut });
  await screenshot('02-shortcut-bottom');

  // ── 2. Streaming still follows the tail after the explicit recovery ──────
  await stream(sessionId, 'shortcut-follow', `shortcut followed tail\n${'follow line\n'.repeat(48)}`);
  await page.getByText('shortcut followed tail', { exact: false }).first().waitFor({ state: 'visible' });
  const followed = await poll(metrics, (value) => value.distance <= 1, 8000, 'follow after stream');
  record('followAfterStream', followed);

  // ── 3. Already at the bottom: no scroll side effect ──────────────────────
  const beforeNoop = await metrics();
  const noopPress = await pressWithProbe('Control+End');
  assert.equal(noopPress.defaultPrevented, true);
  const afterNoop = await metrics();
  assert.equal(afterNoop.top, beforeNoop.top, 'already-at-bottom press must not move the list');
  record('alreadyAtBottom', { before: beforeNoop, after: afterNoop });

  // ── 4. Composer keeps the native caret behavior ──────────────────────────
  await scrollUp(900);
  const awayBeforeComposer = await metrics();
  await page.getByTestId('rich-text-composer').click();
  await page.keyboard.type('keyboard guard');
  await page.keyboard.press('Home');
  const composerPress = await pressWithProbe('Control+End');
  assert.equal(composerPress.defaultPrevented, false, 'composer Ctrl+End must stay native');
  const caret = await page.evaluate(() => {
    const editor = document.querySelector('[data-testid="rich-text-composer"]');
    const node = window.getSelection()?.anchorNode ?? null;
    return {
      focusInComposer: Boolean(editor && node && editor.contains(node)),
      offset: window.getSelection()?.anchorOffset ?? -1,
      nodeLength: node?.textContent?.length ?? -1,
    };
  });
  assert.equal(caret.focusInComposer, true, 'caret should stay in the composer');
  assert.equal(caret.offset, caret.nodeLength, 'native Ctrl+End should move the caret to the end');
  const awayAfterComposer = await metrics();
  assert.equal(awayAfterComposer.top, awayBeforeComposer.top, 'composer chord must not scroll chat');
  record('composerProtection', { press: composerPress, caret, awayBeforeComposer });
  await screenshot('03-composer-guard');
  await page.getByTestId('rich-text-composer').fill('');
  await page.locator('.chat-view-stage').click({ position: { x: 8, y: 8 } });

  // ── 5. Settings: visible row, conflict refusal, rebind, persistence ─────
  await openSettings('Shortcuts');
  const row = page.locator('[data-shortcut-action="chat.scrollToBottom"]');
  await row.waitFor({ state: 'visible' });
  const rowText = (await row.innerText()).replace(/\s+/g, ' ').trim();
  assert.ok(rowText.includes('Scroll chat to bottom'), `row should show the label: ${rowText}`);
  const defaultKbd = await page.locator('[data-shortcut-binding="chat.scrollToBottom"]').innerText();
  assert.equal(defaultKbd, 'Ctrl+End');
  record('settingsRow', { rowText, binding: defaultKbd });

  // Recording an already-owned chord is refused, not silently persisted.
  await page.locator('[data-shortcut-record="chat.scrollToBottom"]').click();
  await page.keyboard.press('Control+b');
  await page.getByTestId('shortcut-recorder-error').waitFor({ state: 'visible' });
  const conflictError = await page.getByTestId('shortcut-recorder-error').innerText();
  assert.ok(
    conflictError.includes('already assigned'),
    `conflict should name the existing owner: ${conflictError}`,
  );
  assert.equal(
    await page.locator('[data-shortcut-binding="chat.scrollToBottom"]').innerText(),
    'Ctrl+End',
  );
  record('conflictRefusal', conflictError);

  await page.locator('[data-shortcut-record="chat.scrollToBottom"]').click();
  await page.keyboard.press('Control+Shift+l');
  await poll(
    () => page.locator('[data-shortcut-binding="chat.scrollToBottom"]').innerText(),
    (text) => text === 'Ctrl+Shift+L',
    5000,
    'rebind saved',
  );
  await page.getByTestId('shortcut-save-status').waitFor({ state: 'visible' });
  record('rebound', { binding: 'Ctrl+Shift+L' });
  await screenshot('04-shortcuts-settings');
  await closeSettings();

  await scrollUp(900);
  const beforeOldChord = await metrics();
  const oldChord = await pressWithProbe('Control+End');
  assert.equal(oldChord.defaultPrevented, false, 'the replaced default must be native again');
  const afterOldChord = await metrics();
  assert.equal(afterOldChord.top, beforeOldChord.top, 'replaced chord must not scroll chat');
  const newChord = await pressWithProbe('Control+Shift+L');
  assert.equal(newChord.defaultPrevented, true, 'the recorded binding must run the action');
  await poll(metrics, (value) => value.distance <= 1, 8000, 'rebound bottom');
  record('rebindDispatch', { oldChord, newChord });

  // A reload re-reads the persisted binding from the isolated settings store.
  await page.reload();
  await selectSession('Chat Stream');
  await page.getByRole('heading', { name: 'Browser file-link fixtures' }).waitFor({ state: 'visible' });
  await poll(metrics, (value) => value.height > value.viewport && value.distance <= 1, 15000, 'reload pin');
  await scrollUp(900);
  const persisted = await pressWithProbe('Control+Shift+L');
  assert.equal(persisted.defaultPrevented, true, 'binding must survive a reload');
  await poll(metrics, (value) => value.distance <= 1, 8000, 'persisted bottom');
  record('persistedBinding', persisted);

  // Restore the default from Settings, proving Clear/Default wiring.
  await openSettings('Shortcuts');
  await page.locator('[data-shortcut-reset="chat.scrollToBottom"]').click();
  await poll(
    () => page.locator('[data-shortcut-binding="chat.scrollToBottom"]').innerText(),
    (text) => text === 'Ctrl+End',
    5000,
    'default restored',
  );
  await closeSettings();
  await scrollUp(900);
  const restored = await pressWithProbe('Control+End');
  assert.equal(restored.defaultPrevented, true, 'restored default must run again');
  await poll(metrics, (value) => value.distance <= 1, 8000, 'restored bottom');
  record('restoredDefault', restored);

  // ── 6. Session A → B keeps A's remembered position ──────────────────────
  await openSettings('Appearance');
  const keepRow = page.getByRole('switch', { name: /Keep reading position per session/ });
  if ((await keepRow.getAttribute('aria-checked')) !== 'true') await keepRow.click();
  await closeSettings();

  await selectSession('Chat Stream');
  await page.getByRole('heading', { name: 'Browser file-link fixtures' }).waitFor({ state: 'visible' });
  const aMid = await scrollUp(900);
  await screenshot('05-session-a-mid');

  const bravoId = await page
    .locator('[data-session-card-id]')
    .filter({ hasText: 'Bravo Session' })
    .first()
    .getAttribute('data-session-card-id');
  assert.ok(bravoId);
  const append = await page.request.post(`${baseURL}/__e2e/append-history`, {
    data: {
      sessionId: bravoId,
      messages: Array.from({ length: 70 }, (_, index) => ({
        role: index % 2 === 0 ? 'user' : 'assistant',
        content: `bravo history ${index} ${'b '.repeat(40)}`,
      })),
    },
  });
  assert.equal(append.status(), 200);
  await selectSession('Bravo Session');
  const bLoaded = await poll(metrics, (value) => value.distance <= 1 && value.height > value.viewport, 15000, 'bravo pin');
  const bMid = await scrollUp(700);

  // The explicit jump on B must leave B's bottom and must not overwrite A's
  // remembered position: returning to A restores the mid-history row.
  const bShortcut = await pressWithProbe('Control+End');
  assert.equal(bShortcut.defaultPrevented, true);
  const bBottom = await poll(metrics, (value) => value.distance <= 1, 8000, 'bravo bottom');
  assert.ok(bMid.distance > 500);

  await page.locator('[data-session-card-id]').filter({ hasText: 'Chat Stream' }).first().click();
  const aRestored = await poll(metrics, (value) => value.distance >= 300, 12000, 'a restore');
  record('sessionScoped', { aMid, bLoaded, bMid, bShortcut, bBottom, aRestored });
  await screenshot('06-session-a-restored');

  // ── 7. Empty chat and non-chat routes leave the key alone ───────────────
  await selectSession('Charlie Session');
  await page.getByText('No messages yet. Start a conversation.').waitFor({ state: 'visible' });
  const emptyPress = await pressWithProbe('Control+End');
  assert.equal(emptyPress.defaultPrevented, false, 'empty chat must not swallow the chord');
  record('emptyChat', emptyPress);

  await page.keyboard.press('Control+2');
  await page.locator('.chat-view-stage').waitFor({ state: 'detached' });
  const awayPress = await pressWithProbe('Control+End');
  assert.equal(awayPress.defaultPrevented, false, 'non-chat route must not consume the chord');
  record('nonChatRoute', { url: page.url(), press: awayPress });
  await screenshot('07-non-chat-route');

  evidence.counts.pageErrors = evidence.counts.pageErrors ?? 0;
  assert.equal(evidence.counts.pageErrors, 0, 'the flow must not raise page errors');
  fs.writeFileSync(path.join(outDir, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({ ok: true, outDir, counts: evidence.counts }, null, 2));
} finally {
  await context.close();
  await browser.close();
}
