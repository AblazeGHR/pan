/* global document, process, URL, getComputedStyle, localStorage */
// Isolated Chromium regression for Session card height.
//
// Serves the production build on a loopback-only port and drives the real app.
// The fixture contains every preview-source shape the card can receive:
// missing (property omitted), null, empty string, whitespace-only string,
// normal text, and a long line. The card must keep the same height for all of
// them, while the long line still truncates to one row.
//
// Safety: loopback only; never touches 8765/8767/8768 or any Pan service.
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';

const DIST = path.resolve(process.env.PAN_SESSION_HEIGHT_DIST || 'dist');
const PORT = Number(process.env.PAN_SESSION_HEIGHT_E2E_PORT || 8791);
const BASE = `http://127.0.0.1:${PORT}`;
const ARTIFACTS = path.resolve(
  'test-results/session-card-height',
);

assert.ok(
  fs.existsSync(path.join(DIST, 'index.html')),
  `missing build at ${DIST}; run pnpm build first`,
);
for (const forbidden of [8765, 8767, 8768]) {
  assert.notEqual(PORT, forbidden, `refusing to bind the shared ${forbidden} port`);
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
};

const LONG_PREVIEW =
  'A deliberately long preview line that must stay on exactly one row and end with an ellipsis instead of wrapping onto a second row inside the Session card.';

function fixtureSession(id, name, lastMessage, updatedAt) {
  const session = {
    id,
    name,
    adapter: 'cbc',
    workerStatus: 'idle',
    lastLegalWorkerState: 'idle',
    historyTotal: 1,
    alwaysThinkingEnabled: false,
    effort: '',
    updatedAt,
  };
  if (lastMessage !== undefined) session.lastMessage = lastMessage;
  return session;
}

const sessionsState = [
  fixtureSession('missing', 'Missing preview', undefined, 600),
  fixtureSession('null', 'Null preview', null, 500),
  fixtureSession('empty', 'Empty preview', '', 400),
  fixtureSession('whitespace', 'Whitespace preview', '  \n\t  ', 300),
  fixtureSession('normal', 'Normal preview', 'Normal one-line preview', 200),
  fixtureSession('long', 'Long preview', LONG_PREVIEW, 100),
];

function json(res, code, body) {
  const payload = JSON.stringify(body);
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  res.end(payload);
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, BASE);
  const p = url.pathname;

  if (p === '/api/sessions' && url.searchParams.get('summary') === '1') {
    return json(res, 200, { sessions: sessionsState });
  }
  if (p === '/api/sessions') return json(res, 200, { sessions: sessionsState });
  if (/^\/api\/sessions\/[^/]+\/history$/.test(p)) {
    return json(res, 200, { history: [], total: 0, hasMore: false, start: 0 });
  }
  if (p === '/api/main/startup-recovery') {
    return json(res, 200, {
      generation: 'fixture',
      state: 'no_candidates',
      candidateSnapshot: [],
      decision: null,
      attempts: 0,
      results: [],
    });
  }
  if (p === '/api/list') return json(res, 200, { workers: [] });
  if (p.startsWith('/api/')) return json(res, 404, { error: 'not found' });

  const rel = p.startsWith('/react/') ? p.slice('/react/'.length) : p.replace(/^\/+/, '');
  const candidate = path.join(DIST, rel);
  const file =
    candidate.endsWith('.html') || path.extname(candidate)
      ? candidate
      : path.join(DIST, 'index.html');
  if (!file.startsWith(DIST) || !fs.existsSync(file)) {
    return json(res, 404, { error: 'not found' });
  }
  res.writeHead(200, {
    'Content-Type': MIME[path.extname(file)] || 'application/octet-stream',
    'Cache-Control': 'no-store',
  });
  fs.createReadStream(file).pipe(res);
});

await new Promise((resolve) => server.listen(PORT, '127.0.0.1', resolve));
fs.mkdirSync(ARTIFACTS, { recursive: true });

const ids = sessionsState.map((session) => session.id);
const report = { base: BASE, viewports: {}, assertions: [], ok: false };

function snapshot() {
  return Object.fromEntries(
    [...document.querySelectorAll('[data-session-card-id]')].map((card) => {
      const id = card.getAttribute('data-session-card-id');
      const rect = card.getBoundingClientRect();
      const line = card.querySelector('[data-testid="session-preview-line"]');
      const lineRect = line?.getBoundingClientRect();
      const style = line ? getComputedStyle(line) : null;
      return [
        id,
        {
          cardHeight: rect.height,
          preview: line
            ? {
                text: line.textContent,
                height: lineRect.height,
                lineHeight: style.lineHeight,
                minHeight: style.minHeight,
                whiteSpace: style.whiteSpace,
                overflow: style.overflow,
                textOverflow: style.textOverflow,
                scrollWidth: line.scrollWidth,
                clientWidth: line.clientWidth,
              }
            : null,
        },
      ];
    }),
  );
}

function assertCards(label, cards, { allowLongOverflow = true } = {}) {
  for (const id of ids) {
    assert.ok(cards[id], `${label}: card ${id} is missing`);
  }

  const heights = ids.map((id) => cards[id].cardHeight);
  const min = Math.min(...heights);
  const max = Math.max(...heights);
  assert.ok(
    max - min <= 0.5,
    `${label}: card heights differ by ${(max - min).toFixed(2)}px (${JSON.stringify(
      Object.fromEntries(ids.map((id) => [id, cards[id].cardHeight])),
    )})`,
  );

  for (const id of ids) {
    assert.ok(cards[id].preview, `${label}: card ${id} has no preview line element`);
  }

  for (const id of ['missing', 'null', 'empty', 'whitespace']) {
    assert.equal(
      cards[id].preview.text,
      '',
      `${label}: ${id} preview must stay empty (no placeholder text)`,
    );
  }
  assert.equal(cards.normal.preview.text, 'Normal one-line preview', `${label}: normal preview text`);
  assert.equal(
    cards.long.preview.text,
    `${LONG_PREVIEW.slice(0, 50)}...`,
    `${label}: long preview must keep the existing 50-character truncation`,
  );
  assert.equal(cards.long.preview.whiteSpace, 'nowrap', `${label}: long preview must stay on one row`);
  assert.equal(cards.long.preview.textOverflow, 'ellipsis', `${label}: long preview ellipsis`);
  if (allowLongOverflow) {
    assert.ok(
      cards.long.preview.scrollWidth > cards.long.preview.clientWidth,
      `${label}: fixture must actually overflow to prove truncation`,
    );
  }

  const lineHeights = ids.map((id) => cards[id].preview.height);
  const lineMin = Math.min(...lineHeights);
  const lineMax = Math.max(...lineHeights);
  assert.ok(
    lineMax - lineMin <= 0.5,
    `${label}: preview line heights differ by ${(lineMax - lineMin).toFixed(2)}px (${JSON.stringify(
      Object.fromEntries(ids.map((id) => [id, cards[id].preview.height])),
    )})`,
  );
}

async function openPage(context, viewport) {
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.goto(`${BASE}/react/`, { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => localStorage.clear());
  await page.reload({ waitUntil: 'domcontentloaded' });

  if (viewport.width < 768) {
    const hamburger = page.getByRole('button', { name: '打开侧边栏' });
    await hamburger.waitFor({ state: 'visible', timeout: 15000 });
    await hamburger.click();
  }
  await page.locator('[data-session-card-id="normal"]').waitFor({ state: 'visible', timeout: 15000 });
  return { page, pageErrors };
}

const browser = await chromium.launch({ headless: true });

try {
  for (const viewport of [
    { name: 'desktop', width: 1440, height: 900 },
    { name: 'mobile', width: 390, height: 844 },
  ]) {
    const context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      deviceScaleFactor: 1,
      isMobile: viewport.width < 768,
      hasTouch: viewport.width < 768,
    });
    const { page, pageErrors } = await openPage(context, viewport);
    try {
      let cards = await page.evaluate(snapshot);
      assertCards(`${viewport.name} initial`, cards);
      report.viewports[viewport.name] = { initial: cards };

      // Selection path: the active card gains an accent border/background but
      // must keep exactly the same box height.
      await page.locator('[data-session-card-id="empty"]').click();
      await page.waitForTimeout(250);
      cards = await page.evaluate(snapshot);
      assertCards(`${viewport.name} selected`, cards);
      assert.equal(
        report.viewports[viewport.name].initial.empty.cardHeight,
        cards.empty.cardHeight,
        `${viewport.name}: selecting the empty-preview card changed its height`,
      );
      report.viewports[viewport.name].selected = cards;

      assert.deepEqual(pageErrors, [], `${viewport.name}: page errors: ${pageErrors.join('; ')}`);
      await page.screenshot({
        path: path.join(ARTIFACTS, `session-card-height-${viewport.name}.png`),
        fullPage: false,
      });
      report.assertions.push(`${viewport.name}: all six card heights match; selection unchanged; long preview truncated`);
    } finally {
      await context.close();
    }
  }
  report.ok = true;
} catch (error) {
  report.error = error instanceof Error ? error.stack || error.message : String(error);
  throw error;
} finally {
  fs.writeFileSync(
    path.join(ARTIFACTS, 'session-card-height.json'),
    `${JSON.stringify(report, null, 2)}\n`,
  );
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
