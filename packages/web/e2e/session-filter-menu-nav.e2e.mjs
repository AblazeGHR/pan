/* global document, localStorage, URL, process, console */
// Isolated production HTTP/Chromium regression for the Session filter popover
// placement:
//   - the panel must stay visible and inside the viewport (desktop + narrow),
//   - its right edge must not reach the WorkspaceRail navigation column,
//   - the rail must remain the topmost element at its own hit point (operable)
//     while the filter is open,
//   - the page must not gain horizontal overflow.
//
// Serves the built production bundle on a loopback-only random port with
// stubbed APIs; never connects to Pan, never touches data/profile/8767/8768.
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { chromium } from '@playwright/test';

const DIST = path.resolve(process.env.PAN_FILTER_NAV_DIST || 'dist');
const OUT = path.join(os.tmpdir(), 'pan-session-filter-nav');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.woff2': 'font/woff2',
};
assert.ok(fs.existsSync(path.join(DIST, 'index.html')), `missing build at ${DIST}; run pnpm build first`);
fs.mkdirSync(OUT, { recursive: true });

const sessions = ['Alpha', 'Bravo', 'Charlie'].map((name, index) => ({
  id: name.toLowerCase(),
  name,
  updatedAt: `2026-01-0${6 - index}T00:00:00.000Z`,
  adapter: 'cbc',
}));

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (url.pathname.startsWith('/api/')) {
    res.writeHead(404, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ error: 'not found' }));
  }
  const rel = url.pathname.startsWith('/react/') ? url.pathname.slice('/react/'.length) : url.pathname.replace(/^\/+/, '');
  const candidate = path.join(DIST, rel);
  const file = path.extname(candidate) ? candidate : path.join(DIST, 'index.html');
  if (!file.startsWith(DIST) || !fs.existsSync(file)) {
    res.writeHead(404);
    return res.end('not found');
  }
  res.writeHead(200, {
    'Content-Type': MIME[path.extname(file)] || 'application/octet-stream',
    'Cache-Control': 'no-store',
  });
  fs.createReadStream(file).pipe(res);
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
assert.ok(![8767, 8768].includes(port), `refusing shared port ${port}`);
const BASE = `http://127.0.0.1:${port}`;

const report = { viewports: {}, ok: false };

const browser = await chromium.launch({ headless: true });
try {
  for (const viewport of [
    { name: 'desktop', width: 1440, height: 900 },
    { name: 'narrow', width: 390, height: 844 },
  ]) {
    const context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      isMobile: viewport.width < 768,
      hasTouch: viewport.width < 768,
    });
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    await page.routeWebSocket('**/ws', (ws) => ws.onMessage(() => {}));
    await page.route('**/api/**', async (route) => {
      const url = new URL(route.request().url());
      const reply = (data, status = 200) =>
        route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
      if (url.pathname === '/api/sessions') return reply({ sessions });
      if (url.pathname === '/api/main/startup-recovery') {
        return reply({ generation: 'fixture', state: 'no_candidates', candidateSnapshot: [], decision: null, attempts: 0, results: [] });
      }
      if (url.pathname === '/api/list') return reply({ workers: [] });
      if (url.pathname === '/api/workspaces') return reply({ workspaces: [] });
      if (url.pathname === '/api/settings/ui') return reply({ showGroupBy: true, defaultGroupBy: 'none' });
      return reply({ error: 'not found' }, 404);
    });

    await page.goto(`${BASE}/react/`, { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => {
      localStorage.clear();
      localStorage.setItem('pan:railExpanded', '1');
    });
    await page.reload({ waitUntil: 'domcontentloaded' });
    if (viewport.width < 768) {
      await page.getByRole('button', { name: '打开侧边栏' }).click();
    }
    await page.locator('[data-session-card-id]').first().waitFor({ state: 'visible', timeout: 15000 });

    const trigger = page.locator('[title="Special filters"]');
    await trigger.click();
    const menu = page.getByTestId('session-filter-menu');
    await menu.waitFor({ state: 'visible' });

    const geometry = await menu.evaluate((el) => {
      const menuRect = el.getBoundingClientRect();
      const rail = document.querySelector('[data-testid="mobile-workspace-rail-collapsed"]')
        ?? document.querySelector('[data-testid="workspace-tab-scroll"]')?.closest('div[class*="self-stretch"]');
      const railRect = rail?.getBoundingClientRect() ?? null;
      return {
        menu: { left: menuRect.left, right: menuRect.right, top: menuRect.top, bottom: menuRect.bottom, width: menuRect.width, height: menuRect.height },
        rail: railRect ? { left: railRect.left, right: railRect.right, width: railRect.width } : null,
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
      };
    });
    assert.ok(geometry.menu.width > 0 && geometry.menu.height > 0, `${viewport.name}: menu invisible`);
    assert.ok(geometry.menu.left >= -0.5, `${viewport.name}: menu overflows left: ${geometry.menu.left}`);
    assert.ok(geometry.menu.top >= -0.5, `${viewport.name}: menu overflows top: ${geometry.menu.top}`);
    assert.ok(geometry.menu.right <= geometry.innerWidth + 0.5, `${viewport.name}: menu overflows right`);
    assert.ok(geometry.menu.bottom <= viewport.height + 0.5, `${viewport.name}: menu overflows bottom`);
    assert.ok(geometry.scrollWidth <= geometry.innerWidth, `${viewport.name}: horizontal overflow`);
    if (geometry.rail && geometry.rail.width > 0) {
      assert.ok(
        geometry.menu.right <= geometry.rail.left + 0.5,
        `${viewport.name}: filter panel covers the workspace rail (${geometry.menu.right} > ${geometry.rail.left})`,
      );
    }

    // The rail must be the topmost element at its own hit point: its clickable
    // area is not covered by the open filter panel.
    const narrow = viewport.width < 768;
    const operable = await page.evaluate(() => {
      const tab = document.querySelector('[data-workspace-tab-id]')
        ?? document.querySelector('[data-testid="mobile-workspace-rail-collapsed"] button');
      if (!tab) return null;
      const rect = tab.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      return {
        inside: tab === hit || tab.contains(hit),
        hitTag: hit?.tagName ?? null,
        hitText: (hit?.textContent ?? '').slice(0, 40),
      };
    });
    assert.ok(operable, `${viewport.name}: no workspace tab`);
    assert.equal(operable.inside, true, `${viewport.name}: rail tab covered: ${JSON.stringify(operable)}`);

    // Operating the rail works while the filter stays open (no dismissal, no
    // blocked click). Narrow screens show the collapsed rail handle first.
    if (narrow) {
      await page.getByRole('button', { name: '展开工作区', exact: true }).click();
      await page.getByTestId('mobile-workspace-rail-expanded').waitFor({ state: 'visible' });
      assert.equal(await menu.isVisible(), true, `${viewport.name}: rail click closed the filter`);
    } else {
      const ungrouped = page.getByTestId('workspace-tab-ungrouped');
      await ungrouped.click();
      assert.equal(await menu.isVisible(), true, `${viewport.name}: rail click closed the filter`);
      assert.ok(
        (await ungrouped.getAttribute('class'))?.includes('text-accent'),
        `${viewport.name}: rail tab did not respond to the click`,
      );
    }
    assert.deepEqual(pageErrors, [], `${viewport.name}: page errors: ${pageErrors.join('; ')}`);

    await page.screenshot({ path: path.join(OUT, `filter-nav-${viewport.name}.png`), fullPage: false });
    report.viewports[viewport.name] = { geometry, operable };
    await context.close();
  }
  report.ok = true;
} catch (error) {
  report.error = error instanceof Error ? error.stack || error.message : String(error);
  throw error;
} finally {
  fs.writeFileSync(path.join(OUT, 'session-filter-nav.json'), `${JSON.stringify(report, null, 2)}\n`);
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
console.log(JSON.stringify(report, null, 2));
