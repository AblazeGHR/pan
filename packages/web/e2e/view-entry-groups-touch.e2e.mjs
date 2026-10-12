/* global Element, EventTarget, URL, console, document, fetch, process, window */
// Real-browser touch reproduction for the mobile ViewEntryGroups swipe bug.
//
// The app boots through the REAL entry (main.tsx + App layout + mobile drawer +
// Sidebar + ViewEntryGroups) in built-in mock mode (?mock=1). Gestures are
// injected through CDP Input.dispatchTouchEvent — the browser's actual input
// pipeline, so touch-action/pointercancel/native drag all apply exactly like a
// real finger. Synthetic DOM pointer events are never used as evidence.
//
// All traffic stays local: WebSocket is stubbed, /api is client-mocked (plus a
// Playwright safety net), and the Vite dev server runs with proxy disabled on
// an isolated port with an isolated cacheDir.
import assert from 'node:assert/strict';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';

const cwd = fileURLToPath(new URL('..', import.meta.url));
const port = 5231;
const baseURL = `http://127.0.0.1:${port}/?mock=1`;

await new Promise((resolve, reject) => {
  const server = net.createServer();
  server.once('error', reject);
  server.listen(port, '127.0.0.1', () => server.close(resolve));
});

// Real project config reused; only cacheDir/port/proxy are isolated.
const server = await createServer({
  configFile: path.join(cwd, 'vite.config.ts'),
  cacheDir: path.join(cwd, 'test-results/view-entry-touch/vite-cache'),
  server: { host: '127.0.0.1', port, strictPort: true, proxy: {} },
});
await server.listen();

let browser;
try {
  let ready = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(baseURL);
      if (response.ok) { ready = true; break; }
    } catch { /* vite warming up */ }
    await delay(250);
  }
  assert.ok(ready, `vite did not become ready at ${baseURL}`);

  browser = await chromium.launch({ headless: true });

  const openRealApp = async (width) => {
    const context = await browser.newContext({
      viewport: { width, height: 844 },
      deviceScaleFactor: 2,
      isMobile: true,
      hasTouch: true,
    });
    const page = await context.newPage();
    const pageErrors = [];
    const consoleErrors = [];
    const protectedRequests = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
    page.on('request', (request) => {
      if (/:(8767|8768)(\/|$)/.test(request.url())) protectedRequests.push(request.url());
    });
    // Safety net: nothing may reach a real Pan instance even if a request
    // escapes the client-side mock.
    await page.route('**/api/**', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, result: {} }) }));
    // No /ws connection may ever leave the page (the dev proxy is disabled too).
    await page.addInitScript(() => {
      class StubWebSocket extends EventTarget {
        static OPEN = 1; static CLOSED = 3;
        readyState = 1;
        send() {}
        close() {}
      }
      window.WebSocket = StubWebSocket;
      // Diagnostic pointer log (capture phase, page-wide).
      window.__pointerLog = [];
      for (const type of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'lostpointercapture', 'click']) {
        document.addEventListener(type, (event) => {
          const log = window.__pointerLog;
          if (!log || log.length > 400) return;
          const target = event.target instanceof Element
            ? `${event.target.tagName.toLowerCase()}${event.target.getAttribute('aria-label') ? `(${event.target.getAttribute('aria-label')})` : ''}`
            : String(event.target);
          log.push({
            type,
            pointerType: event.pointerType ?? null,
            pointerId: event.pointerId ?? null,
            isPrimary: event.isPrimary ?? null,
            button: event.button ?? null,
            x: Math.round(event.clientX ?? -1),
            y: Math.round(event.clientY ?? -1),
            cancelable: event.cancelable ?? null,
            target,
          });
        }, true);
      }
    });
    await page.goto(baseURL, { waitUntil: 'networkidle' });
    return { context, page, pageErrors, consoleErrors, protectedRequests };
  };

  const openMobileDrawer = async (page) => {
    const hamburger = page.getByRole('button', { name: '打开侧边栏' });
    await hamburger.waitFor({ state: 'visible', timeout: 15000 });
    await hamburger.click();
    // Drawer is a 200ms transform transition; the entry group becomes visible.
    const groupBox = page.locator('div[aria-label^="界面入口，第"]');
    await groupBox.waitFor({ state: 'visible', timeout: 5000 });
    await page.waitForTimeout(350);
    return groupBox;
  };

  const groupLabel = (page) =>
    page.locator('div[aria-label^="界面入口，第"]').getAttribute('aria-label');

  // Real touch drag through the browser input pipeline (CDP), optionally with
  // a vertical drift like a real finger.
  const touchSwipe = async (page, { x, y, dx, dy = 0, steps = 8, stepDelay = 16 }) => {
    const cdp = await page.context().newCDPSession(page);
    const points = [];
    for (let i = 1; i <= steps; i += 1) {
      points.push({ x: x + (dx * i) / steps, y: y + (dy * i) / steps });
    }
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [{ x, y, id: 1, radiusX: 2, radiusY: 2, force: 1 }],
    });
    for (const point of points) {
      await cdp.send('Input.dispatchTouchEvent', {
        type: 'touchMove',
        touchPoints: [{ x: point.x, y: point.y, id: 1, radiusX: 2, radiusY: 2, force: 1 }],
      });
      if (stepDelay) await delay(stepDelay);
    }
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await cdp.detach();
    await page.waitForTimeout(300);
  };

  const swipeEntryRow = async (page, dx, drift = 0, fraction = 0.85) => {
    const box = await page.locator('div[aria-label^="界面入口，第"]').boundingBox();
    assert.ok(box, 'entry group container box');
    const startX = box.x + box.width * fraction;
    const endDx = dx > 0 ? Math.min(dx, box.x + box.width - startX - 2) : dx;
    await touchSwipe(page, {
      x: startX,
      y: box.y + box.height / 2,
      dx: endDx,
      dy: drift,
    });
  };

  // ── Mobile 320 & 390 ──────────────────────────────────────────────
  for (const width of [320, 390]) {
    const { context, page, pageErrors, consoleErrors, protectedRequests } = await openRealApp(width);
    await openMobileDrawer(page);
    const initial = await groupLabel(page);
    assert.match(initial, /第 1 组/, `${width}px should start on group 1 (Chat/Editor/Jobs)`);

    // Left swipe (with realistic finger drift) must reach the Terminal group.
    await swipeEntryRow(page, -(width * 0.6), Math.round(width * 0.03));
    if (process.env.PAN_DEBUG_POINTER === '1') {
      console.log(JSON.stringify({ width, label: await groupLabel(page), pointerLog: await page.evaluate(() => window.__pointerLog) }, null, 1));
    }
    const afterLeft = await groupLabel(page);
    const terminalVisible = await page.getByRole('link', { name: 'Terminal' }).isVisible().catch(() => false);
    const urlAfterLeft = new URL(page.url()).pathname;
    assert.match(afterLeft, /第 2 组/, `${width}px left swipe must enter the Terminal group`);
    assert.ok(terminalVisible, `${width}px Terminal entry must be visible after the swipe`);
    assert.equal(urlAfterLeft, '/', `${width}px switching groups must not navigate`);

    // A large fling may only move one group (clamped at the last group).
    await swipeEntryRow(page, -(width * 1.2), Math.round(width * 0.02));
    assert.match(await groupLabel(page), /第 2 组/, `${width}px oversized swipe must stay on the last group`);

    // Right swipe returns to group 1.
    await swipeEntryRow(page, width * 0.6, Math.round(width * 0.03), 0.15);
    assert.match(await groupLabel(page), /第 1 组/, `${width}px right swipe must return to group 1`);

    // Vertical scroll gesture must not switch groups.
    const box = await page.locator('div[aria-label^="界面入口，第"]').boundingBox();
    await touchSwipe(page, { x: box.x + box.width / 2, y: box.y + box.height / 2, dx: Math.round(width * 0.05), dy: 200 });
    assert.match(await groupLabel(page), /第 1 组/, `${width}px vertical gesture must not switch groups`);
    assert.equal(new URL(page.url()).pathname, '/', `${width}px gestures must never navigate`);

    // Short tap on Terminal still navigates to its original route.
    await swipeEntryRow(page, -(width * 0.6), Math.round(width * 0.03));
    assert.match(await groupLabel(page), /第 2 组/, `${width}px left swipe (second time) must enter the Terminal group`);
    // The swipe sets a 400ms trailing-click suppression window; a genuine tap
    // after it expires must navigate.
    await page.waitForTimeout(500);
    await page.getByRole('link', { name: 'Terminal' }).click();
    await page.waitForURL('**/terminals', { timeout: 5000 });
    assert.equal(new URL(page.url()).pathname, '/terminals', `${width}px tapping Terminal must navigate`);

    assert.deepEqual(protectedRequests, [], `${width}px protected ports were requested`);
    assert.deepEqual(pageErrors, [], `${width}px page errors`);
    assert.deepEqual(consoleErrors, [], `${width}px console errors`);
    const pointerLog = process.env.PAN_DEBUG_POINTER === '1'
      ? await page.evaluate(() => window.__pointerLog)
      : await page.evaluate(() => window.__pointerLog.map((entry) => entry.type));
    console.log(JSON.stringify({ width, mobile: 'ok', pointerTypes: pointerLog }));
    await context.close();
  }

  // ── Desktop regression: arrows, wheel, keyboard ───────────────────
  {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    await page.route('**/api/**', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, result: {} }) }));
    await page.addInitScript(() => {
      class StubWebSocket extends EventTarget {
        static OPEN = 1; static CLOSED = 3;
        readyState = 1;
        send() {}
        close() {}
      }
      window.WebSocket = StubWebSocket;
    });
    await page.goto(`http://127.0.0.1:${port}/?mock=1`, { waitUntil: 'networkidle' });

    const groupBox = page.locator('div[aria-label^="界面入口，第"]');
    await groupBox.waitFor({ state: 'visible', timeout: 15000 });
    assert.match(await groupBox.getAttribute('aria-label'), /第 1 组/, 'desktop starts on group 1');
    // No arrows on mobile; arrows on desktop.
    await page.getByRole('button', { name: '下一组界面入口' }).waitFor({ state: 'visible' });

    // Wheel over the entries switches one group per burst.
    const box = await groupBox.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.wheel(0, 120);
    await page.waitForTimeout(250);
    assert.match(await groupBox.getAttribute('aria-label'), /第 2 组/, 'desktop wheel down enters Terminal group');
    assert.equal(new URL(page.url()).pathname, '/', 'desktop wheel must not navigate');
    await page.mouse.wheel(0, -120);
    await page.waitForTimeout(250);
    assert.match(await groupBox.getAttribute('aria-label'), /第 1 组/, 'desktop wheel up returns');

    // Keyboard arrows.
    await groupBox.focus();
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(100);
    assert.match(await groupBox.getAttribute('aria-label'), /第 2 组/, 'ArrowRight enters Terminal group');
    await page.keyboard.press('ArrowLeft');
    await page.waitForTimeout(100);
    assert.match(await groupBox.getAttribute('aria-label'), /第 1 组/, 'ArrowLeft returns');

    // Click navigation still works on desktop (Terminal lives in group 2).
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(250);
    await page.getByRole('link', { name: 'Terminal' }).click();
    await page.waitForURL('**/terminals', { timeout: 5000 });

    assert.deepEqual(pageErrors, [], 'desktop page errors');
    console.log(JSON.stringify({ width: 1280, desktop: 'ok' }));
    await context.close();
  }

  console.log('ALL PASS');
} finally {
  await browser?.close();
  await server.close();
}
