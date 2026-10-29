import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { chromium, expect } from '@playwright/test';

const [base, output] = process.argv.slice(2);
assert.ok(base && ![8767, 8768].includes(Number(new URL(base).port)));
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await context.newPage();
const evidence = { productionBundle: true, providerCalls: 0, scenarios: [] };
try {
  const login = await context.request.post(base + '/api/automation/login', {
    data: { key: process.env.PAN_TEST_MINIMAL_KEY },
  });
  assert.equal(login.status(), 200);
  const state = await context.request.get(base + '/api/automation/session');
  assert.equal((await state.json()).authenticated, true);
  evidence.authenticated = true;
  for (const mode of ['denied', 'actual', 'unknown']) {
    const set = await context.request.post(base + '/__test/native-policy/' + (mode === 'denied' ? mode : 'actual'), {
      headers: { Origin: base }, data: {},
    });
    assert.equal(set.status(), 200);
    if (mode === 'unknown') await page.route('**/api/platform', route => route.fulfill({ status: 503, body: 'unavailable' }));
    await page.goto(base + '/react/');
    await page.getByTitle('New with settings', { exact: true }).click();
    await page.locator('#new-session-form select').first().selectOption('codex');
    const checkbox = page.getByRole('checkbox', { name: /Enable native Terminal TUI/ });
    await checkbox.waitFor();
    const info = await (await context.request.get(base + '/api/platform')).json();
    const denied = mode === 'denied' || (mode === 'actual' && info.capabilities['terminal.native'] === false);
    if (denied) await expect(checkbox).toBeDisabled();
    else await expect(checkbox).toBeEnabled();
    assert.equal(await checkbox.isDisabled(), denied);
    await expect(page.getByRole('button', { name: 'Create', exact: true })).toBeEnabled();
    if (mode === 'denied') {
      const rejected = await context.request.post(base + '/api/sessions', {
        headers: { Origin: base }, data: { name: 'native-denied', adapter: 'codex', nativeControl: true, mcpServers: [] },
      });
      assert.match((await rejected.json()).error, /unsupported_platform/);
      await page.screenshot({ path: output + '/native-denied.png' });
    }
    evidence.scenarios.push({ mode, checkboxDisabled: await checkbox.isDisabled(), serverPlatform: info.platform });
  }
  const created = await context.request.post(base + '/api/sessions', {
    headers: { Origin: base }, data: { name: 'ordinary-stream', adapter: 'codex', nativeControl: false, mcpServers: [] },
  });
  const body = await created.json();
  assert.equal(body.adapter, 'codex');
  assert.equal(body.nativeControl, false);
  evidence.ordinarySessionCreated = true;
  await fs.writeFile(output + '/browser.json', JSON.stringify(evidence, null, 2));
  console.log('PASS fresh production: auth, native false/actual/unknown, ordinary Session');
} catch (error) {
  await page.screenshot({ path: output + '/browser-failure.png' }).catch(() => {});
  await fs.writeFile(output + '/browser-failure.txt', await page.locator('body').innerText()).catch(() => {});
  throw error;
} finally {
  await browser.close();
}
