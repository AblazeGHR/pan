/* global document, console, process */
import { chromium } from '@playwright/test';
import { writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

/**
 * Reproducible evidence capture for the local Action Panel mock.
 *
 * Run from packages/web:
 *   node mock/action-panel/capture.mjs
 *
 * It only opens the self-contained preview.html from file://. It does not start
 * a Pan server or touch Sessions, Groups, ports 8767/8768, or user data.
 */
const here = path.dirname(fileURLToPath(import.meta.url));
const artifactDir = path.resolve(here, '../../../../audit/action-panel-mock-20261021');
const previewFile = path.join(artifactDir, 'preview/preview.html');
const shotsDir = path.join(artifactDir, 'screenshots');
const url = pathToFileURL(previewFile).href;
const QUERY = 'ribbon=1&controls=0';

mkdirSync(shotsDir, { recursive: true });

const browser = await chromium.launch();
const version = browser.version();
const externalRequests = [];
const consoleErrors = [];
const viewportChecks = [];
const rowActionChecks = [];
const interactions = {};
const screenshots = [];

function track(page) {
  page.on('pageerror', (error) => consoleErrors.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(`console: ${message.text()}`);
  });
  page.on('request', (request) => {
    if (!request.url().startsWith('file:')) externalRequests.push(request.url());
  });
}

async function openPage(width, height, query) {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
  track(page);
  await page.goto(`${url}?${query}`);
  await page.waitForTimeout(300);
  return page;
}

async function checkViewport(page, view, width) {
  const sizes = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  const ok = sizes.document <= sizes.client && sizes.body <= sizes.client;
  viewportChecks.push({ view, width, ...sizes, ok });
  if (!ok) throw new Error(`Horizontal overflow in ${view} at ${width}px: ${JSON.stringify(sizes)}`);
}

async function shot(page, name) {
  const file = path.join(shotsDir, name);
  await page.screenshot({ path: file });
  screenshots.push(name);
}

try {
  // Desktop list, editors, modal, states.
  {
    const page = await openPage(1440, 900, QUERY);
    await checkViewport(page, 'actions', 1440);
    await shot(page, '01-actions-desktop.png');

    await page.getByLabel('Search Actions').fill('echo');
    await page.waitForTimeout(100);
    interactions.searchEchoRows = await page.locator('ul[aria-label="Actions"] > li').count();
    await page.getByLabel('Clear search').click();

    await page.getByRole('button', { name: /^Groups/ }).click();
    await page.waitForTimeout(100);
    await checkViewport(page, 'groups', 1440);
    await shot(page, '02-groups-desktop.png');
    interactions.groupRows = await page.locator('ul[aria-label="Groups"] > li').count();
    await page.close();
  }

  {
    const page = await openPage(1440, 900, `${QUERY}&view=edit-action&id=action_7f2c91a4d83b6e05a1c47d92`);
    await checkViewport(page, 'action-editor', 1440);
    await shot(page, '03-action-editor.png');
    await page.close();
  }

  {
    const page = await openPage(1440, 900, `${QUERY}&view=edit-group&id=group_browser_tools`);
    await checkViewport(page, 'group-editor', 1440);
    await shot(page, '04-group-editor.png');
    interactions.unbuildDisabledOnNativeQa = false; // measured on the blocked fixture below
    await page.close();
  }

  {
    const page = await openPage(1440, 900, `${QUERY}&view=use&id=group_browser_tools`);
    const dialog = page.getByRole('dialog');
    await dialog.getByText('Release audit — action groups', { exact: true }).click();
    await page.waitForTimeout(120);
    await dialog.getByText('Discover / call', { exact: true }).click();
    await page.waitForTimeout(250);
    interactions.modeCards = await dialog
      .getByRole('radiogroup', { name: 'Plugin mode' })
      .getByRole('radio')
      .count();
    interactions.selectedMode = await dialog
      .getByRole('radio', { name: 'Discover / call' })
      .isChecked();
    await shot(page, '05-use-in-session.png');
    await dialog.locator('div.overflow-auto').first().evaluate((element) => {
      element.scrollTop = 0;
    });
    await page.waitForTimeout(120);
    await shot(page, '05b-use-in-session-top.png');
    await page.close();
  }

  {
    const page = await openPage(1440, 900, `${QUERY}&view=new-action`);
    await shot(page, '06-new-action.png');
    await page.close();
  }

  {
    const page = await openPage(1440, 900, `${QUERY}&view=edit-action&id=action_7f2c91a4d83b6e05a1c47d92`);
    await page.locator('#action-name').fill('List Sessions changed');
    await page.getByRole('button', { name: 'Cancel' }).click();
    await page.waitForTimeout(350);
    const dialog = page.getByRole('dialog');
    interactions.dirtyDialog = await dialog.isVisible();
    interactions.dirtyDialogText = (await dialog.innerText()).split('\n').slice(0, 2).join(' ');
    await shot(page, '07-dirty-guard.png');
    await page.close();
  }

  {
    const page = await openPage(1440, 900, `${QUERY}&view=edit-action&id=action_7f2c91a4d83b6e05a1c47d92`);
    await page.locator('#action-name').fill('Keyboard draft');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(250);
    interactions.keyboardEscapeDialog = await page.getByRole('dialog').isVisible();
    await page.keyboard.press('Escape');
    await page.waitForTimeout(150);
    await page.locator('#action-name').fill('List Sessions via keyboard');
    await page.keyboard.press('Control+s');
    await page.waitForTimeout(500);
    interactions.keyboardSaveClosedEditor = (await page.locator('#action-name').count()) === 0;
    interactions.keyboardSaveRow = await page
      .getByRole('button', { name: 'List Sessions via keyboard', exact: true })
      .count();
    await page.close();
  }

  {
    const page = await openPage(1440, 900, `${QUERY}&view=use&id=group_browser_tools`);
    await page.waitForTimeout(200);
    for (let index = 0; index < 20; index += 1) await page.keyboard.press('Tab');
    interactions.focusTrapInsideDialog = await page.evaluate(() =>
      Boolean(document.activeElement?.closest('[role="dialog"]')),
    );
    await page.close();
  }

  for (const [name, state] of [
    ['08-state-loading.png', 'loading'],
    ['09-state-error.png', 'error'],
    ['10-state-empty.png', 'empty'],
    ['11-state-stale.png', 'stale'],
  ]) {
    const page = await openPage(1440, 900, `${QUERY}&state=${state}`);
    await shot(page, name);
    if (state === 'empty') {
      interactions.emptyCountText = await page.locator('[aria-live="polite"]').innerText();
    }
    await page.close();
  }

  {
    const page = await openPage(1440, 900, `${QUERY}&theme=light`);
    await shot(page, '12-light-theme.png');
    await page.close();
  }

  {
    const page = await openPage(1440, 900, 'ribbon=1&controls=1');
    await shot(page, '19-preview-controls.png');
    await page.close();
  }

  {
    const page = await openPage(320, 844, 'ribbon=1&controls=1');
    await checkViewport(page, 'preview-controls', 320);
    await shot(page, '19b-preview-controls-320.png');
    await page.close();
  }

  // Mobile list and editor geometry.
  for (const width of [390, 320]) {
    const page = await openPage(width, 844, QUERY);
    await checkViewport(page, 'actions', width);
    await shot(page, width === 390 ? '13-actions-390.png' : '15-actions-320.png');
    await page.getByRole('button', { name: /^Groups/ }).click();
    await page.waitForTimeout(100);
    await checkViewport(page, 'groups', width);
    await shot(page, width === 390 ? '14-groups-390.png' : '15b-groups-320.png');
    if (width === 390) {
      await page.getByRole('button', { name: 'Open sidebar' }).click();
      await page.waitForTimeout(250);
      const drawer = await page.getByLabel('Pan sidebar').boundingBox();
      interactions.mobileDrawerX = drawer?.x;
      interactions.mobileDrawerOpen = drawer?.x === 0;
      await shot(page, '13b-sidebar-390.png');
      await page.getByRole('button', { name: 'Close sidebar', exact: true }).click();
    }
    await page.close();
  }

  {
    const page = await openPage(320, 844, `${QUERY}&view=edit-action&id=action_7f2c91a4d83b6e05a1c47d92`);
    await checkViewport(page, 'action-editor', 320);
    await shot(page, '16-action-editor-320.png');
    await page.close();
  }

  {
    const page = await openPage(320, 844, `${QUERY}&view=edit-group&id=group_browser_tools`);
    await checkViewport(page, 'group-editor', 320);
    await shot(page, '17-group-editor-320.png');
    await page.close();
  }

  for (const width of [390, 320]) {
    const page = await openPage(width, 844, `${QUERY}&view=use&id=group_browser_tools`);
    const dialog = page.getByRole('dialog');
    await dialog.getByText('Release audit — action groups', { exact: true }).click();
    await page.waitForTimeout(200);
    await checkViewport(page, 'use-in-session', width);
    await dialog.locator('div.overflow-auto').first().evaluate((element) => {
      element.scrollTop = 0;
    });
    await page.waitForTimeout(100);
    await shot(page, width === 390 ? '18-use-390.png' : '18b-use-320.png');
    await dialog.getByText('Discover / call', { exact: true }).click();
    await page.waitForTimeout(250);
    await shot(page, width === 390 ? '18c-use-modes-390.png' : '18d-use-modes-320.png');
    await page.close();
  }

  // Interaction checks on a disposable page.
  {
    const page = await openPage(1440, 900, QUERY);
    await page.getByRole('button', { name: 'New Action' }).click();
    await page.locator('#action-name').fill('Release Notes Digest');
    await page
      .locator('#action-description')
      .fill('Collect merged pull requests and unresolved blockers into a weekly release digest.');
    await page.getByLabel('New input field').fill('version');
    await page.getByRole('button', { name: 'Add field' }).click();
    await page.getByRole('button', { name: 'Create Action' }).click();
    await page.waitForTimeout(400);
    interactions.createdActionRow = await page
      .getByRole('button', { name: 'Release Notes Digest', exact: true })
      .count();
    await page.getByRole('button', { name: 'Release Notes Digest', exact: true }).click();
    await page.waitForTimeout(100);
    const id = await page.getByText(/^action_[0-9a-f]{24}$/).first().innerText();
    interactions.generatedActionId = id;
    interactions.generatedActionIdLength = id.length;
    await page.close();
  }

  {
    const page = await openPage(1440, 900, `${QUERY}&view=groups`);
    await page.getByRole('button', { name: 'Workspace Utilities', exact: true }).click();
    await page.getByRole('button', { name: 'Build Group', exact: true }).click();
    await page.waitForTimeout(500);
    interactions.buildToast = await page.getByRole('status').last().innerText();
    await shot(page, '20-group-built.png');
    await page.close();
  }

  {
    const page = await openPage(1440, 900, `${QUERY}&view=edit-group&id=group_native_qa`);
    const button = page.getByRole('button', { name: 'Unbuild Group' });
    interactions.unbuildDisabledOnNativeQa = await button.isDisabled();
    interactions.unbuildReason = await button.getAttribute('title');
    await page.close();
  }

  // Row-action fit at every breakpoint. Catches text/label overflow that a
  // document-level scrollWidth check would miss because the list clips rows.
  for (const view of ['groups', 'actions']) {
    for (const width of [1600, 1440, 1366, 1280, 1152, 1024, 900, 768, 390, 320]) {
      const page = await openPage(width, 900, `${QUERY}&view=${view}`);
      const check = await page.evaluate((viewName) => {
        const label = viewName === 'groups' ? 'Groups' : 'Actions';
        const list = document.querySelector(`ul[aria-label="${label}"]`)?.parentElement;
        const row = document.querySelector(`ul[aria-label="${label}"] > li`);
        if (!list || !row) return null;
        return {
          listOverflow: list.scrollWidth > list.clientWidth,
          documentOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          clippedButtons: [...row.querySelectorAll('button')]
            .filter((button) => button.scrollWidth > button.clientWidth + 1)
            .map((button) => button.innerText.trim()),
        };
      }, view);
      if (!check) throw new Error(`Missing ${view} row at ${width}px`);
      const ok = !check.listOverflow && !check.documentOverflow && check.clippedButtons.length === 0;
      rowActionChecks.push({ view, width, ...check, ok });
      if (!ok) throw new Error(`Row action overflow in ${view} at ${width}px: ${JSON.stringify(check)}`);
      await page.close();
    }
  }

  // Mid-size desktop check to catch the 768px sidebar+list squeeze.
  {
    const page = await openPage(768, 900, QUERY);
    await checkViewport(page, 'actions', 768);
    await page.getByRole('button', { name: /^Groups/ }).click();
    await page.waitForTimeout(100);
    await checkViewport(page, 'groups', 768);
    await page.close();
  }

  const metrics = {
    generatedAt: new Date().toISOString(),
    browser: `Chromium ${version}`,
    source: 'local fixture preview.html (file://)',
    preview: 'preview/preview.html',
    screenshots,
    viewportChecks,
    rowActionChecks,
    interactions,
    externalRequests,
    consoleErrors,
    note: 'No Pan API, Session, Group, port 8767/8768 or user data was contacted. UI states are simulated for design review; these numbers are not upstream performance measurements.',
  };
  writeFileSync(path.join(artifactDir, 'metrics.json'), JSON.stringify(metrics, null, 2));
  console.log(JSON.stringify(metrics, null, 2));
  if (consoleErrors.length) process.exitCode = 1;
  if (externalRequests.length) process.exitCode = 1;
} finally {
  await browser.close();
}
