/* Minimal real-browser proof for ChatMessages follow/measurement behavior. */
import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';

const port = Number(process.env.PAN_BOTTOM_FOLLOW_PORT || 8798);
assert.notEqual(port, 8768, 'bottom-follow E2E must not use protected port 8768');
assert.notEqual(port, 8767, 'bottom-follow E2E must use its dedicated port');
const baseURL = `http://127.0.0.1:${port}`;

async function poll(read, check, timeout = 5000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    last = await read();
    if (check(last)) return last;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`poll timeout; last=${JSON.stringify(last)}`);
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
await context.route('**://fonts.googleapis.com/**', (route) => route.abort());
await context.route('**://fonts.gstatic.com/**', (route) => route.abort());
const page = await context.newPage();

try {
  await page.goto(`${baseURL}/react/`);
  await page.locator('[data-session-card-id]').filter({ hasText: 'Chat Stream' }).first().click();
  await page.getByRole('heading', { name: 'Browser file-link fixtures' }).waitFor({ state: 'visible' });
  const scroller = page.locator('main div.overflow-auto').first();
  const metrics = () => scroller.evaluate((el) => ({
    top: el.scrollTop,
    height: el.scrollHeight,
    viewport: el.clientHeight,
    distance: el.scrollHeight - el.scrollTop - el.clientHeight,
  }));
  await poll(metrics, (value) => value.height > value.viewport && value.distance <= 1);

  const sessionId = await page.locator('[data-session-card-id]').filter({ hasText: 'Chat Stream' }).first().getAttribute('data-session-card-id');
  assert.ok(sessionId);
  const stream = async (itemId, content) => {
    const response = await page.request.post(`${baseURL}/__e2e/stream`, {
      data: {
        sessionId,
        event: { type: 'assistant', role: 'assistant', content, item_id: itemId, delta: true },
      },
    });
    assert.equal(response.status(), 200);
  };

  await stream('bottom-follow-growth', `browser stream\n${'growth line\n'.repeat(36)}`);
  await page.getByText('browser stream', { exact: false }).first().waitFor({ state: 'visible' });
  const pinnedAfterGrowth = await poll(metrics, (value) => value.distance <= 1);

  // Real wheel input opts out. The following stream must grow below the
  // reader instead of pulling the reader back to the tail.
  await scroller.hover();
  await page.mouse.wheel(0, -700);
  const awayBefore = await poll(metrics, (value) => value.distance > 100);
  await stream('away-from-bottom', `reader stays here\n${'new tail line\n'.repeat(48)}`);
  await page.getByText('reader stays here', { exact: false }).first().waitFor({ state: 'visible' });
  const awayAfter = await page.waitForFunction(
    (element) => ({
      top: element.scrollTop,
      distance: element.scrollHeight - element.scrollTop - element.clientHeight,
    }),
    await scroller.elementHandle(),
  ).then((handle) => handle.jsonValue());
  assert.ok(awayAfter.distance > awayBefore.distance, `user reader was pulled toward tail: ${JSON.stringify({ awayBefore, awayAfter })}`);
  assert.ok(awayAfter.distance > 100);

  await page.getByTitle('Scroll to bottom').click();
  await poll(metrics, (value) => value.distance <= 1);
  await stream('pinned-final', `final block\n${'final line\n'.repeat(72)}`);
  const pinnedAfterFinal = await poll(metrics, (value) => value.distance <= 1);

  console.log(JSON.stringify({
    port,
    initialAndGrowth: pinnedAfterGrowth,
    awayBefore,
    awayAfter,
    final: pinnedAfterFinal,
  }, null, 2));
} finally {
  await context.close();
  await browser.close();
}
