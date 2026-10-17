// Owned headless browser preflight only; this is not a native toast click.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
const require = createRequire('D:/project/Pan-main/packages/web/package.json');
const { chromium } = require('@playwright/test');
const here = path.dirname(new URL(import.meta.url).pathname.replace(/^\/(\w:)/, '$1'));
const ready = JSON.parse(fs.readFileSync(path.join(here, 'runtime-ready.json'), 'utf8'));
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(ready.origin) || [8767, 8768].includes(ready.port)) {
  throw new Error('Expected owned non-protected loopback runtime');
}
const candidates = [chromium.executablePath(),
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'];
const executablePath = candidates.find(candidate => fs.existsSync(candidate));
if (!executablePath) throw new Error('No existing browser found; no installation attempted');
const browser = await chromium.launch({ headless: true, executablePath });
try {
  const context = await browser.newContext({
    extraHTTPHeaders: { 'X-Pan-Owned-Preflight': '1' },
    viewport: { width: 1360, height: 900 },
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(ready.deepLink, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.locator('main').getByText(ready.historyMarker, { exact: false }).first().waitFor({ timeout: 20000 });
  const body = await page.locator('main').innerText();
  if (!body.includes(ready.sessionName) || !body.includes(ready.historyMarker) || body.includes('Select a session to start')) {
    throw new Error('Real React page did not open the owned Session history');
  }
  await page.screenshot({ path: path.join(here, 'runtime-session-page.png'), fullPage: false });
  const evidence = { at: new Date().toISOString(), url: page.url(), executablePath,
    sessionNameVisibleInMain: true, historyMarkerVisibleInMain: true, pageErrors: errors,
    nativeClick: false, source: 'owned headless Playwright preflight',
    screenshot: 'runtime-session-page.png' };
  fs.writeFileSync(path.join(here, 'runtime-browser.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify(evidence, null, 2));
} finally {
  await browser.close();
}
