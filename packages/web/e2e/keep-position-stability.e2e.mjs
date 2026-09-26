import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';

const port = Number(process.env.PAN_KEEP_POSITION_PORT || 8796);
assert.ok(![8767, 8768].includes(port), 'keep-position E2E must not use protected ports');
const baseURL = `http://127.0.0.1:${port}`;

async function poll(read, check, message, timeout = 8000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    last = await read();
    if (check(last)) return last;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`${message}; last=${JSON.stringify(last)}`);
}

async function metrics(scroller) {
  return scroller.evaluate((element) => ({
    top: element.scrollTop,
    height: element.scrollHeight,
    viewport: element.clientHeight,
    distance: Math.max(0, element.scrollHeight - element.scrollTop - element.clientHeight),
  }));
}

async function visibleAnchor(scroller) {
  return scroller.evaluate((element) => {
    const viewport = element.getBoundingClientRect();
    const row = [...element.querySelectorAll('[data-message-identity]')].find((candidate) => {
      const rect = candidate.getBoundingClientRect();
      return rect.bottom > viewport.top + 1 && rect.top < viewport.bottom - 1;
    });
    if (!row) return null;
    const rect = row.getBoundingClientRect();
    return {
      identity: row.dataset.messageIdentity,
      rowKey: row.dataset.scrollAnchorKey,
      offset: rect.top - viewport.top,
      text: row.textContent?.trim().slice(0, 100) ?? '',
    };
  });
}

async function anchorByIdentity(scroller, identity) {
  return scroller.evaluate((element, targetIdentity) => {
    const node = [...element.querySelectorAll('[data-message-identity]')]
      .find((candidate) => candidate.dataset.messageIdentity === targetIdentity);
    if (!node) return null;
    const viewport = element.getBoundingClientRect();
    const rect = node.getBoundingClientRect();
    return {
      identity: targetIdentity,
      offset: rect.top - viewport.top,
      visible: rect.bottom > viewport.top && rect.top < viewport.bottom,
      text: node.textContent?.trim().slice(0, 100) ?? '',
    };
  }, identity);
}

async function selectSession(page, name, expectedText) {
  const card = page.locator('[data-session-card-id]').filter({ hasText: name }).first();
  await card.waitFor({ state: 'visible' });
  await card.click();
  await page.getByText(expectedText, { exact: false }).first().waitFor({ state: 'visible' });
}

async function setKeepPosition(page, enabled) {
  await page.getByTitle('App settings').first().click();
  const dialog = page.getByRole('dialog', { name: 'App Settings' });
  await dialog.getByRole('tab', { name: 'Appearance' }).click();
  const toggle = dialog.getByRole('switch', { name: 'Keep reading position per session' });
  const expected = String(enabled);
  if (await toggle.getAttribute('aria-checked') !== expected) {
    const saved = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === '/api/settings/ui' && response.request().method() === 'PUT';
    });
    await toggle.click();
    assert.equal((await saved).status(), 200, 'isolated UI setting persisted');
  }
  assert.equal(await toggle.getAttribute('aria-checked'), expected, `setting toggle becomes ${expected}`);
  await dialog.getByRole('button', { name: 'Close' }).click();
}

async function wheelUp(page, scroller, packets, amount = 420) {
  const box = await scroller.boundingBox();
  assert.ok(box, 'chat scroller has browser geometry');
  await page.mouse.move(box.x + Math.min(box.width - 4, 100), box.y + Math.min(box.height - 4, 100));
  for (let i = 0; i < packets; i += 1) {
    await page.mouse.wheel(0, -amount);
    await page.waitForTimeout(55);
  }
}

const browser = await chromium.launch({ headless: true });
const report = { port, chromium: browser.version(), scenarios: [] };
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  await context.route('**://fonts.googleapis.com/**', (route) => route.abort());
  await context.route('**://fonts.gstatic.com/**', (route) => route.abort());
  const page = await context.newPage();
  const historyRequests = [];
  await page.route('**/api/sessions/**/history**', async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('before') && url.searchParams.get('before') !== '0') {
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
    await route.continue();
  });
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.includes('/history')) historyRequests.push(url.href);
  });

  await page.request.put(`${baseURL}/api/settings/ui`, { data: { keepScrollOnSessionSwitch: true } });
  await page.goto(`${baseURL}/react/`);
  await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });

  const streamId = await page.locator('[data-session-card-id]').filter({ hasText: 'Chat Stream' }).first().getAttribute('data-session-card-id');
  const alphaId = await page.locator('[data-session-card-id]').filter({ hasText: 'Alpha Session' }).first().getAttribute('data-session-card-id');
  const bravoId = await page.locator('[data-session-card-id]').filter({ hasText: 'Bravo Session' }).first().getAttribute('data-session-card-id');
  assert.ok(streamId && alphaId && bravoId, 'isolated fixture sessions are listed');
  const addHistory = async (sessionId, name) => {
    const messages = Array.from({ length: 100 }, (_, index) => ({
      role: index % 2 === 0 ? 'user' : 'assistant',
      content: index === 99 ? `${name} latest marker` : `${name} history row ${index + 1} — ${'variable message text '.repeat(index % 5 + 1)}`,
    }));
    const response = await page.request.post(`${baseURL}/__e2e/append-history`, { data: { sessionId, messages } });
    assert.equal(response.status(), 200, `${name} history fixture was written`);
  };
  await addHistory(alphaId, 'Alpha');
  await addHistory(bravoId, 'Bravo');

  await selectSession(page, 'Chat Stream', 'Browser file-link fixtures');
  const scroller = page.locator('.chat-view-stage .overflow-auto').first();
  await poll(() => metrics(scroller), (value) => value.height > value.viewport && value.distance <= 2, 'initial Chat Stream reaches its real measured bottom');
  await wheelUp(page, scroller, 4, 360);
  const alphaPosition = await poll(() => visibleAnchor(scroller), Boolean, 'Chat Stream has a visible message anchor');
  const aMetricsBefore = await metrics(scroller);

  await selectSession(page, 'Alpha Session', 'Alpha latest marker');
  await poll(() => metrics(scroller), (value) => value.height > value.viewport && value.distance <= 2, 'new Alpha selection starts at latest message');
  await wheelUp(page, scroller, 3, 360);
  const bravoPosition = await poll(() => visibleAnchor(scroller), Boolean, 'Alpha has a visible message anchor');

  await selectSession(page, 'Chat Stream', 'Browser file-link fixtures');
  const aRestored = await poll(async () => {
    const current = await visibleAnchor(scroller);
    return current?.identity === alphaPosition.identity ? current : null;
  }, Boolean, 'A→B→A restores the same Chat Stream message identity');
  assert.ok(Math.abs(aRestored.offset - alphaPosition.offset) <= 2, `Chat Stream viewport offset restored: ${JSON.stringify({ alphaPosition, aRestored })}`);

  await selectSession(page, 'Alpha Session', 'Alpha latest marker');
  const bRestored = await poll(async () => {
    const current = await visibleAnchor(scroller);
    return current?.identity === bravoPosition.identity ? current : null;
  }, Boolean, 'A→B→A→B restores Alpha independently');
  assert.ok(Math.abs(bRestored.offset - bravoPosition.offset) <= 2, `Alpha viewport offset restored: ${JSON.stringify({ bravoPosition, bRestored })}`);
  report.scenarios.push({ name: 'desktop independent session anchors', alphaPosition, aMetricsBefore, aRestored, bravoPosition, bRestored });

  await setKeepPosition(page, false);
  await wheelUp(page, scroller, 2, 360);
  const offSessionAnchor = await visibleAnchor(scroller);
  await selectSession(page, 'Chat Stream', 'Browser file-link fixtures');
  const offA = await poll(() => metrics(scroller), (value) => value.height > value.viewport && value.distance <= 2, 'with the setting off, selecting a session starts at latest');
  await selectSession(page, 'Alpha Session', 'Alpha latest marker');
  const offB = await poll(() => metrics(scroller), (value) => value.height > value.viewport && value.distance <= 2, 'with the setting off, switching back also starts at latest');
  report.scenarios.push({ name: 'OFF always starts every selected session at latest', offSessionAnchor, offA, offB });
  await setKeepPosition(page, true);

  // Delay persisted-settings hydration in a fresh real browser context. A fast
  // A→B→A sequence while the switch is still unknown must adopt A's saved row
  // once the API returns the already-enabled preference.
  const hydrationContext = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  await hydrationContext.route('**://fonts.googleapis.com/**', (route) => route.abort());
  await hydrationContext.route('**://fonts.gstatic.com/**', (route) => route.abort());
  const hydrationPage = await hydrationContext.newPage();
  let settingsRequestStarted = false;
  let settingsResponseReturned = false;
  let releaseSettingsHydration;
  const settingsHydrationGate = new Promise((resolve) => { releaseSettingsHydration = resolve; });
  await hydrationPage.route('**/api/settings/ui', async (route) => {
    if (route.request().method() === 'GET') {
      settingsRequestStarted = true;
      await settingsHydrationGate;
    }
    await route.continue();
    if (route.request().method() === 'GET') settingsResponseReturned = true;
  });
  await hydrationPage.goto(`${baseURL}/react/`);
  await hydrationPage.locator('[data-session-card-id]').first().waitFor({ state: 'visible' });
  await poll(() => settingsRequestStarted, Boolean, 'settings GET is held before hydration');
  await selectSession(hydrationPage, 'Chat Stream', 'Browser file-link fixtures');
  const hydrationScroller = hydrationPage.locator('.chat-view-stage .overflow-auto').first();
  await poll(() => metrics(hydrationScroller), (value) => value.height > value.viewport && value.distance <= 2, 'hydration fixture session reaches its bottom');
  await wheelUp(hydrationPage, hydrationScroller, 3, 360);
  const hydrationAnchor = await poll(() => visibleAnchor(hydrationScroller), Boolean, 'hydration fixture has a visible anchor');
  const alphaHydrationRequest = hydrationPage.waitForRequest((request) => {
    const url = new URL(request.url());
    return url.pathname.includes(`/sessions/${alphaId}/history`) && url.searchParams.get('before') === '0';
  });
  await hydrationPage.locator('[data-session-card-id]').filter({ hasText: 'Alpha Session' }).first().click();
  await alphaHydrationRequest;
  const streamHydrationRequest = hydrationPage.waitForRequest((request) => {
    const url = new URL(request.url());
    return url.pathname.includes(`/sessions/${streamId}/history`) && url.searchParams.get('before') === '0';
  });
  await hydrationPage.locator('[data-session-card-id]').filter({ hasText: 'Chat Stream' }).first().click();
  await streamHydrationRequest;
  assert.equal(settingsResponseReturned, false, 'both session switches happened before settings hydration returned');
  releaseSettingsHydration();
  await poll(
    async () => {
      const current = await visibleAnchor(hydrationScroller);
      return current?.identity === hydrationAnchor.identity ? current : null;
    },
    Boolean,
    'delayed settings hydration restores the correct row after a fast A→B→A switch',
    10000,
  );
  const hydratedAnchor = await visibleAnchor(hydrationScroller);
  assert.ok(Math.abs((hydratedAnchor?.offset ?? Infinity) - hydrationAnchor.offset) <= 2, `delayed hydration restores the same pixel offset: ${JSON.stringify({ hydrationAnchor, hydratedAnchor })}`);
  report.scenarios.push({ name: 'fast A→B→A while settings hydration is delayed', hydrationAnchor, hydratedAnchor, settingsResponseReturned });
  await hydrationContext.close();

  await selectSession(page, 'Chat Stream', 'Browser file-link fixtures');
  await wheelUp(page, scroller, 3, 360);
  const streamAnchorBefore = await poll(() => visibleAnchor(scroller), Boolean, 'stream-growth fixture has an off-bottom anchor');
  const streamMetricsBefore = await metrics(scroller);
  const streamResponse = await page.request.post(`${baseURL}/__e2e/stream`, {
    data: {
      sessionId: streamId,
      event: {
        type: 'assistant',
        role: 'assistant',
        item_id: 'keep-position-live-growth',
        delta: true,
        content: `live growth marker\n${'stream growth line '.repeat(180)}`,
      },
    },
  });
  assert.equal(streamResponse.status(), 200, 'isolated stream event was broadcast through the real WebSocket fan-out');
  const streamAfter = await poll(async () => {
    const currentMetrics = await metrics(scroller);
    const currentAnchor = await visibleAnchor(scroller);
    return currentMetrics.height > streamMetricsBefore.height && currentAnchor
      ? { currentMetrics, currentAnchor }
      : null;
  }, Boolean, 'live stream append changes measured content height while reading above it');
  assert.equal(streamAfter.currentAnchor.identity, streamAnchorBefore.identity, 'live append keeps the visible message identity');
  assert.ok(Math.abs(streamAfter.currentAnchor.offset - streamAnchorBefore.offset) <= 2, `live append keeps the visible message offset: ${JSON.stringify({ streamAnchorBefore, streamAfter })}`);
  report.scenarios.push({ name: 'live stream growth while reading above the tail', streamAnchorBefore, streamMetricsBefore, streamAfter });

  if (await scroller.evaluate((element) => element.classList.contains('bubble-mode'))) {
    await page.getByRole('button', { name: 'Switch to TUI view' }).click();
    await poll(() => scroller.evaluate((element) => !element.classList.contains('bubble-mode')), Boolean, 'TUI presentation is active before the comparison');
  }
  const bubbleBox = await scroller.boundingBox();
  assert.ok(bubbleBox);
  await page.mouse.move(bubbleBox.x + Math.min(bubbleBox.width - 4, 90), bubbleBox.y + Math.min(bubbleBox.height - 4, 100));
  await page.mouse.wheel(0, -120);
  await page.waitForTimeout(650);
  const beforeBubble = await visibleAnchor(scroller);
  assert.ok(beforeBubble, 'a visible TUI row exists before the Bubble switch');
  await page.getByRole('button', { name: 'Switch to Bubble view' }).click();
  await poll(() => scroller.evaluate((element) => element.classList.contains('bubble-mode')), Boolean, 'Bubble presentation has mounted');
  await page.waitForTimeout(300);
  const bubbleAnchor = await anchorByIdentity(scroller, beforeBubble.identity);
  assert.equal(bubbleAnchor?.identity, beforeBubble?.identity, `switching TUI→Bubble keeps the logical reading row: ${JSON.stringify({ beforeBubble, bubbleAnchor })}`);
  assert.equal(bubbleAnchor?.visible, true, `the selected anchor remains visible after the layout change: ${JSON.stringify({ beforeBubble, bubbleAnchor })}`);
  assert.ok(Math.abs((bubbleAnchor?.offset ?? Infinity) - beforeBubble.offset) <= 2, `switching TUI→Bubble keeps the message offset: ${JSON.stringify({ beforeBubble, bubbleAnchor })}`);
  await page.getByRole('button', { name: 'Switch to TUI view' }).click();
  await poll(() => scroller.evaluate((element) => !element.classList.contains('bubble-mode')), Boolean, 'TUI presentation has mounted again');
  report.scenarios.push({ name: 'TUI/Bubble presentation change preserves the reading row', beforeBubble, bubbleAnchor });

  // Prepending is user-triggered once. Its own scroll/resize/measurement work
  // must not request another page until a new upward gesture arrives.
  const beforePageRequests = historyRequests.filter((url) => new URL(url).searchParams.get('before') !== '0').length;
  const scrollBox = await scroller.boundingBox();
  assert.ok(scrollBox, 'chat scroller has browser geometry before pagination');
  await page.mouse.move(scrollBox.x + Math.min(scrollBox.width - 4, 100), scrollBox.y + Math.min(scrollBox.height - 4, 100));
  // Deliver real wheel packets only until the first history request begins.
  // Further packets after that point would be a continued user gesture and may
  // legitimately request subsequent pages after the prepend settles.
  let paginationTriggered = false;
  for (let packet = 0; packet < 30 && !paginationTriggered; packet += 1) {
    await page.mouse.wheel(0, -700);
    await page.waitForTimeout(35);
    paginationTriggered = historyRequests.filter((url) => new URL(url).searchParams.get('before') !== '0').length > beforePageRequests;
  }
  await poll(
    () => historyRequests.filter((url) => new URL(url).searchParams.get('before') !== '0').length,
    (count) => count > beforePageRequests,
    'user scroll to top requests older history',
  );
  const anchorWhilePagePending = await visibleAnchor(scroller);
  const metricsWhilePagePending = await metrics(scroller);
  await page.waitForTimeout(1000);
  const afterOnePageRequests = historyRequests.filter((url) => new URL(url).searchParams.get('before') !== '0');
  assert.equal(afterOnePageRequests.length, beforePageRequests + 1, `one user top-scroll loads only one page: ${JSON.stringify(afterOnePageRequests)}`);
  const prependedAnchor = await visibleAnchor(scroller);
  assert.equal(prependedAnchor?.identity, anchorWhilePagePending?.identity, `prepend keeps the logical visible message: ${JSON.stringify({ anchorWhilePagePending, prependedAnchor })}`);
  assert.ok(Math.abs((prependedAnchor?.offset ?? Infinity) - (anchorWhilePagePending?.offset ?? -Infinity)) <= 2, `prepend keeps the message at the same viewport offset: ${JSON.stringify({ anchorWhilePagePending, prependedAnchor, metricsWhilePagePending, after: await metrics(scroller) })}`);
  report.scenarios.push({ name: 'one top-pagination gesture does not self-chain', beforePageRequests, afterOnePageRequests, anchorWhilePagePending, prependedAnchor, metricsWhilePagePending, metrics: await metrics(scroller) });

  // A narrow mobile-size viewport must not initiate another page by resize;
  // a distinct upward gesture at the newly exposed history boundary may.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(500);
  const narrowBefore = await visibleAnchor(scroller);
  const requestsAfterResize = historyRequests.filter((url) => new URL(url).searchParams.get('before') !== '0');
  assert.equal(requestsAfterResize.length, afterOnePageRequests.length, 'viewport resize does not start another history page');
  assert.equal(narrowBefore?.identity, prependedAnchor?.identity, `narrow viewport retains the same logical row after resize: ${JSON.stringify({ prependedAnchor, narrowBefore })}`);
  const narrowMetricsBefore = await metrics(scroller);
  const narrowBox = await scroller.boundingBox();
  assert.ok(narrowBox, 'narrow chat scroller has browser geometry');
  await page.mouse.move(narrowBox.x + Math.min(narrowBox.width - 4, 80), narrowBox.y + Math.min(narrowBox.height - 4, 80));
  let secondPageTriggered = false;
  for (let packet = 0; packet < 30 && !secondPageTriggered; packet += 1) {
    await page.mouse.wheel(0, -700);
    await page.waitForTimeout(35);
    secondPageTriggered = historyRequests.filter((url) => new URL(url).searchParams.get('before') !== '0').length > afterOnePageRequests.length;
  }
  await poll(
    () => historyRequests.filter((url) => new URL(url).searchParams.get('before') !== '0').length,
    (count) => count > afterOnePageRequests.length,
    'continued user upward input requests the next page at a narrow viewport',
  );
  const narrowPendingAnchor = await visibleAnchor(scroller);
  await page.waitForTimeout(1000);
  const narrowAfterPages = historyRequests.filter((url) => new URL(url).searchParams.get('before') !== '0');
  const narrowAfter = await visibleAnchor(scroller);
  assert.equal(narrowAfterPages.length, afterOnePageRequests.length + 1, `one narrow-viewport gesture loads one next page: ${JSON.stringify(narrowAfterPages)}`);
  assert.equal(narrowAfter?.identity, narrowPendingAnchor?.identity, `narrow-viewport prepend keeps the visible message: ${JSON.stringify({ narrowPendingAnchor, narrowAfter })}`);
  assert.ok(Math.abs((narrowAfter?.offset ?? Infinity) - (narrowPendingAnchor?.offset ?? -Infinity)) <= 2, `narrow-viewport prepend preserves offset: ${JSON.stringify({ narrowPendingAnchor, narrowAfter, narrowMetricsBefore, metrics: await metrics(scroller) })}`);
  report.scenarios.push({ name: '390px viewport resize and continued page', requestsAfterResize, narrowBefore, narrowPendingAnchor, narrowAfter, narrowAfterPages, narrowMetricsBefore, metrics: await metrics(scroller) });

  await context.close();

  console.log(JSON.stringify(report, null, 2));
} finally {
  await browser.close();
}
