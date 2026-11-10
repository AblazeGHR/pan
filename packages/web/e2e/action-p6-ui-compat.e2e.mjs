import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { chromium, expect } from '@playwright/test';
import { setTimeout as sleep } from 'node:timers/promises';

const root = path.resolve(import.meta.dirname, '../../..');
const runtime = path.join(root, '.tmp', `p6-ui-${Date.now()}`);
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
Object.assign(env, { PAN_E2E_RUNTIME: runtime, PAN_PORT: String(port), PYTHONPATH: root });
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
const evidence = { port, checks: [], errors: [] };
child.stdout.on('data', (chunk) => {
  log += chunk;
});
child.stderr.on('data', (chunk) => {
  log += chunk;
});

try {
  for (let index = 0; index < 90; index++) {
    if (child.exitCode !== null) throw Error('owned server exited');
    try {
      if ((await fetch(base + '/__e2e/identity')).ok) break;
    } catch {}
    await sleep(500);
  }
  const identity = await (await fetch(base + '/__e2e/identity')).json();
  assert.equal(identity.pid, child.pid);
  assert.equal(path.resolve(identity.runtime), runtime);
  assert.equal(path.resolve(identity.checkout), root);
  evidence.checks.push('owned service PID, checkout and runtime verified');

  const sourcePath = path.join(runtime, 'cooperative-actions/registry.json');
  for (let index = 0; index < 90; index++) {
    if (await fs.stat(sourcePath).then(() => true, () => false)) break;
    await fetch(base + '/__e2e/identity');
    await sleep(500);
  }
  const sourceBytes = await fs.readFile(sourcePath);
  const seed = JSON.parse(await fs.readFile(path.join(runtime, 'p6-seed.json'), 'utf8'));
  const key = (await fs.readFile(path.join(runtime, 'automation/user.key'), 'utf8')).trim();

  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('pageerror', (error) => evidence.errors.push(error.message));
  page.on('request', (request) => {
    if (request.method() !== 'GET' && request.url().includes('/api/')) {
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
  if (login.ok) await page.reload();
  if (await page.getByLabel('Local user key').count()) {
    await page.getByLabel('Local user key').fill(key);
    await page.getByRole('button', { name: 'Log in', exact: true }).click();
  }
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
    const text = await page.getByRole('article', { name: `JobTemplates ${name}` }).innerText();
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
