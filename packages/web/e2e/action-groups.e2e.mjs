/* Production browser against an owned HTTP instance; no API interception. */
/* global document, window, navigator */
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
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: base });
  const page = await context.newPage();
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(base + '/react/actions');
  await expect(page.getByRole('heading', { name: 'Actions & Groups' })).toBeVisible();
  assert.deepEqual(await page.getByRole('tab').allTextContents(), ['Actions', 'New Action', 'Groups']);
  await expect(page.getByRole('button', { name: 'Edit Echo', exact: true })).toBeVisible();
  await expect(page.getByLabel('Session', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: path.join(runtime, 'actions-desktop.png'), fullPage: true });
  await page.getByRole('tab', { name: 'New Action' }).click();
  await expect(page.getByLabel('ID', { exact: true })).toHaveCount(0);
  await page.getByLabel('Name', { exact: true }).fill('Browser Sessions');
  await page.getByLabel('Description').fill('Created in the production browser');
  await page.getByLabel('Enable for build').check();
  await page.screenshot({ path: path.join(runtime, 'new-action-desktop.png'), fullPage: true });
  const creating = page.waitForResponse((r) => r.url().endsWith('/api/actions/actions') && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Save Action', exact: true }).click();
  const response = await creating;
  assert.equal('id' in response.request().postDataJSON().item, false);
  const actionId = (await response.json()).item.id;
  assert.match(actionId, /^[a-z][a-z0-9_]{0,31}$/);
  await expect(page.getByRole('button', { name: 'Edit Browser Sessions', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save Action' })).toBeEnabled();
  await page.getByLabel('Name', { exact: true }).fill('Browser Sessions Renamed');
  const updating = page.waitForResponse((r) => r.url().endsWith('/api/actions/actions/' + actionId) && r.request().method() === 'PUT');
  await page.getByRole('button', { name: 'Save Action' }).click();
  assert.equal((await updating).request().postDataJSON().item.id, actionId);
  await expect(page.getByRole('button', { name: 'Edit Browser Sessions Renamed' })).toBeVisible();
  await page.getByText('Details', { exact: true }).click();
  await page.getByRole('button', { name: 'Copy ID', exact: true }).click();
  await expect(page.getByText('ID copied', { exact: true })).toBeVisible();
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), actionId);

  await page.getByRole('tab', { name: 'Groups' }).click();
  await page.getByRole('button', { name: 'New Group', exact: true }).click();
  await expect(page.getByLabel('Group ID')).toHaveCount(0);
  await page.getByLabel('Group name').fill('Browser Tools');
  const choices = page.getByRole('group', { name: 'Group Actions', exact: true });
  await choices.getByLabel('Browser Sessions Renamed', { exact: false }).check();
  await choices.getByLabel('Echo', { exact: false }).check();
  const grouping = page.waitForResponse((r) => r.url().endsWith('/api/actions/groups') && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Save Group', exact: true }).click();
  const groupResponse = await grouping;
  assert.equal('id' in groupResponse.request().postDataJSON().item, false);
  const groupId = (await groupResponse.json()).item.id;
  await expect(page.getByRole('button', { name: 'Build Group', exact: true })).toBeEnabled();
  const building = page.waitForResponse((r) => r.url().endsWith('/api/actions/groups/' + groupId + '/build'));
  await page.getByRole('button', { name: 'Build Group', exact: true }).click();
  const registry = await (await building).json();
  const built = registry.groups.find((g) => g.id === groupId).build;
  assert.deepEqual(built.action_ids, [actionId, 'echo']);
  assert.ok(registry.groups.find((g) => g.id === 'probe').action_ids.includes('echo'));
  const pid = built.plugin_id;
  await expect(page.getByRole('button', { name: 'Save Group' })).toBeEnabled();
  await page.getByLabel('Group name').fill('Browser Tools Renamed');
  const renaming = page.waitForResponse((r) => r.url().endsWith('/api/actions/groups/' + groupId) && r.request().method() === 'PUT');
  await page.getByRole('button', { name: 'Save Group' }).click();
  assert.deepEqual((await (await renaming).json()).groups.find((g) => g.id === groupId).build, built);
  const groupCard = page.locator('article').filter({ has: page.getByRole('heading', { name: 'Browser Tools Renamed', exact: true }) });
  await expect(groupCard.getByRole('button', { name: 'Use in Session' })).toBeEnabled();
  await groupCard.getByRole('button', { name: 'Use in Session' }).click();
  const sessionPlugins = page.getByRole('region', { name: 'Use in Session' });
  await expect(sessionPlugins.getByText('Select a Session to load its plugin settings.')).toBeVisible();
  await sessionPlugins.getByLabel('Session', { exact: true }).selectOption(process.env.ACTION_PROBE_SESSION);
  const toggle = sessionPlugins.getByRole('checkbox', { name: /Browser Tools Renamed/ });
  await toggle.click();
  await expect(toggle).toBeChecked();
  for (const mode of ['standalone', 'pan_native', 'gateway']) {
    await expect(page.getByLabel(pid + ' mode')).toBeEnabled();
    await page.getByLabel(pid + ' mode').selectOption(mode);
    await expect(page.getByLabel(pid + ' mode')).toBeEnabled();
    await expect(page.getByLabel(pid + ' mode')).toHaveValue(mode);
  }
  const selected = await (await page.request.get(base + '/api/sessions/' + process.env.ACTION_PROBE_SESSION)).json();
  assert.ok(selected.pluginBindings.some((b) => b.plugin_id === 'action-group-probe' && b.enabled));
  assert.ok(selected.pluginBindings.some((b) => b.plugin_id === pid && b.enabled && b.mode === 'gateway'));
  const main = page.getByRole('main', { name: 'Actions Panel' });
  await main.evaluate((element) => { element.scrollTop = 0; });
  await page.screenshot({ path: path.join(runtime, 'panel-desktop.png'), fullPage: true });
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await main.evaluate((element) => { element.scrollTop = 0; });
    await page.screenshot({ path: path.join(runtime, `groups-${width}.png`), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false, 'document overflow');
    assert.equal(await main.evaluate((e) => e.scrollWidth > e.clientWidth), false, 'panel overflow');
    await sessionPlugins.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(runtime, `binding-${width}.png`), fullPage: true });
    await page.getByRole('tab', { name: 'New Action' }).click();
    await page.screenshot({ path: path.join(runtime, `new-action-${width}.png`), fullPage: true });
    assert.equal(await main.evaluate((e) => e.scrollWidth > e.clientWidth), false, 'form overflow');
    await page.getByRole('tab', { name: 'Groups' }).click();
  }
  await page.getByRole('button', { name: 'Unbuild Group', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Unbuild Group' })).toBeDisabled();
  await page.getByRole('button', { name: 'Delete Group', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Edit Browser Tools Renamed' })).toHaveCount(0);
  await page.getByRole('tab', { name: 'Actions' }).click();
  await page.getByRole('button', { name: 'Edit Browser Sessions Renamed' }).click();
  await page.getByRole('button', { name: 'Delete Action', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Edit Browser Sessions Renamed' })).toHaveCount(0);
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(runtime, 'browser.json'), JSON.stringify({ production: true, realHttp: true,
    threeSections: true, automaticActionId: actionId, automaticGroupId: groupId, renamePreservesIdsAndRelease: true,
    copyId: true, sharedCommand: true, build: true, contextualSessionBinding: true, preservesOtherBindings: true,
    allThreeModes: true, idleUnbuild: true, delete: true, narrowWidths: [390, 320], mobileOverflow: false, pageErrors: errors }, null, 2));
} finally {
  await browser.close();
}
