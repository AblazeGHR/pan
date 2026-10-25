/* global URL, fetch, document, console, process */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, unlinkSync, writeFileSync } from 'node:fs';
import net from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

// Isolated fixture only: Vite on 5215 with every API call fulfilled in-process.
// This script never contacts Pan's practical services (8767/8768).
const port = 5215;
const harnessName = '.locker-desktop-notify-harness';
const htmlPath = new URL(`../src/${harnessName}.html`, import.meta.url);
const tsxPath = new URL(`../src/${harnessName}.tsx`, import.meta.url);
const baseURL = `http://127.0.0.1:${port}/${harnessName}.html`;
const cwd = new URL('..', import.meta.url).pathname.replace(/^\/(\w:)/, '$1').replaceAll('/', '\\');
const outDir = fileURLToPath(new URL('../evidence/locker-desktop-notify/', import.meta.url));

await new Promise((resolve, reject) => {
  const server = net.createServer();
  server.once('error', reject);
  server.listen(port, '127.0.0.1', () => server.close(resolve));
});

writeFileSync(
  htmlPath,
  `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1.0" /></head><body><div id="root"></div><script type="module" src="/${harnessName}.tsx"></script></body></html>`,
);
writeFileSync(
  tsxPath,
  `import { useState } from 'react';\nimport { createRoot } from 'react-dom/client';\nimport { AppSettingsModal } from './components/layout/AppSettingsModal';\nimport { useAppSettingsStore, DEFAULT_SETTINGS } from './stores/appSettingsStore';\nimport './index.css';\nuseAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });\nfunction Harness() {\n  const [open, setOpen] = useState(true);\n  return <AppSettingsModal open={open} onClose={() => setOpen(false)} />;\n}\ncreateRoot(document.getElementById('root')!).render(<Harness />);`,
);

const server = spawn(
  process.execPath,
  [
    fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url)),
    '--host',
    '127.0.0.1',
    '--port',
    String(port),
    '--strictPort',
  ],
  { cwd, windowsHide: true, stdio: 'ignore' },
);

let browser;

try {
  let ready = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (server.exitCode !== null) throw new Error(`Vite exited with code ${server.exitCode}`);
    try {
      const response = await fetch(baseURL);
      if (response.ok) {
        ready = true;
        break;
      }
    } catch {
      // Vite is still starting.
    }
    await delay(250);
  }
  assert.ok(ready, `Vite did not become ready at ${baseURL}`);

  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/settings/session-lifecycle') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ exitStrategy: 'ask', startupPreference: 'ask', wakeMessage: '继续' }),
      });
    }
    if (path === '/api/main/restart/status') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ available: true, pending: false, platform: 'nt' }) });
    }
    if (path === '/api/main/exit/status') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ available: true, pending: false, platform: 'nt', stage: 'idle' }) });
    }
    if (path === '/api/remote/status') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ available: false, enabled: false, running: false }) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  const openNotifications = async () => {
    await page.getByRole('tab', { name: 'Notifications' }).click();
    const section = page.getByTestId('locker-desktop-notify-section');
    await section.scrollIntoViewIfNeeded();
    await page.waitForTimeout(250);
    return section;
  };

  await page.goto(baseURL);
  await page.getByRole('tab', { name: 'Notifications' }).waitFor();

  mkdirSync(outDir, { recursive: true });

  const section = await openNotifications();
  assert.equal(
    await page.getByRole('switch', { name: /Locker new messages/ }).getAttribute('aria-checked'),
    'false',
  );
  await page.screenshot({ path: `${outDir}1440-notifications.png` });

  await page.setViewportSize({ width: 390, height: 844 });
  await openNotifications();
  await page.screenshot({ path: `${outDir}390-notifications.png` });

  const box = await section.boundingBox();
  assert.ok(box && box.width > 0, 'Locker section is rendered');

  assert.deepEqual(pageErrors, [], `page errors: ${pageErrors.join('; ')}`);
  assert.deepEqual(consoleErrors, [], `console errors: ${consoleErrors.join('; ')}`);
  console.log(JSON.stringify({
    screenshots: [`${outDir}1440-notifications.png`, `${outDir}390-notifications.png`],
    section: { width: Math.round(box.width), height: Math.round(box.height) },
    pageErrors,
    consoleErrors,
  }));
} finally {
  await browser?.close();
  server.kill();
  for (const path of [htmlPath, tsxPath]) if (existsSync(path)) unlinkSync(path);
}
