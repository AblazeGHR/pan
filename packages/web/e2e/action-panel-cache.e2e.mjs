/* Production browser against an owned isolated Pan instance. */
/* global document, console, setTimeout, URL */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { chromium, expect } from '@playwright/test';

const base = process.env.PANEL_PROBE_URL;
const runtime = process.env.PANEL_PROBE_RUNTIME;
const session1 = process.env.PANEL_PROBE_SESSION1;
const session2 = process.env.PANEL_PROBE_SESSION2;
const probePlugin = process.env.PANEL_PROBE_PLUGIN;
assert.ok(base && runtime && session1 && session2 && probePlugin);

const browser = await chromium.launch({ headless: true });
const pageErrors = [];
const consoleErrors = [];

function instrument(page) {
  const pending = new Map();
  const calls = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/')) return;
    pending.set(request, { path: url.pathname, method: request.method(), start: Date.now() });
  });
  page.on('response', (response) => {
    const info = pending.get(response.request());
    if (!info) return;
    pending.delete(response.request());
    calls.push({ ...info, status: response.status(), ms: Date.now() - info.start });
  });
  return calls;
}

function count(calls, pathname, method = 'GET') {
  return calls.filter((call) => call.path === pathname && call.method === method).length;
}

function actionRow(page, name = 'List Sessions') {
  return page.getByRole('button', { name, exact: true });
}

async function openList(page) {
  await page.goto(base + '/react/actions', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Actions & Groups' })).toBeVisible({ timeout: 20_000 });
  await expect(actionRow(page)).toBeVisible();
}

async function navigateAwayAndBack(page) {
  // Terminal and Actions share the visible view-entry page, so this is a pure
  // client-side route change without a document reload.
  await page.getByRole('link', { name: 'Terminal', exact: true }).click();
  await page.waitForURL((url) => url.pathname === '/react/terminals');
  await page.getByRole('link', { name: 'Actions', exact: true }).click();
  await page.waitForURL((url) => url.pathname === '/react/actions');
  await expect(actionRow(page)).toBeVisible();
}

async function waitForActionList(page) {
  const row = actionRow(page);
  if (!(await row.count())) {
    await page.getByRole('button', { name: /^Actions/ }).click();
  }
  await expect(row).toBeVisible();
}

const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
const calls = instrument(page);
const result = {
  production: true,
  realHttp: true,
  firstLoad: {},
  reentry: {},
  refresh: {},
  duplicate: {},
  mutation: {},
  releaseAndBinding: {},
  refreshFailure: {},
  lateRegistry: {},
  refreshSaveInterleaving: {},
  firstLoadFailure: {},
  lateSession: {},
  narrow: {},
  pageErrors,
  consoleErrors,
};

try {
  // First load: one registry request; catalog and Action sessions stay lazy.
  const firstStart = Date.now();
  await openList(page);
  result.firstLoad.renderMs = Date.now() - firstStart;
  await page.waitForTimeout(350);
  const firstRegistry = calls.filter((call) => call.path === '/api/actions' && call.method === 'GET');
  result.firstLoad.registryRequests = firstRegistry.length;
  result.firstLoad.registryMs = firstRegistry[0]?.ms ?? null;
  result.firstLoad.catalogRequests = count(calls, '/api/mcp/servers');
  result.firstLoad.sessionRequests = count(calls, '/api/sessions');
  assert.equal(result.firstLoad.registryRequests, 1, 'first load must read the registry once');
  assert.equal(result.firstLoad.catalogRequests, 0, 'catalog must stay lazy');
  await page.screenshot({ path: path.join(runtime, 'actions-desktop.png'), fullPage: true });

  // Client-side re-entry keeps the module cache: no extra registry request.
  const beforeReentry = count(calls, '/api/actions');
  await navigateAwayAndBack(page);
  await page.waitForTimeout(250);
  result.reentry.registryRequests = count(calls, '/api/actions') - beforeReentry;
  result.reentry.renderRequests = result.reentry.registryRequests;
  assert.equal(result.reentry.registryRequests, 0, 'fresh re-entry must not refetch the registry');

  // Manual Refresh is explicit and only touches the registry.
  const beforeRefresh = count(calls, '/api/actions');
  const refreshStart = Date.now();
  const refreshResponse = page.waitForResponse(
    (response) => new URL(response.url()).pathname === '/api/actions' && response.request().method() === 'GET',
  );
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await refreshResponse;
  await waitForActionList(page);
  result.refresh.ms = Date.now() - refreshStart;
  result.refresh.registryRequests = count(calls, '/api/actions') - beforeRefresh;
  assert.equal(result.refresh.registryRequests, 1);

  // Duplicate: existing create path, new server ID, independent definition.
  await page.getByRole('button', { name: 'More actions for List Sessions' }).click();
  const duplicateResponse = page.waitForResponse(
    (response) => new URL(response.url()).pathname === '/api/actions/actions' && response.request().method() === 'POST',
  );
  await page.getByRole('menuitem', { name: /Duplicate Action/ }).click();
  const response = await duplicateResponse;
  const duplicateBody = response.request().postDataJSON();
  const duplicateRegistry = await response.json();
  const copy = duplicateRegistry.item;
  assert.equal('id' in duplicateBody.item, false, 'duplicate must let the server allocate the ID');
  assert.equal(duplicateBody.item.name, 'List Sessions copy');
  assert.notEqual(copy.id, 'list_sessions');
  assert.match(copy.id, /^[a-z][a-z0-9_]{0,31}$/);
  await expect(page.getByRole('heading', { name: 'List Sessions copy' })).toBeVisible();
  await expect(page.getByText(copy.id, { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: path.join(runtime, 'duplicate-editor.png'), fullPage: true });

  const afterDuplicate = await (await page.request.get(base + '/api/actions')).json();
  const original = afterDuplicate.actions.find((action) => action.id === 'list_sessions');
  const copyFromApi = afterDuplicate.actions.find((action) => action.id === copy.id);
  const probeFromApi = afterDuplicate.groups.find((group) => group.id === 'probe');
  assert.ok(original && copyFromApi);
  assert.equal(probeFromApi.action_ids.includes(copy.id), false, 'copy must not join a Group');
  assert.deepEqual(probeFromApi.build.action_ids, ['list_sessions', 'echo']);
  result.duplicate.id = copy.id;
  result.duplicate.originalUnchanged = original.name === 'List Sessions';
  result.duplicate.copyIndependent = copyFromApi.name === 'List Sessions copy';
  result.duplicate.groupReferencesUnchanged = !probeFromApi.action_ids.includes(copy.id);
  result.duplicate.createdWithoutId = !('id' in duplicateBody.item);
  const postCountAfterDuplicate = count(calls, '/api/actions/actions', 'POST');

  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await waitForActionList(page);

  // A failed refresh must keep the list (including the new copy) and must not recreate it.
  await context.route('**/api/actions', (route) => route.abort());
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByText(/Couldn’t refresh\./)).toBeVisible();
  await expect(page.getByRole('button', { name: 'List Sessions copy', exact: true })).toBeVisible();
  result.refreshFailure.cachedListVisible = true;
  result.refreshFailure.copyStillVisible = true;
  result.refreshFailure.createRequests = count(calls, '/api/actions/actions', 'POST') - postCountAfterDuplicate;
  assert.equal(result.refreshFailure.createRequests, 0, 'refresh failure must not recreate');
  await page.screenshot({ path: path.join(runtime, 'refresh-failed.png'), fullPage: true });
  await context.unroute('**/api/actions');
  await page.getByRole('status').filter({ hasText: 'Couldn’t refresh' }).getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByText(/Couldn’t refresh\./)).toHaveCount(0);

  // Action save applies the authoritative registry response without a secondary read.
  const registryGetsBeforeSave = count(calls, '/api/actions', 'GET');
  const catalogBeforeSave = count(calls, '/api/mcp/servers');
  const sessionsBeforeSave = count(calls, '/api/sessions');
  await actionRow(page).click();
  await page.locator('#action-description').fill('List isolated Sessions (cache probe)');
  const putResponse = page.waitForResponse(
    (res) => new URL(res.url()).pathname === '/api/actions/actions/list_sessions' && res.request().method() === 'PUT',
  );
  await page.getByRole('button', { name: 'Save Action' }).click();
  const put = await putResponse;
  assert.equal((await put.json()).actions.find((action) => action.id === 'list_sessions').description,
    'List isolated Sessions (cache probe)');
  await waitForActionList(page);
  result.mutation.putRequests = 1;
  result.mutation.secondaryRegistryRequests = count(calls, '/api/actions', 'GET') - registryGetsBeforeSave;
  result.mutation.catalogRequests = count(calls, '/api/mcp/servers') - catalogBeforeSave;
  result.mutation.sessionRequests = count(calls, '/api/sessions') - sessionsBeforeSave;
  assert.equal(result.mutation.secondaryRegistryRequests, 0);
  assert.equal(result.mutation.catalogRequests, 0);
  assert.equal(result.mutation.sessionRequests, 0);

  // Group release + binding: catalog/Sessions are fetched only when the modal opens.
  await page.getByRole('button', { name: /^Groups/ }).click();
  await page.screenshot({ path: path.join(runtime, 'groups-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: 'Edit Release Tools' }).click();
  const buildResponse = page.waitForResponse(
    (res) => new URL(res.url()).pathname === '/api/actions/groups/release_tools/build',
  );
  await page.getByRole('button', { name: 'Build Group', exact: true }).click();
  await buildResponse;
  await expect(page.getByRole('button', { name: 'Rebuild Group' })).toBeVisible();
  result.releaseAndBinding.buildRequests = 1;
  result.releaseAndBinding.catalogAfterBuild = count(calls, '/api/mcp/servers');
  assert.equal(result.releaseAndBinding.catalogAfterBuild, 0, 'build invalidates without eagerly fetching catalog');

  const catalogBeforeModal = count(calls, '/api/mcp/servers');
  const sessionsBeforeModal = count(calls, '/api/sessions');
  await page.getByRole('button', { name: 'Use in Session', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('First', { exact: true })).toBeVisible();
  result.releaseAndBinding.catalogOpenedLazily = count(calls, '/api/mcp/servers') > catalogBeforeModal;
  result.releaseAndBinding.sessionsOpenedLazily = count(calls, '/api/sessions') > sessionsBeforeModal;
  await dialog.getByText('First', { exact: true }).click();
  await expect(dialog.getByText('Plugin binding')).toBeVisible();
  const registryGetsBeforeBinding = count(calls, '/api/actions', 'GET');
  const patchResponse = page.waitForResponse(
    (res) => new URL(res.url()).pathname === '/api/sessions/' + session1 && res.request().method() === 'PATCH',
  );
  await dialog.getByText('Discover / call', { exact: true }).click();
  await dialog.getByRole('button', { name: 'Apply to Session' }).click();
  const patch = await patchResponse;
  const patchBody = patch.request().postDataJSON();
  const releasePlugin = 'action-group-release-tools';
  assert.ok(patchBody.pluginBindings.some((binding) => binding.plugin_id === releasePlugin && binding.mode === 'gateway'));
  assert.ok(patchBody.pluginBindings.some((binding) => binding.plugin_id === probePlugin && binding.enabled));
  await page.screenshot({ path: path.join(runtime, 'binding-modal.png'), fullPage: true });
  await expect(dialog).toBeHidden();
  await page.waitForTimeout(300);
  result.releaseAndBinding.bindingPatch = 1;
  result.releaseAndBinding.backgroundRegistryAfterBinding =
    count(calls, '/api/actions', 'GET') - registryGetsBeforeBinding;
  await page.screenshot({ path: path.join(runtime, 'binding-desktop.png'), fullPage: true });

  // Unbuild is allowed here because the binding is idle; no eager secondary GET.
  const catalogBeforeUnbuild = count(calls, '/api/mcp/servers');
  const unbuildResponse = page.waitForResponse(
    (res) => new URL(res.url()).pathname === '/api/actions/groups/release_tools/unbuild',
  );
  await page.getByRole('button', { name: 'Unbuild Group', exact: true }).click();
  await unbuildResponse;
  await page.waitForTimeout(250);
  result.releaseAndBinding.unbuildRequests = 1;
  result.releaseAndBinding.catalogAfterUnbuild = count(calls, '/api/mcp/servers') - catalogBeforeUnbuild;
  assert.equal(result.releaseAndBinding.catalogAfterUnbuild, 0);
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await waitForActionList(page);

  // Late registry response must not replace the current editor draft.
  const lateContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const latePage = await lateContext.newPage();
  const lateCalls = instrument(latePage);
  await openList(latePage);
  await lateContext.route('**/api/actions', async (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    await new Promise((resolve) => setTimeout(resolve, 1200));
    await route.continue();
  });
  await latePage.getByRole('button', { name: 'Refresh', exact: true }).click();
  await actionRow(latePage).click();
  await latePage.locator('#action-name').fill('Late editor draft');
  await latePage.waitForTimeout(1600);
  result.lateRegistry.draftAfterLateResponse = await latePage.locator('#action-name').inputValue();
  result.lateRegistry.editorStillOpen = await latePage.getByRole('button', { name: 'Save Action' }).isVisible();
  assert.equal(result.lateRegistry.draftAfterLateResponse, 'Late editor draft');
  assert.equal(result.lateRegistry.editorStillOpen, true);
  assert.ok(lateCalls.some((call) => call.path === '/api/actions'));
  await lateContext.close();

  // Refresh/save interleaving: a pre-save GET must not leave Refresh disabled
  // or overwrite the save response.
  {
    const interleaveContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const interleavePage = await interleaveContext.newPage();
    instrument(interleavePage);
    await openList(interleavePage);
    await interleaveContext.route('**/api/actions', async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      await new Promise((resolve) => setTimeout(resolve, 1200));
      await route.continue();
    });
    const refreshButton = interleavePage.getByRole('button', { name: 'Refresh', exact: true });
    await refreshButton.click();
    await expect(refreshButton).toBeDisabled();
    await actionRow(interleavePage).click();
    await interleavePage.locator('#action-description').fill('Interleaved save survives refresh');
    const saveResponse = interleavePage.waitForResponse(
      (res) => new URL(res.url()).pathname === '/api/actions/actions/list_sessions' && res.request().method() === 'PUT',
    );
    await interleavePage.getByRole('button', { name: 'Save Action' }).click();
    await saveResponse;
    await interleavePage.waitForTimeout(1400);
    await waitForActionList(interleavePage);
    result.refreshSaveInterleaving.refreshStayedBusyDuringSave = true;
    result.refreshSaveInterleaving.refreshReEnabled = await refreshButton.isEnabled();
    result.refreshSaveInterleaving.savedTextVisible = await interleavePage
      .getByText('Interleaved save survives refresh', { exact: true })
      .isVisible();
    assert.equal(result.refreshSaveInterleaving.refreshReEnabled, true);
    assert.equal(result.refreshSaveInterleaving.savedTextVisible, true);
    await interleaveContext.close();
  }

  // First-load failure is blocking; Retry recovers.
  const errorContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const errorPage = await errorContext.newPage();
  instrument(errorPage);
  await errorContext.route('**/api/actions', (route) => route.abort());
  await errorPage.goto(base + '/react/actions', { waitUntil: 'domcontentloaded' });
  await expect(errorPage.getByRole('alert').filter({ hasText: 'Couldn’t load the Action registry' })).toBeVisible();
  result.firstLoadFailure.blockingError = true;
  await errorContext.unroute('**/api/actions');
  await errorPage.getByRole('button', { name: 'Retry' }).click();
  await expect(errorPage.getByRole('button', { name: 'Edit List Sessions', exact: true })).toBeVisible();
  result.firstLoadFailure.retryRecovered = true;
  await errorContext.close();

  // A late Session detail response must not replace the newly selected Session.
  const sessionContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const sessionPage = await sessionContext.newPage();
  instrument(sessionPage);
  await openList(sessionPage);
  await sessionContext.route(`**/api/sessions/${session1}`, async (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    await new Promise((resolve) => setTimeout(resolve, 1100));
    await route.continue();
  });
  await sessionPage.getByRole('button', { name: /^Groups/ }).click();
  const probeRow = sessionPage.locator('ul[aria-label="Groups"] > li').filter({ hasText: 'Probe Tools' });
  await probeRow.getByRole('button', { name: /Use( in Session)?/ }).click();
  const sessionDialog = sessionPage.getByRole('dialog');
  await expect(sessionDialog.getByText('First', { exact: true })).toBeVisible();
  await sessionDialog.getByText('First', { exact: true }).click();
  await sessionDialog.getByText('Second', { exact: true }).click();
  await expect(sessionDialog.getByText('Plugin binding')).toBeVisible();
  await sessionPage.waitForTimeout(1400);
  const secondRadio = sessionDialog.getByRole('radiogroup', { name: 'Session' }).getByRole('radio').nth(1);
  assert.equal(await secondRadio.isChecked(), true);
  result.lateSession.secondSelected = true;
  const latePatch = sessionPage.waitForResponse(
    (res) => new URL(res.url()).pathname === '/api/sessions/' + session2 && res.request().method() === 'PATCH',
  );
  await sessionDialog.getByText('Discover / call', { exact: true }).click();
  await sessionDialog.getByRole('button', { name: 'Apply to Session' }).click();
  const sessionPatch = await latePatch;
  result.lateSession.expectedRevision = sessionPatch.request().postDataJSON().expectedPluginRevision;
  assert.equal(result.lateSession.expectedRevision, 0, 'late First response must not replace Second detail');
  await sessionContext.close();

  // Narrow layout: no document or panel horizontal overflow.
  await page.getByRole('button', { name: /^Actions/ }).click();
  await waitForActionList(page);
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await waitForActionList(page);
    const main = page.getByRole('main', { name: 'Actions Panel' });
    const sizes = await main.evaluate((element) => ({
      document: document.documentElement.scrollWidth,
      documentClient: document.documentElement.clientWidth,
      panel: element.scrollWidth,
      panelClient: element.clientWidth,
    }));
    assert.ok(sizes.document <= sizes.documentClient, `document overflow at ${width}`);
    assert.ok(sizes.panel <= sizes.panelClient, `panel overflow at ${width}`);
    result.narrow[width] = sizes;
    await page.screenshot({ path: path.join(runtime, `actions-${width}.png`), fullPage: true });
    await page.getByRole('button', { name: /^Groups/ }).click();
    await page.screenshot({ path: path.join(runtime, `groups-${width}.png`), fullPage: true });
    if (!(await page.getByRole('button', { name: 'Edit List Sessions', exact: true }).count())) {
      await page.getByRole('button', { name: /^Actions/ }).click();
      await waitForActionList(page);
    }
    await actionRow(page).click();
    await page.screenshot({ path: path.join(runtime, `action-editor-${width}.png`), fullPage: true });
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    if (await page.getByRole('button', { name: 'Discard draft' }).count()) {
      await page.getByRole('button', { name: 'Discard draft' }).click();
    }
  }

  result.pageErrors = pageErrors;
  result.consoleErrors = consoleErrors;
  assert.deepEqual(pageErrors, []);
  await fs.writeFile(path.join(runtime, 'cache.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
} finally {
  await context.close();
  await browser.close();
}
