/**
 * Real Chromium wiring check for the New Session native folder picker.
 *
 * The OS dialog is scripted by `new-session-native-directory.server.py`; the
 * route, picker singleton, gate and the whole React bundle are the product's.
 * Real native dialog behavior is proven by evidence/…/probe_native_directory.py.
 */
/* global console, fetch */
import assert from 'node:assert/strict';
import process from 'node:process';
import { chromium, expect } from '@playwright/test';

const base = process.env.PAN_E2E_BASE_URL ?? 'http://127.0.0.1:8765';
const identity = await (await fetch(`${base}/__e2e/identity`)).json();
assert.ok(identity.checkout.endsWith('new-session-native-directory-20261112'), identity);
assert.ok(![8767, 8768].includes(identity.port), identity);
const before = await (await fetch(`${base}/__probe/state`)).json();

const pickerRequests = [];
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const pageErrors = [];
page.on('pageerror', (error) => pageErrors.push(String(error)));
page.on('request', (request) => {
  if (!request.url().includes('/api/native-picker')) return;
  let body = null;
  try { body = request.postDataJSON(); } catch { body = null; }
  pickerRequests.push({ url: request.url(), method: request.method(), body });
});

const result = { identity, cases: [] };
try {
  await page.goto(`${base}/react/`, { waitUntil: 'domcontentloaded' });
  await page.getByTitle('New with settings').click();
  const dialog = page.getByRole('dialog', { name: 'New Session' });
  await dialog.waitFor({ state: 'visible' });

  const button = dialog.getByRole('button', { name: 'Browse server folders' });
  const input = dialog.locator('[data-testid="new-session-workdir-input"]');
  const status = dialog.locator('[data-testid="native-picker-feedback"]');
  await expect(button).toBeVisible();
  await expect(button).toBeEnabled();

  // 1. Pending, then the selected folder is written into the workdir field.
  await button.click();
  await expect(status).toContainText('Opening a folder dialog');
  await expect(button).toBeDisabled();
  await expect(input).toHaveValue(/picked-folder$/);
  await expect(status).toContainText('Folder selected');
  const pickedPath = await input.inputValue();
  result.cases.push({ case: 'selected', value: pickedPath });

  // 2. Cancel leaves the value exactly as it was.
  await button.click();
  await expect(status).toContainText('Native folder selection cancelled');
  await expect(input).toHaveValue(pickedPath);
  result.cases.push({ case: 'cancelled', value: pickedPath });

  // 3. Unsupported desktop is visible and manual entry keeps working.
  await button.click();
  await expect(status).toContainText('No interactive Windows desktop');
  await input.fill('D:\\manual\\workdir\\');
  await expect(input).toHaveValue('D:\\manual\\workdir\\');
  await expect(status).toHaveCount(0);
  result.cases.push({ case: 'unavailable-then-manual', value: await input.inputValue() });

  // 4. A second dialog on the same server is reported as busy.
  await button.click();
  await expect(status).toContainText('already open');
  result.cases.push({ case: 'busy', value: await input.inputValue() });

  await dialog.getByRole('button', { name: 'Close' }).click();
  await expect(dialog).toHaveCount(0);

  assert.equal(pickerRequests.length, 4, JSON.stringify(pickerRequests));
  for (const request of pickerRequests) {
    assert.equal(request.method, 'POST');
    assert.ok(request.url.endsWith('/api/native-picker'), request.url);
    assert.equal(request.body?.mode, 'directories', JSON.stringify(request.body));
  }
  const after = await (await fetch(`${base}/__probe/state`)).json();
  assert.equal(after.calls.length, 4, JSON.stringify(after.calls));
  assert.deepEqual(after.calls.map((call) => call.mode), ['directories', 'directories', 'directories', 'directories']);
  assert.deepEqual(after.calls.map((call) => call.status), ['selected', 'cancelled', 'unavailable', 'busy']);
  assert.equal(after.sessionCount, before.sessionCount, 'the picker must not create a Session');
  assert.equal(after.pickerProcessOwned, false, 'no dialog owner may be left behind');
  assert.deepEqual(pageErrors, [], 'no browser error during the picker flow');
  result.requests = pickerRequests;
  result.serverState = after;
  result.pageErrors = pageErrors;
  console.log(JSON.stringify(result, null, 2));
} finally {
  await browser.close();
}
