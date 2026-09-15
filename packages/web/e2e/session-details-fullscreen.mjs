/* Real-Chromium verification for the mobile Session Details fullscreen change.
 *
 * Runs against the isolated Pan E2E server (`e2e/server.py`, launched by
 * `e2e/run-session-details.ps1`), so the browser talks to the real FastAPI
 * routes and the real session store — no frontend mock.
 *
 * Claims under test:
 *   mobile (390x844 and 390x480): the dialog fills the visual viewport with no
 *     window chrome, the title row + close button stay visible at the top, the
 *     middle region scrolls while the title row stays put, and the document
 *     itself never scrolls behind the modal.
 *   desktop (1440x900): the centered size="lg" window is unchanged — 42rem
 *     wide, 8px radius, 1px border, 16px overlay inset, clicks on the mask
 *     still close it.
 */

/* global console, document, getComputedStyle, localStorage, process, window */

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

const baseURL = process.env.PAN_E2E_BASE_URL || 'http://127.0.0.1:8791';
const runtime = process.env.PAN_E2E_RUNTIME || path.resolve('test-results/pan-e2e-runtime');
const artifacts = path.join(runtime, 'artifacts');
await fs.mkdir(artifacts, { recursive: true });

const SESSION_NAME = 'Alpha Session';
const MOBILE = { width: 390, height: 844 };
const MOBILE_SHORT = { width: 390, height: 480 };
const DESKTOP = { width: 1440, height: 900 };
const LG_MAX_WIDTH = 42 * 16;

const browser = await chromium.launch({ headless: true });
const results = [];

async function openApp(contextOptions) {
  const context = await browser.newContext({ deviceScaleFactor: 1, ...contextOptions });
  const page = await context.newPage();
  await page.goto(`${baseURL}/react/`);
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  return { context, page };
}

async function openDetails(page, { mobile }) {
  if (mobile) {
    // The hamburger only exists while the drawer is closed; after a previous
    // open/close cycle the drawer may still be on screen.
    const hamburger = page.locator('button[title="Toggle sidebar"]');
    if (await hamburger.count()) {
      await hamburger.click({ timeout: 2000 }).catch(() => {});
    }
  }
  const card = page.locator('[data-session-card-id]').filter({ hasText: SESSION_NAME }).first();
  await card.waitFor({ state: 'visible' });
  await card.locator('button[title="Session actions"]').click();
  await page.getByRole('button', { name: 'Details', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Session Details' });
  await dialog.waitFor({ state: 'visible' });
  return dialog;
}

/** Everything the geometry assertions need, read in one round trip. */
function readGeometry(page) {
  return page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"][aria-modal="true"]');
    if (!dialog) throw new Error('Session Details dialog is not rendered');
    const overlay = dialog.parentElement;
    const [header, body] = dialog.children;
    const close = dialog.querySelector('button[aria-label="Close"]');
    const rect = (element) => {
      const box = element.getBoundingClientRect();
      return { x: box.x, y: box.y, width: box.width, height: box.height, right: box.right, bottom: box.bottom };
    };
    const dialogStyle = getComputedStyle(dialog);
    const bodyStyle = getComputedStyle(body);
    return {
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      heading: header.querySelector('h2')?.textContent,
      dialog: rect(dialog),
      header: rect(header),
      close: rect(close),
      body: {
        ...rect(body),
        scrollHeight: body.scrollHeight,
        clientHeight: body.clientHeight,
        overflowY: bodyStyle.overflowY,
      },
      overlay: { ...rect(overlay), padding: getComputedStyle(overlay).padding },
      radius: dialogStyle.borderTopLeftRadius,
      borderWidth: dialogStyle.borderTopWidth,
      documentScrollHeight: document.documentElement.scrollHeight,
      bodyScrollHeight: document.body.scrollHeight,
    };
  });
}

/** Scrolls the dialog body and reports how far the title row moved. */
function scrollDialogBody(page, offset) {
  return page.evaluate((top) => {
    const dialog = document.querySelector('[role="dialog"][aria-modal="true"]');
    const [header, body] = dialog.children;
    const before = header.getBoundingClientRect().y;
    body.scrollTop = top;
    return { headerBefore: before, headerAfter: header.getBoundingClientRect().y, scrollTop: body.scrollTop };
  }, offset);
}

/** Page-level scroll state: the document must never scroll behind the modal. */
function readDocumentState(page) {
  return page.evaluate(() => {
    window.scrollTo(0, 400);
    const state = {
      innerHeight: window.innerHeight,
      documentScrollHeight: document.documentElement.scrollHeight,
      bodyScrollHeight: document.body.scrollHeight,
      documentScrollTop: document.documentElement.scrollTop,
      bodyScrollTop: document.body.scrollTop,
      documentOverflow: getComputedStyle(document.documentElement).overflow,
      bodyOverflow: getComputedStyle(document.body).overflow,
    };
    window.scrollTo(0, 0);
    return state;
  });
}

/**
 * `overflow: hidden` boxes can still be scrolled programmatically, so the probe
 * is a real wheel gesture over the dialog plus the computed overflow that has to
 * block it. Both must leave the page behind the modal untouched.
 */
async function assertNoScrollBleed(page, label) {
  const state = await readDocumentState(page);
  assert.equal(state.documentScrollTop, 0, `${label}: window.scrollTo moved the document`);
  assert.equal(state.bodyScrollTop, 0, `${label}: the page body is scrolled`);
  assert.equal(state.documentOverflow, 'hidden', `${label}: <html> overflow`);
  assert.equal(state.bodyOverflow, 'hidden', `${label}: <body> overflow`);

  const box = await page.locator('[role="dialog"][aria-modal="true"]').boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + 20);
  await page.mouse.wheel(0, 300);
  const afterWheel = await page.evaluate(() => ({ document: document.documentElement.scrollTop, body: document.body.scrollTop }));
  assert.equal(afterWheel.document, 0, `${label}: wheel scrolled the document behind the modal`);
  assert.equal(afterWheel.body, 0, `${label}: wheel scrolled the body behind the modal`);
}

function assertFullscreenDialog(geometry, viewport, label) {
  assert.ok(Math.abs(geometry.dialog.x) <= 0.5, `${label}: dialog x=${geometry.dialog.x}`);
  assert.ok(Math.abs(geometry.dialog.y) <= 0.5, `${label}: dialog y=${geometry.dialog.y}`);
  assert.ok(Math.abs(geometry.dialog.width - viewport.width) <= 0.5, `${label}: dialog width=${geometry.dialog.width}`);
  assert.ok(Math.abs(geometry.dialog.height - viewport.height) <= 0.5, `${label}: dialog height=${geometry.dialog.height}`);
  assert.equal(geometry.radius, '0px', `${label}: window radius must be removed`);
  assert.equal(geometry.borderWidth, '0px', `${label}: window border must be removed`);
  assert.equal(geometry.overlay.padding, '0px', `${label}: mask must leave no visible inset`);
  assert.equal(geometry.overlay.width, viewport.width, `${label}: mask width`);
  // Title row + close button pinned to the top, fully inside the viewport.
  assert.equal(geometry.heading, 'Session Details', `${label}: dialog title`);
  assert.ok(Math.abs(geometry.header.y - geometry.dialog.y) <= 0.5, `${label}: header y=${geometry.header.y}`);
  assert.ok(geometry.close.y >= 0 && geometry.close.bottom <= geometry.header.bottom, `${label}: close button clipped`);
  // The scrollable region is the middle band, so the title row cannot scroll away.
  assert.equal(geometry.body.overflowY, 'auto', `${label}: content region must scroll`);
  assert.ok(Math.abs(geometry.body.y - geometry.header.bottom) <= 0.5, `${label}: content overlaps the header`);
  assert.ok(geometry.body.bottom <= viewport.height + 0.5, `${label}: content extends past the viewport`);
  assert.ok(geometry.body.scrollHeight >= geometry.body.clientHeight, `${label}: content region has no scroll range`);
}

async function runMobileCase() {
  const name = 'mobile 390x844 fullscreen session details';
  const started = new Date().toISOString();
  let context;
  let page;
  try {
    ({ context, page } = await openApp({ viewport: MOBILE, isMobile: true, hasTouch: true, deviceScaleFactor: 2 }));
    const baseline = await readDocumentState(page);
    await openDetails(page, { mobile: true });
    // Expand both collapsible sections so the body carries realistic content.
    await page.getByRole('button', { name: /System prompt/ }).click();
    await page.getByRole('button', { name: /Usage/ }).click();

    const geometry = await readGeometry(page);
    assertFullscreenDialog(geometry, MOBILE, 'mobile');
    const opened = await readDocumentState(page);
    assert.equal(opened.documentScrollHeight, baseline.documentScrollHeight, 'mobile: opening the modal grew the document');
    assert.equal(opened.bodyScrollHeight, baseline.bodyScrollHeight, 'mobile: opening the modal grew the body');
    await assertNoScrollBleed(page, 'mobile');
    const scroll = await scrollDialogBody(page, 400);
    assert.equal(scroll.headerAfter, scroll.headerBefore, 'mobile: title row moved while scrolling content');
    await page.screenshot({ path: path.join(artifacts, 'session-details-mobile-390x844.png') });

    // Short viewport: the content band must actually scroll while the title row
    // stays pinned (the 844px case may not overflow with fixture data).
    await page.setViewportSize(MOBILE_SHORT);
    const shortGeometry = await readGeometry(page);
    assertFullscreenDialog(shortGeometry, MOBILE_SHORT, 'mobile-short');
    await assertNoScrollBleed(page, 'mobile-short');
    assert.ok(shortGeometry.body.scrollHeight > shortGeometry.body.clientHeight, 'mobile-short: content did not overflow');
    const shortScroll = await scrollDialogBody(page, 400);
    assert.ok(shortScroll.scrollTop > 0, 'mobile-short: content region did not scroll');
    assert.equal(shortScroll.headerAfter, shortScroll.headerBefore, 'mobile-short: title row moved while scrolling content');
    await page.screenshot({ path: path.join(artifacts, 'session-details-mobile-390x480.png') });
    await page.setViewportSize(MOBILE);

    // Closable: ESC, then the header close button.
    await page.keyboard.press('Escape');
    await page.getByRole('dialog', { name: 'Session Details' }).waitFor({ state: 'hidden' });
    await openDetails(page, { mobile: true });
    await page.getByRole('dialog', { name: 'Session Details' }).getByRole('button', { name: 'Close' }).click();
    await page.getByRole('dialog', { name: 'Session Details' }).waitFor({ state: 'hidden' });

    results.push({ name, status: 'passed', started, geometry: { mobile: geometry, short: shortGeometry }, screenshot: 'session-details-mobile-390x844.png' });
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    if (page) await page.screenshot({ path: path.join(artifacts, 'session-details-mobile-failure.png') }).catch(() => {});
    results.push({ name, status: 'failed', started, error: message });
  } finally {
    await context?.close();
  }
}

/**
 * 700x800 sits inside the Tailwind `max-md` band (< 768px) but outside the
 * 640px bottom-sheet CSS block: the fullscreen treatment must come from the
 * max-md classes alone here, with no residual mask inset.
 */
async function runMobileWideCase() {
  const viewport = { width: 700, height: 800 };
  const name = 'mobile-wide 700x800 fullscreen session details';
  const started = new Date().toISOString();
  let context;
  let page;
  try {
    ({ context, page } = await openApp({ viewport, isMobile: true, hasTouch: true }));
    const baseline = await readDocumentState(page);
    await openDetails(page, { mobile: true });

    const geometry = await readGeometry(page);
    assertFullscreenDialog(geometry, viewport, 'mobile-wide');
    const opened = await readDocumentState(page);
    assert.equal(opened.documentScrollHeight, baseline.documentScrollHeight, 'mobile-wide: opening the modal grew the document');
    await assertNoScrollBleed(page, 'mobile-wide');
    await page.screenshot({ path: path.join(artifacts, 'session-details-mobile-700x800.png') });

    results.push({ name, status: 'passed', started, geometry, screenshot: 'session-details-mobile-700x800.png' });
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    if (page) await page.screenshot({ path: path.join(artifacts, 'session-details-mobile-wide-failure.png') }).catch(() => {});
    results.push({ name, status: 'failed', started, error: message });
  } finally {
    await context?.close();
  }
}

async function runDesktopCase() {
  const name = 'desktop 1440x900 keeps the centered size="lg" window';
  const started = new Date().toISOString();
  let context;
  let page;
  try {
    ({ context, page } = await openApp({ viewport: DESKTOP }));
    const baseline = await readDocumentState(page);
    await openDetails(page, { mobile: false });

    const geometry = await readGeometry(page);
    // Pre-existing desktop geometry: p-4 mask inset, size="lg" = 42rem card,
    // rounded-lg = 8px, 1px border, centered both axes.
    assert.equal(geometry.overlay.padding, '16px', 'desktop: mobile p-0 leaked into the desktop mask');
    assert.equal(geometry.dialog.width, LG_MAX_WIDTH, 'desktop: card width changed');
    assert.equal(geometry.dialog.x, (DESKTOP.width - LG_MAX_WIDTH) / 2, 'desktop: card is not horizontally centered');
    assert.equal(geometry.radius, '8px', 'desktop: card radius changed');
    assert.equal(geometry.borderWidth, '1px', 'desktop: card border changed');
    assert.ok(geometry.dialog.height < geometry.innerHeight - 100, `desktop: card filled the viewport (height=${geometry.dialog.height})`);
    assert.ok(Math.abs(geometry.dialog.y - (geometry.innerHeight - geometry.dialog.height) / 2) <= 1, 'desktop: card is not vertically centered');
    const opened = await readDocumentState(page);
    assert.equal(opened.documentScrollHeight, baseline.documentScrollHeight, 'desktop: opening the modal grew the document');
    await assertNoScrollBleed(page, 'desktop');
    await page.screenshot({ path: path.join(artifacts, 'session-details-desktop-1440x900.png') });

    // The visible mask ring is still clickable.
    await page.mouse.click(60, Math.round(geometry.innerHeight / 2));
    await page.getByRole('dialog', { name: 'Session Details' }).waitFor({ state: 'hidden' });

    results.push({ name, status: 'passed', started, geometry, screenshot: 'session-details-desktop-1440x900.png' });
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    if (page) await page.screenshot({ path: path.join(artifacts, 'session-details-desktop-failure.png') }).catch(() => {});
    results.push({ name, status: 'failed', started, error: message });
  } finally {
    await context?.close();
  }
}

await runMobileCase();
await runMobileWideCase();
await runDesktopCase();
await browser.close();

await fs.writeFile(path.join(runtime, 'session-details-browser-results.json'), JSON.stringify({ baseURL, results }, null, 2));
for (const result of results) {
  console.log(`${result.status.toUpperCase()} ${result.name}`);
  if (result.error) console.error(result.error);
}
if (results.some((result) => result.status === 'failed')) process.exit(1);
