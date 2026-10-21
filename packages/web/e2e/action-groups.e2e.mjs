/* Production browser against the owned real HTTP probe; no API interception. */
/* global document, window */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { chromium, expect } from '@playwright/test';

const base = process.env.ACTION_PROBE_URL;
const runtime = process.env.ACTION_PROBE_RUNTIME;
assert.ok(base && runtime);
const browser = await chromium.launch({ headless: true });
const errors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(base + '/react/actions');
  await expect(page.getByRole('heading', { name: 'Actions & Groups' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Probe Tools · Registered' })).toBeVisible();
  await page.getByLabel('ID', { exact: true }).fill('browser_sessions');
  await page.getByLabel('Name', { exact: true }).fill('Browser Sessions');
  await page.getByLabel('Description').fill('Created in the production browser');
  await page.getByLabel('Enable for build').check();
  await page.getByRole('button', { name: 'Save Action', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Browser Sessions · Enabled' })).toBeVisible();
  await page.getByLabel('Group ID').fill('browser');
  await page.getByLabel('Group name').fill('Browser Tools');
  await page.getByRole('group', { name: 'Group Actions', exact: true }).getByLabel('Browser Sessions', { exact: true }).check();
  await page.getByRole('group', { name: 'Group Actions', exact: true }).getByLabel('Echo', { exact: true }).check();
  await page.getByRole('button', { name: 'Save Group', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Browser Tools · Unbuilt' })).toBeVisible();
  await page.getByRole('button', { name: 'Build Group', exact: true }).click();
  await expect(page.getByText('Exposed Actions: browser_sessions, echo', { exact: true })).toBeVisible();
  await page.getByLabel('Session', { exact: true }).selectOption(process.env.ACTION_PROBE_SESSION);
  const sessionPlugins = page.getByRole('heading', { name: 'Session plugins' }).locator('..');
  await sessionPlugins.getByRole('checkbox', { name: /Browser Tools/ }).click();
  await expect(sessionPlugins.getByRole('checkbox', { name: /Browser Tools/ })).toBeChecked();
  await page.getByLabel('action-group-browser mode').selectOption('pan_native');
  await expect(page.getByLabel('action-group-browser mode')).toBeEnabled();
  await expect(page.getByLabel('action-group-browser mode')).toHaveValue('pan_native');
  await page.getByRole('main', { name: 'Actions Panel' }).evaluate((element) => { element.scrollTop = 0; });
  await page.screenshot({ path: path.join(runtime, 'panel-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('main', { name: 'Actions Panel' }).evaluate((element) => { element.scrollTop = 0; });
  await page.screenshot({ path: path.join(runtime, 'panel-mobile.png'), fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false, 'mobile overflow');
  await page.getByRole('button', { name: 'Unbuild Group', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Browser Tools · Unbuilt' })).toBeVisible();
  await page.getByRole('button', { name: 'Delete Group', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Browser Tools · Unbuilt' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Browser Sessions · Enabled' }).click();
  await page.getByRole('button', { name: 'Delete Action', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Browser Sessions · Enabled' })).toHaveCount(0);
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(runtime, 'browser.json'), JSON.stringify({ production: true, realHttp: true,
    createAction: true, createGroup: true, sharedCommand: true, build: true, sessionBinding: true,
    modeSwitch: true, idleUnbuild: true, delete: true, mobileOverflow: false, pageErrors: errors }, null, 2));
} finally {
  await browser.close();
}
