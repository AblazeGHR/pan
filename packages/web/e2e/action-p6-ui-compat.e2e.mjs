import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { randomUUID } from 'node:crypto';
import { execFileSync, spawn } from 'node:child_process';
import { chromium, expect } from '@playwright/test';
import { setTimeout as sleep } from 'node:timers/promises';

const key = randomUUID();
const automationOnly = process.argv.includes('--automation-only');
const root = path.resolve(import.meta.dirname, '../../..');
const runtime = path.join(root, 'packages/web/test-results', `p6-ui-${Date.now()}`);
await fs.mkdir(runtime, { recursive: true });
const port = await new Promise((resolve) => {
  const server = net.createServer();
  server.listen(0, '127.0.0.1', () => {
    const value = server.address().port;
    server.close(() => resolve(value));
  });
});
assert(![8767, 8768].includes(port));
const base = `http://127.0.0.1:${port}`;
const env = Object.fromEntries(
  Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith('PAN_')),
);
Object.assign(env, { PAN_E2E_RUNTIME: runtime, PAN_E2E_ADMIN_SECRET: key, PAN_PORT: String(port), PYTHONPATH: root });
const configuredPython = process.env.PAN_E2E_PYTHON || 'E:/software/miniforge/python.exe';
// Windows venv `Scripts/python.exe` is a launcher on this machine: Node
// receives the launcher PID while the real interpreter owns the HTTP socket.
// Resolve its pyvenv.cfg executable before spawn so the PID returned by Node
// and the PID published by /__e2e/identity are the same process.
let python = configuredPython;
try {
  const cfg = await fs.readFile(path.join(path.dirname(configuredPython), '..', 'pyvenv.cfg'), 'utf8');
  const executable = cfg.match(/^executable\s*=\s*(.+)$/m)?.[1]?.trim();
  if (executable && await fs.stat(executable).then(() => true, () => false)) python = executable;
} catch {}
const child = spawn(python, [path.join(import.meta.dirname, 'action-p6-server.py')], {
  cwd: root,
  env,
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
let log = '';
let browser;
let page;
const requests = [];
const evidence = {
  sha: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
  dirty: Boolean(execFileSync('git', ['status', '--porcelain'], { cwd: root, encoding: 'utf8' }).trim()),
  port,
  checks: [],
  errors: [],
};
child.stdout.on('data', (chunk) => {
  log += chunk;
});
child.stderr.on('data', (chunk) => {
  log += chunk;
});

try {
  let bootstrap;
  for (let index = 0; index < 40; index++) {
    if (child.exitCode !== null) throw Error('owned server exited');
    try {
      bootstrap = await fetch(base + '/api/automation/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json', Origin: base },
        body: JSON.stringify({ key }),
      });
    } catch { await sleep(500); continue; }
    if (bootstrap.status === 503) { await sleep(500); continue; }
    break;
  }
  assert(bootstrap?.ok, 'isolated bootstrap status ' + bootstrap?.status);
  const { token } = await bootstrap.json();
  const identityResponse = await fetch(base + '/__e2e/identity', { headers: { Authorization: 'Bearer ' + token } });
  assert.equal(identityResponse.status, 200);
  const identity = await identityResponse.json();
  assert.equal(identity.pid, child.pid);
  assert.equal(path.resolve(identity.runtime), runtime);
  assert.equal(path.resolve(identity.checkout), root);
  evidence.checks.push('owned service PID, checkout and runtime verified');

  const sourcePath = path.join(runtime, 'cooperative-actions/registry.json');
  const sourceBytes = await fs.readFile(sourcePath);
  const seed = JSON.parse(await fs.readFile(path.join(runtime, 'p6-seed.json'), 'utf8'));

  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', (error) => evidence.errors.push(error.message));
  page.on('request', (request) => {
    if (request.method() !== 'GET' && request.url().includes('/api/') && !request.url().endsWith('/automation/login')) {
      requests.push({
        method: request.method(),
        url: request.url(),
        body: request.postDataJSON?.(),
      });
    }
  });

  await page.goto(base + '/react/actions');
  // The integrated canonical shell may expose login from Settings rather than
  // inline on Actions. Establish the same HttpOnly session through the real
  // authorization endpoint, then keep the inline flow for older shells.
  const login = await page.evaluate(async (value) => {
    const response = await fetch('/api/automation/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: value }) });
    return { ok: response.ok };
  }, key);
  assert.equal(login.ok, true, 'isolated administrator login');
  // Global login may have returned the initial deep link to the Chat root.
  await page.goto(base + '/react/actions');
  if (await page.getByLabel('Local user key').count()) {
    await page.getByLabel('Local user key').fill(key);
    await page.getByRole('button', { name: 'Log in', exact: true }).click();
  }
  if (!automationOnly) {
  const mappedTab = page.getByRole('button', { name: '旧定义与 Session 绑定' });
  if (await mappedTab.count()) await mappedTab.click();
  else await page.getByRole('button', { name: 'Inspect Owned old series', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Mapped canonical Action' })).toBeVisible();
  evidence.checks.push('old UI mapped advanced Action opens the canonical compatibility panel');

  const duplicateResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/jobs/actions') && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Duplicate canonical Action' }).click();
  await page.getByLabel('Copy name').fill('Owned browser copy');
  await page.getByRole('button', { name: 'Create canonical copy' }).click();
  const duplicated = await duplicateResponse;
  assert.equal(duplicated.status(), 201);
  const duplicateBody = duplicated.request().postDataJSON();
  assert.equal(duplicateBody.expectedRevision, 0);
  assert.equal(duplicateBody.definition.name, 'Owned browser copy');
  assert.equal(duplicateBody.definition.execution.config.commands.length, 2);
  assert.equal(duplicateBody.definition.execution.config.commands[1].timeout, 19);
  assert.ok(duplicateBody.requestKey);
  evidence.checks.push('canonical duplicate preserves both commands, timeout and a fresh requestKey');

  // The lifecycle receipt updates the old panel before onOpenCopy remounts
  // the copied identity. Wait for that final identity, then open its editor.
  const duplicateReceipt = await duplicated.json();
  const copiedId = duplicateReceipt.result.object.id;
  await expect(page.getByRole('heading', { name: `Owned browser copy · ${copiedId}`, exact: true })).toBeVisible();
  const draftButton = page.getByRole('button', { name: '编辑为新草稿版本' });
  if (await draftButton.count()) await draftButton.click();
  else await page.getByRole('button', { name: 'Edit as a new draft version' }).click();
  const copyDefinition = JSON.parse(
    await page.getByRole('textbox', { name: 'Action definition' }).inputValue(),
  );
  assert.equal(copyDefinition.execution.config.commands.length, 2);
  assert.equal(copyDefinition.name, 'Owned browser copy');
  const backCatalog = page.getByRole('button', { name: '返回旧目录' });
  if (await backCatalog.count()) await backCatalog.click();
  else await page.getByRole('button', { name: 'Back to catalog' }).click();
  await page.getByRole('button', { name: 'Discard draft' }).click();
  evidence.checks.push('duplicated canonical copy opens with the full definition and discard guard');

  if (await page.getByRole('button', { name: 'Edit Owned old series', exact: true }).count()) {
    await page.getByRole('button', { name: 'Edit Owned old series', exact: true }).click();
  } else {
    await page.getByRole('button', { name: 'Inspect Owned old series', exact: true }).click();
  }
  const deletesBefore = requests.filter((item) => item.method === 'DELETE').length;
  await page.getByRole('button', { name: 'Delete canonical Action' }).click();
  await expect(page.getByText(/Action is used by Owned Group/)).toBeVisible();
  assert.equal(
    requests.filter((item) => item.method === 'DELETE').length,
    deletesBefore,
    'a Group-referenced Action must not be deleted',
  );
  evidence.checks.push('delete is blocked for a Group-referenced Action without a delete request');

  const disableResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/jobs/actions/p6_original/operations') &&
      response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Disable canonical Action' }).click();
  const disabled = await disableResponse;
  assert.equal(disabled.request().postDataJSON().operation, 'disable');
  assert.equal(disabled.request().postDataJSON().expectedRevision, seed.actionRevision);
  assert.equal(disabled.request().postDataJSON().expectedStateRevision, seed.actionStateRevision);
  await expect(page.getByRole('button', { name: 'Enable canonical Action' })).toBeVisible();

  const enableResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/jobs/actions/p6_original/operations') &&
      response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Enable canonical Action' }).click();
  const enabled = await enableResponse;
  assert.equal(enabled.request().postDataJSON().operation, 'enable');
  assert.equal(enabled.request().postDataJSON().expectedRevision, seed.actionRevision);
  assert.equal(enabled.request().postDataJSON().expectedStateRevision, seed.actionStateRevision + 1);
  await expect(page.getByRole('button', { name: 'Disable canonical Action' })).toBeVisible();
  evidence.checks.push('disable/enable use canonical CAS and the observed state revision');

  assert.deepEqual(await fs.readFile(sourcePath), sourceBytes);
  const originalRegistry = JSON.parse(sourceBytes.toString('utf8'));
  const finalRegistry = JSON.parse(await fs.readFile(sourcePath, 'utf8'));
  assert.equal(finalRegistry.groups.p6_tools.build.version, originalRegistry.groups.p6_tools.build.version);
  assert.equal(finalRegistry.groups.p6_tools.build.version, seed.groupRelease);
  assert.equal(await fs.access(path.join(runtime, 'effects')).then(() => true, () => false), false);
  evidence.checks.push('old source bytes, Group release and effect side effects remain unchanged');

  }

  // Jobs now has one product entry. Detail and editors take over the main
  // content panel while the application navigation shell stays mounted.
  await page.goto(base + '/react/jobs');
  await expect(page.getByRole('heading', { name: 'Jobs' })).toBeVisible();
  await expect(page.getByText('All Jobs', { exact: true })).toBeVisible();
  assert.equal(await page.getByText('Definitions', { exact: true }).count(), 0);
  const newJob = page.getByRole('button', { name: 'New Job', exact: true });
  await newJob.click();
  await expect(page.getByRole('heading', { name: 'Create a Job' })).toBeVisible();
  await expect(page.getByText('Job configuration', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Cancel', exact: true }).first().click();
  await expect(page.getByRole('heading', { name: 'Jobs' })).toBeVisible();
  evidence.checks.push('production Jobs flow uses one list and an in-panel structured editor');

  // A canonical Action Job is created and edited from the same Jobs entry.
  await page.getByRole('button', { name: 'New Job', exact: true }).click();
  await page.getByLabel('Job configuration type').selectOption('action');
  await page.getByLabel('Name', { exact: true }).fill('Owned Action Job');
  await page.getByLabel('Action for step 1').selectOption('p6_original');
  await expect(page.getByRole('region', { name: 'Parameter bindings for step 1' })).toBeVisible();
  assert.equal(await page.getByRole('dialog').count(), 0, 'Action Job editor must occupy the main panel');
  await page.getByRole('button', { name: 'Create Action Job' }).click();
  await expect(page.getByTestId('jobs-list-scroll').getByText('Owned Action Job', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByTestId('jobs-list-scroll').getByText('Owned Action Job', { exact: true })).toBeVisible();
  await page.getByTestId('jobs-list-scroll').getByText('Owned Action Job', { exact: true }).click();
  const jobDetail = page.getByRole('region', { name: 'Job detail' });
  await expect(jobDetail.getByRole('region', { name: 'Canonical Job configuration' })).toBeVisible();
  await expect(jobDetail.getByText('Action Job', { exact: true })).toBeVisible();
  await expect(jobDetail.getByRole('button', { name: 'Change target' })).toHaveCount(0);
  await expect(jobDetail.getByText(/Command 2/)).toBeVisible();
  await expect(jobDetail.getByText('Timeout: 19')).toBeVisible();
  await jobDetail.getByRole('button', { name: 'Edit', exact: true }).click();
  await page.getByLabel('Name', { exact: true }).fill('Owned Action Job edited');
  await page.getByRole('button', { name: 'Save Action Job' }).click();
  await expect(jobDetail.getByText('Owned Action Job edited', { exact: true })).toBeVisible();
  await jobDetail.getByRole('button', { name: 'Close' }).click();
  await page.getByTestId('jobs-list-scroll').getByText('Owned Action Job edited', { exact: true }).click();
  await expect(jobDetail.getByText('Timeout: 19')).toBeVisible();
  await page.screenshot({ path: path.join(runtime, 'action-job-detail-1280.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 900 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: path.join(runtime, 'action-job-detail-390.png'), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await jobDetail.getByRole('button', { name: 'Close' }).click();
  evidence.checks.push('Action Job creation, reload, exact Action definition and panel edit round-trip');

  if (!automationOnly) {
  await page.screenshot({ path: path.join(runtime, 'jobs-list.png'), fullPage: true });

  await page.goto(base + '/react/jobs');
  await page.getByRole('button', { name: 'Job Templates', exact: true }).click();
  await expect(page.getByRole('article', { name: 'JobTemplates Owned template', exact: true })).toBeVisible();

  const templateDuplicateResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/job-templates') && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'duplicate Owned template' }).click();
  const templateDuplicate = await templateDuplicateResponse;
  assert.equal(templateDuplicate.status(), 201);
  const templateBody = templateDuplicate.request().postDataJSON();
  assert.equal(templateBody.expectedRevision, 0);
  assert.equal(templateBody.definition.name, 'Owned template copy');
  assert.equal(templateBody.definition.jobDefinition.steps.length, 1);
  assert.equal(templateBody.definition.jobDefinition.steps[0].actionRef.id, seed.actionRef.id);
  assert.deepEqual(templateBody.definition.composition, { creators: [], triggers: [] });
  assert.ok(templateBody.requestKey);
  await expect(page.getByRole('article', { name: 'JobTemplates Owned template copy' })).toBeVisible();
  evidence.checks.push('canonical Template duplicate preserves the complete advanced definition');

  async function revisions(name) {
    const item = page.getByRole('article', { name: `JobTemplates ${name}` });
    const identifiers = item.getByText('Identifiers and revisions', { exact: true });
    await identifiers.click();
    const text = await item.innerText();
    await identifiers.click();
    return {
      revision: Number(text.match(/(?:定义版本|Definition revision)\s*(\d+)/)?.[1]),
      stateRevision: Number(text.match(/(?:状态版本|State revision)\s*(\d+)/)?.[1]),
    };
  }
  const beforePublish = await revisions('Owned template copy');
  const templatePublishResponse = page.waitForResponse(
    (response) =>
      response.url().includes('/api/job-templates/') &&
      response.url().endsWith('/operations') &&
      response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Publish Owned template copy' }).click();
  const templatePublished = await templatePublishResponse;
  assert.equal(templatePublished.request().postDataJSON().operation, 'publish');
  assert.equal(templatePublished.request().postDataJSON().expectedRevision, beforePublish.revision);
  assert.equal(
    templatePublished.request().postDataJSON().expectedStateRevision,
    beforePublish.stateRevision,
  );
  await expect(page.getByRole('button', { name: 'Instantiate Owned template copy' })).toBeVisible();
  evidence.checks.push('Template publish uses canonical revision/stateRevision');

  const beforeRetire = await revisions('Owned template copy');
  await page.getByRole('button', { name: 'retire Owned template copy' }).click();
  const retireResponse = page.waitForResponse(
    (response) =>
      response.url().includes('/api/job-templates/') &&
      response.request().method() === 'DELETE',
  );
  await page.getByRole('button', { name: /确认 retire|Confirm retire/ }).click();
  const retired = await retireResponse;
  assert.equal(retired.request().postDataJSON().expectedRevision, beforeRetire.revision);
  assert.equal(retired.request().postDataJSON().expectedStateRevision, beforeRetire.stateRevision);
  await expect(page.getByRole('button', { name: 'retire Owned template copy' })).toBeDisabled();
  evidence.checks.push('Template delete retires only the selected revision with observed CAS');

  await page.getByRole('button', { name: /创建 JobTemplates|Create JobTemplates/ }).click();
  await page.getByRole('textbox', { name: /^(名称|Name)$/ }).fill('Browser new template');
  await page.getByText('Advanced definition JSON', { exact: true }).click();
  await page
    .getByLabel('Definition JSON (name is entered separately)')
    .fill(
      JSON.stringify(
        {
          jobDefinition: {
            name: 'Browser new job',
            steps: [
              {
                stepId: 'effect',
                actionRef: seed.actionRef,
                arguments: {},
                completion: { contract: 'process-exit' },
              },
            ],
            retention: { policyRef: 'default' },
          },
          composition: { creators: [], triggers: [] },
        },
        null,
        2,
      ),
    );
  const createResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/job-templates') && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: /保存定义|Save definition/ }).click();
  const created = await createResponse;
  assert.equal(created.status(), 201);
  assert.equal(created.request().postDataJSON().definition.name, 'Browser new template');
  await expect(
    page.getByRole('article', { name: 'JobTemplates Browser new template' }),
  ).toBeVisible();
  evidence.checks.push('Template new uses the canonical definition and appears in the complete list');

  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(base + '/react/actions');
    await page.getByRole('button', { name: 'Inspect Owned old series', exact: true }).click();
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      true,
      `mapped Action panel overflows at ${width}`,
    );
    await page.screenshot({ path: path.join(runtime, `mapped-${width}.png`), fullPage: true });
    await page.getByRole('button', { name: 'Back to catalog' }).click();
    await page.goto(base + '/react/jobs');
    await page.getByRole('button', { name: 'Job Templates', exact: true }).click();
    await expect(page.getByRole('article', { name: 'JobTemplates Owned template', exact: true })).toBeVisible();
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      true,
      `Template view overflows at ${width}`,
    );
    await page.screenshot({ path: path.join(runtime, `template-${width}.png`), fullPage: true });
    evidence.checks.push(`production viewport ${width}: Action and Template flows fit without horizontal overflow`);
  }

  }

  const scheduledId = await page.evaluate(async () => {
    const sessionsResponse = await fetch('/api/sessions');
    const payload = await sessionsResponse.json();
    const sessions = Array.isArray(payload) ? payload : payload.sessions;
    const target = sessions.find(session => session.name === 'Alpha Session');
    const response = await fetch('/api/jobs/legacy-create', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'scheduled-task', name: 'Owned scheduled Job', text: 'Isolated schedule test',
        target: { sessionId: target.id }, enabled: false, requestKey: crypto.randomUUID(),
        action: { api: 'assign' }, schedule: [{ kind: 'cron', cron: '0 9 * * *', timezone: 'Asia/Shanghai' }] }),
    });
    const body = await response.json();
    if (!response.ok || !body.job?.jobId) throw Error('Scheduled fixture failed: ' + JSON.stringify(body));
    return body.job.jobId;
  });

  const protection = await page.evaluate(async () => {
    const url = '/api/jobs/actions/p6_system';
    const observed = await (await fetch(url)).json();
    const versions = await (await fetch(url + '/versions')).json();
    const expectedRevision = observed.revision;
    const expectedStateRevision = observed.stateRevision;
    const request = async (method, endpoint, body) => {
      const response = await fetch(endpoint, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, expectedRevision, requestKey: crypto.randomUUID() }),
      });
      return response.status;
    };
    const lifecycle = { expectedStateRevision, parameters: {} };
    const statuses = await Promise.all([
      request('PUT', url, { definition: { ...versions.items[0].definition, name: 'Unauthorized change' } }),
      request('POST', url + '/operations', { ...lifecycle, operation: 'disable' }),
      request('DELETE', url, lifecycle),
      request('POST', '/api/actions/actions/p6_system/lifecycle', { enabled: false }),
    ]);
    const final = await (await fetch(url)).json();
    return { statuses, unchanged: final.name === observed.name && final.enabled === observed.enabled &&
      final.revision === expectedRevision && final.stateRevision === expectedStateRevision };
  });
  assert.deepEqual(protection, { statuses: [403, 403, 403, 410], unchanged: true });
  evidence.checks.push('ordinary profile receives 403 for system Action update, disable and delete; legacy lifecycle returns 410');

  let theme;
  const capture = async (name, surface, width) => {
    await expect(surface).toBeVisible();
    await surface.scrollIntoViewIfNeeded();
    assert.equal(await surface.evaluate((el) => el.scrollWidth <= el.clientWidth + 1), true,
      `${name} has horizontal overflow at ${width}px (${theme})`);
    await page.screenshot({ path: path.join(runtime, `${name}-${width}-${theme}.png`) });
  };
  for (const [width, colorMode] of [[1280, 'dark'], [390, 'light'], [1280, 'light'], [390, 'dark']]) {
    theme = colorMode;
    await page.setViewportSize({ width, height: 900 });
    await page.evaluate((theme) => localStorage.setItem('pan:theme', theme), theme);
    await page.goto(base + '/react/');
    if (width < 768) await page.getByTitle('Toggle sidebar').click();
    const entry = page.getByRole('link', { name: 'Automation', exact: true });
    if (!(await entry.count())) {
      await page.locator('nav [tabindex="0"][aria-label]').first().press('ArrowRight');
    }
    await entry.click();
    await expect(page).toHaveURL(/\/react\/jobs$/);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    const mobileOverlay = page.getByTestId('mobile-sidebar-workspace-drawer');
    if (width < 768 && await mobileOverlay.getAttribute('aria-hidden') !== 'true') {
      await page.mouse.click(width - 3, 420);
    }
    const jobs = page.getByRole('region', { name: 'Jobs management' });
    await expect(jobs.getByRole('link', { name: 'Jobs', exact: true })).toHaveAttribute('aria-current', 'page');
    await capture('automation-jobs', jobs, width);
    if (automationOnly) {
      const tools = jobs.getByRole('group', { name: 'Jobs tools' });
      const bounds = await tools.getByRole('button').evaluateAll((buttons) =>
        buttons.map((button) => ({ top: button.offsetTop, height: button.offsetHeight })));
      if (width === 390) {
        assert.equal(new Set(bounds.map(({ top }) => top)).size, 1);
        assert(bounds.every(({ height }) => height >= 44));
      }
      await jobs.getByText('Sort and refresh', { exact: true }).click();
      await expect(jobs.getByRole('button', { name: 'Refresh', exact: true })).toBeVisible();
      await jobs.getByRole('combobox', { name: 'Sort jobs' }).selectOption('updated');
      await jobs.getByText('Sort and refresh', { exact: true }).click();
    }
    const nav = () => page.getByRole('navigation', { name: 'Automation pages' });
    const triggerLink = nav().getByRole('link', { name: 'Trigger', exact: true });
    await triggerLink.focus();
    await triggerLink.press('Enter');
    const authorization = page.getByRole('region', { name: 'Job user authorization' });
    if (automationOnly) {
      const disclosure = authorization.getByText('Authorized', { exact: true });
      await expect(authorization.locator('details')).not.toHaveAttribute('open');
      await disclosure.click();
      await expect(authorization.getByText(/Enabled Jobs keep/)).toBeVisible();
      await disclosure.click();

    }
    await page.getByRole('button', { name: 'Create Triggers', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Create Triggers' })).toBeVisible();
    await expect(page.getByText(/definition revision observed.*0/)).toHaveCount(0);
    await capture('trigger-editor', page.getByRole('region', { name: 'Automation management' }), width);
    await nav().getByRole('link', { name: 'Event Register', exact: true }).click();
    await page.getByRole('button', { name: 'New Scheduler' }).click();
    const scheduler = page.getByRole('region', { name: 'Scheduler editor' });
    await scheduler.getByLabel('Scheduler name').fill(`Owned timer ${width} ${theme}`);
    await capture('scheduler-editor', scheduler, width);
    await scheduler.getByRole('button', { name: 'Save Scheduler' }).click();
    const timer = page.getByRole('article', { name: `Scheduler Owned timer ${width} ${theme}` });
    await expect(timer).toBeVisible();
    const timerId = await timer.getAttribute('id');
    await page.goto(base + `/react/automation?tab=events&focus=${encodeURIComponent(timerId)}`);
    await expect(page.locator(`[id="${timerId}"]`)).toHaveClass(/ring-accent/);
    await expect(page.getByRole('region', { name: 'Scheduler detail' }).getByText('0 referencing subscribers')).toHaveCount(0);
    await capture('event-register', page.getByRole('region', { name: 'Automation management' }), width);
    const schedulerRecord = page.getByRole('complementary', { name: 'Record workspace' });
    if (!(await schedulerRecord.isVisible())) await timer.getByRole('button', {name: /Owned timer/}).first().click();
    await expect(schedulerRecord).toHaveAttribute('data-layout', 'side');
    await schedulerRecord.getByRole('button', { name: 'Expand detail' }).click();
    await expect(schedulerRecord).toHaveAttribute('data-layout', 'expanded');
    await expect(page.getByLabel('Search event sources')).toBeHidden();
    await expect(nav()).toBeVisible();
    await capture('scheduler-expanded', page.getByRole('region', { name: 'Automation management' }), width);
    await schedulerRecord.getByRole('button', { name: 'Return to side detail' }).click();
    await nav().getByRole('link', { name: 'Trigger', exact: true }).click();
    await page.getByRole('button', { name: 'Create Triggers', exact: true }).click();
    const triggerName = `Owned trigger ${width} ${theme}`;
    await page.getByLabel('Name', { exact: true }).fill(triggerName);
    await page.getByLabel('Target canonical Job').selectOption(scheduledId);
    await page.getByLabel('Scheduler sources').selectOption(timerId);
    await page.getByRole('button', { name: 'Save definition', exact: true }).click();
    const triggerRow = page.getByRole('button', { name: triggerName, exact: true });
    await triggerRow.press('Enter');
    const triggerRecord = page.getByRole('complementary', { name: 'Record workspace' });
    await expect(triggerRecord).toHaveAttribute('data-layout', 'side');
    await capture('trigger-side', page.getByRole('region', { name: 'Automation management' }), width);
    await triggerRecord.getByRole('button', { name: 'Expand detail' }).click();
    await expect(triggerRecord).toHaveAttribute('data-layout', 'expanded');
    await expect(page.getByRole('button', { name: 'Create Triggers', exact: true })).toBeHidden();
    await expect(nav()).toBeVisible();
    await triggerRecord.getByRole('button', { name: 'Return to side detail' }).click();
    await triggerRecord.getByRole('button', { name: 'Close detail' }).click();
    await expect(page.getByRole('button', { name: 'Refresh list' })).toBeEnabled();
    await nav().getByRole('link', { name: 'Actions & Groups' }).click();
    const systemRows = page.getByRole('listitem').filter({ has: page.getByRole('button', { name: 'Inspect Owned system Action', exact: true }) });
    await expect(systemRows).toHaveCount(2);
    await expect(systemRows.filter({ hasText: 'p6_system_distinct' })).toHaveCount(1);
    await systemRows.filter({ hasNotText: 'p6_system_distinct' }).getByRole('button', { name: 'Inspect Owned system Action', exact: true }).click();
    const system = page.getByRole('region', { name: 'Canonical Action detail' });
    await expect(system.getByLabel('Action version').locator('option')).toHaveCount(1);
    await expect(system.getByRole('button', { name: 'Run fixed version' })).toBeEnabled();
    await expect(system.getByRole('button', { name: /publish|enable|disable|edit|delete/i })).toHaveCount(0);
    await capture('system-action', system, width);
    const actionRecord = page.getByRole('complementary', { name: 'Record workspace' });
    await actionRecord.getByRole('button', { name: 'Expand detail' }).click();
    await expect(actionRecord).toHaveAttribute('data-layout', 'expanded');
    await expect(nav()).toBeVisible();
    await actionRecord.getByRole('button', { name: 'Return to side detail' }).click();
    await system.getByRole('button', { name: 'Back to list' }).click();
    await page.getByRole('button', { name: 'New Action', exact: true }).click();
    await capture('user-action-editor', page.getByRole('main', { name: 'Actions Panel' }), width);
    await page.getByRole('button', { name: 'Cancel', exact: true }).first().click();
    await page.getByRole('button', { name: /^Groups/ }).click();
    const actions = page.getByRole('main', { name: 'Actions Panel' });
    await capture('groups', actions, width);
    await page.getByRole('button', { name: 'Inspect Owned Group' }).click();
    await expect(page.getByRole('article', { name: 'Group Owned Group' })).toBeVisible();
    await capture('group-detail', actions, width);
    await page.getByRole('button', { name: 'Edit Group', exact: true }).click();
    await expect(actionRecord).toHaveAttribute('data-layout', 'expanded');
    await expect(nav()).toBeVisible();
    await actionRecord.getByRole('button', { name: 'Back to list', exact: true }).click();
    await page.getByRole('button', { name: 'Inspect Owned Group' }).click();
    await expect(page.getByRole('button', { name: 'Inspect reference in catalog' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Inspect Action', exact: true }).click();
    await expect(page.getByRole('region', { name: 'Canonical Action detail' })).toBeVisible();

    const jobId = await page.evaluate(async () => {
      const result = await (await fetch('/api/jobs/legacy-list')).json();
      const jobs = Array.isArray(result) ? result : result.jobs;
      return jobs.find((job) => job.name === 'Owned Action Job edited').jobId;
    });
    await page.goto(base + `/react/automation?tab=job&focus=${encodeURIComponent(jobId)}`);
    await expect(page).toHaveURL(/\/react\/jobs\?focus=/);
    const detail = page.getByRole('region', { name: 'Job detail' });
    const record = page.getByRole('complementary', { name: 'Record workspace' });
    await expect(record).toHaveAttribute('data-layout', 'side');
    await record.getByRole('button', { name: 'Expand detail' }).press('Enter');
    await expect(record).toHaveAttribute('data-layout', 'expanded');
    await expect(page.getByTestId('jobs-list-scroll')).toBeHidden();
    await capture('job-expanded', jobs, width);
    await record.getByRole('button', { name: 'Return to side detail' }).click();
    await expect(record).toHaveAttribute('data-layout', 'side');
    await capture('job-side-detail', jobs, width);
    await expect(detail.getByRole('heading', { name: /^(Schedule|Last result)$/ })).toHaveCount(0);
    const runHistory = (url) => url.pathname === `/api/jobs/${encodeURIComponent(jobId)}/runs`;
    if (width === 1280 && theme === 'dark') {
      await page.route(runHistory, (route) => route.fulfill({ status: 503, json: { detail: 'Owned test failure' } }), { times: 1 });
      await page.reload();
      await expect(detail.getByRole('alert').filter({ hasText: 'Could not load run history.' })).toBeVisible();
      await capture('job-runs-failure', jobs, width);
      await detail.getByRole('button', { name: 'Retry', exact: true }).click();
      await expect(detail.getByText('No runs yet.', { exact: true }).first()).toBeVisible();
      evidence.checks.push('failed Run history read is distinguished from an empty list and can be retried');
    }
    await detail.getByRole('button', { name: 'Manage Run operations' }).click();
    await expect(detail.getByText('No Runs yet.', { exact: true })).toBeVisible();
    await capture('job-runs', page.getByRole('region', { name: 'Job runs' }), width);
    await detail.getByRole('button', { name: 'Close Runs' }).click();
    await detail.getByRole('button', { name: 'Edit', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Save Action Job' })).toBeVisible();
    await capture('job-editor', jobs, width);
    await page.goto(base + '/react/jobs');
    const scheduled = jobs.getByRole('button', { name: 'Owned scheduled Job', exact: true });
    await scheduled.press('Enter');
    const scheduledDetail = jobs.getByRole('region', { name: 'Job detail' });
    await scheduledDetail.getByRole('button', { name: 'Edit', exact: true }).click();
    await expect(jobs.getByRole('heading', { name: 'When should this Job run?' })).toBeVisible();
    await capture('scheduled-job-edit-default', jobs, width);
    const fireTime = jobs.getByLabel('Fire time');
    const originalTime = await fireTime.inputValue();
    const switchControl = jobs.getByRole('switch', { name: 'Job enabled' });
    const geometry = async () => {
      const hit = await switchControl.boundingBox();
      const track = await switchControl.getByTestId('schedule-switch-track').boundingBox();
      return [track.width, track.height, hit.height];
    };
    const before = await geometry();
    assert.deepEqual(before.slice(0, 2), [38, 22]);
    assert(before[2] >= 44);
    await switchControl.click();
    assert.deepEqual(await geometry(), before);
    await switchControl.click();
    const changed = originalTime === '10:15' ? '11:15' : '10:15';
    await fireTime.fill(changed);
    await capture('scheduled-job-edit', jobs, width);
    const saveResponse = page.waitForResponse((response) => response.request().method() === 'PATCH' &&
      new URL(response.url()).pathname === '/api/scheduler/tasks/' + scheduledId);
    await jobs.getByRole('button', { name: 'Save', exact: true }).click();
    const saved = await saveResponse;
    assert(saved.ok(), 'Scheduler save failed: ' + await saved.text());
    await expect(scheduledDetail.getByText('15 ' + changed.split(':')[0] + ' * * *', { exact: true })).toBeVisible();
    await scheduledDetail.getByRole('button', { name: 'Edit', exact: true }).click();
    await expect(fireTime).toHaveValue(changed);
    await fireTime.fill('12:40');
    page.once('dialog', (dialog) => dialog.dismiss());
    await jobs.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(fireTime).toHaveValue('12:40');
    page.once('dialog', (dialog) => dialog.accept());
    await jobs.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(scheduledDetail.getByText('15 ' + changed.split(':')[0] + ' * * *', { exact: true })).toBeVisible();
    await page.goto(base + '/react/jobs?focus=' + encodeURIComponent(scheduledId));
    await expect(scheduledDetail.getByText('15 ' + changed.split(':')[0] + ' * * *', { exact: true })).toBeVisible();
    evidence.checks.push(`scheduled Job edit, save, detail readback, cancel and toggle geometry at ${width}px (${theme})`);
    evidence.checks.push(`Automation navigation, deep links, Runs and editors at ${width}px (${theme})`);
  }

  assert.deepEqual(evidence.errors, []);
  evidence.status = 'passed';
} catch (error) {
  evidence.status = 'failed';
  evidence.failure = String(error.stack ?? error);
  if (page) {
    evidence.pageText = await page.locator('body').innerText();
    await page.screenshot({ path: path.join(runtime, 'failure.png') });
  }
  throw error;
} finally {
  await fs.writeFile(path.join(runtime, 'server.log'), log);
  await fs.writeFile(path.join(runtime, 'requests.json'), JSON.stringify(requests, null, 2));
  await fs.writeFile(path.join(runtime, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({ runtime, ...evidence }));
  await browser?.close();
  child.kill();
}
