/* global URL:readonly, console:readonly, document:readonly, getComputedStyle:readonly, innerWidth:readonly */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { chromium, expect } from '@playwright/test';

const web = path.resolve(import.meta.dirname, '..');
const output = path.join(web, 'test-results/terminal-tui-states');
await fs.mkdir(output, { recursive: true });
const port = await new Promise((resolve, reject) => {
  const probe = net.createServer();
  probe.once('error', reject);
  probe.listen(0, '127.0.0.1', () => {
    const value = probe.address().port;
    probe.close(() => resolve(value));
  });
});
assert.ok(port !== 8767 && port !== 8768);
const server = await createServer({
  configFile: false,
  root: web,
  cacheDir: path.join(output, 'vite-cache'),
  resolve: { alias: { '@': path.join(web, 'src') } },
  server: { host: '127.0.0.1', port, strictPort: true, proxy: {} },
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'terminal-state-fixture',
      configureServer(vite) {
        vite.middlewares.use('/__fixture', async (_req, res) => {
          res.setHeader('Content-Type', 'text/html');
          res.end(
            await vite.transformIndexHtml(
              '/__fixture',
              '<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/e2e/composerSendFixture.tsx"></script></body></html>',
            ),
          );
        });
      },
    },
  ],
});
await server.listen();
const browser = await chromium.launch({ headless: true });
const evidence = { port, tests: [], requests: [], errors: [], states: [] };
let active;
try {
  for (const width of [1440, 390, 320]) {
    for (const theme of ['dark', 'light']) {
      const context = await browser.newContext({ viewport: { width, height: 900 }, isMobile: width < 768, hasTouch: width < 768 });
      await context.tracing.start({ screenshots: true, snapshots: true });
      const page = await context.newPage(); active = page;
      let status = 'upgrade', mode = 'pan';
      page.on('pageerror', (error) => evidence.errors.push(String(error)));
      page.on('request', (request) => evidence.requests.push(request.url()));
      await page.route('**/api/**', async (route) => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname.includes('/native-control')) {
          await route.fulfill({ json: { ok: true, result: { workerId: 'w-s1', mode, generation: 1,
            enabled: status !== 'upgrade', pendingEnabled: status === 'upgrading' ? true : null,
            error: status === 'error' ? 'control unavailable' : null, cursor: '0', data: '', gap: false } } }); return;
        }
        if (pathname.endsWith('/queue')) { await route.fulfill({ json: { items: [], queueRevision: 0 } }); return; }
        if (pathname.endsWith('/workspaces')) { await route.fulfill({ json: { workspaces: [] } }); return; }
        if (pathname.endsWith('/draft')) { await route.fulfill({ json: { revision: 0, draft: null } }); return; }
        await route.fulfill({ json: {} });
      });
      await page.goto(`http://127.0.0.1:${port}/__fixture`);
      await page.waitForLoadState('networkidle');
      await page.evaluate((theme) => document.documentElement.dataset.theme = theme, theme);
      const button = page.getByRole('button', { name: 'Terminal TUI', exact: true });
      for (const current of ['upgrade', 'available', 'enabled', 'upgrading', 'error']) {
        status = current; mode = current === 'enabled' ? 'native' : 'pan';
        await page.evaluate(async ({ enabled, pendingEnabled, mode, error }) => {
          const { useNativeControlStore } = await import('/src/stores/nativeControlStore.ts');
          const { useSessionStore } = await import('/src/stores/sessionStore.ts');
          useSessionStore.setState((state) => ({ sessions: state.sessions.map((session) => ({ ...session, nativeControl: enabled })) }));
          useNativeControlStore.setState({ sessions: { s1: { workerId: 'w-s1', mode, generation: 1, error, enabled, pendingEnabled } }, terminalViews: { s1: mode === 'native' }, switching: {} });
        }, { enabled: current !== 'upgrade', pendingEnabled: current === 'upgrading' ? true : null, mode, error: current === 'error' ? 'control unavailable' : null });
        await expect(button).toHaveAttribute('data-terminal-status', current);
        await button.evaluate(async (element) => { await Promise.all(element.getAnimations().map((animation) => animation.finished.catch(() => {}))); });
        const metrics = await button.evaluate((element) => { const style = getComputedStyle(element); return { text: element.textContent, title: element.title, color: style.color, background: style.backgroundColor, bounds: element.getBoundingClientRect().toJSON(), scrollWidth: document.documentElement.scrollWidth, width: innerWidth }; });
        if (width < 768) {
          assert.equal(metrics.text, '');
          assert.ok(metrics.bounds.width >= 44 && metrics.bounds.height >= 44);
          assert.ok(metrics.bounds.x >= 0 && metrics.bounds.right <= width);
          assert.ok(metrics.scrollWidth <= width);
        } else assert.ok(metrics.text.includes(current === 'upgrade' ? 'Upgrades' : 'Terminal TUI'));
        assert.ok(metrics.title.length > 0);
        assert.equal(await button.locator('.terminal-tui-entry__upgrade-arrow').count(), current === 'upgrade' ? 1 : 0);
        assert.equal(await button.getAttribute('aria-pressed'), current === 'enabled' ? 'true' : 'false');
        if (current === 'upgrading') await expect(button).toBeDisabled(); else await expect(button).toBeEnabled();
        if (current === 'available' || current === 'enabled') assert.notEqual(metrics.background, 'rgba(0, 0, 0, 0)');
        evidence.states.push({ width, theme, status: current, ...metrics });
        await page.screenshot({ path: path.join(output, `${width}-${theme}-${current}.png`) });
      }
      const values = evidence.states.filter((state) => state.width === width && state.theme === theme);
      assert.equal(values.find((state) => state.status === 'available').color, values.find((state) => state.status === 'enabled').color);
      assert.notEqual(values.find((state) => state.status === 'available').color, values.find((state) => state.status === 'upgrade').color);
      evidence.tests.push(`${width}px ${theme}: upgrade/available/enabled/upgrading/error, mobile icon-only+44px touch bounds, desktop labels, theme colors, aria/title`);
      await context.tracing.stop({ path: path.join(output, `${width}-${theme}-trace.zip`) });
      await context.close();
    }
  }
  assert.deepEqual(evidence.errors, []);
  assert.equal(evidence.requests.some((url) => /:(8767|8768)(\/|$)/.test(url)), false);
  console.log(JSON.stringify(evidence.tests));
} catch (error) { evidence.failure = String(error.stack || error); evidence.body = await active?.locator('body').innerText(); await active?.screenshot({ path: path.join(output, 'failure.png') }); throw error; }
finally { await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(evidence, null, 2)); await browser.close(); await server.close(); }
