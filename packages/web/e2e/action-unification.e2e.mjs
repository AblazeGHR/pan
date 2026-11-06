import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { chromium } from '@playwright/test';
import { setTimeout as sleep } from 'node:timers/promises';
const root = path.resolve(import.meta.dirname, '../../..'),
  runtime = path.join(root, '.tmp', `p5-http-${Date.now()}`);
await fs.mkdir(runtime, { recursive: true });
const port = await new Promise((resolve, reject) => {
  const s = net.createServer();
  s.on('error', reject);
  s.listen(0, '127.0.0.1', () => {
    const p = s.address().port;
    s.close(() => resolve(p));
  });
});
assert(![8767, 8768].includes(port));
const base = `http://127.0.0.1:${port}`,
  env = Object.fromEntries(
    Object.entries(process.env).filter(([k]) => !k.toUpperCase().startsWith('PAN_')),
  );
Object.assign(env, { PAN_E2E_RUNTIME: runtime, PAN_PORT: String(port), PYTHONPATH: root });
const log = createWriteStream(path.join(runtime, 'server.log')),
  child = spawn(
    process.env.PAN_E2E_PYTHON ?? 'python',
    [path.join(import.meta.dirname, 'action-unification-server.py')],
    { cwd: root, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] },
  );
child.stdout.pipe(log);
child.stderr.pipe(log);
let browser, page;
const evidence = { runtime, port, checks: [], errors: [] };
try {
  for (let i = 0; i < 90; i++) {
    if (child.exitCode !== null) throw Error('server exited ' + child.exitCode);
    try {
      if ((await fetch(base + '/api/jobs/actions')).status === 401) break;
    } catch {}
    await sleep(500);
  }
  const key = (await fs.readFile(path.join(runtime, 'automation/user.key'), 'utf8')).trim();
  const login = await fetch(base + '/api/automation/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key }),
  });
  assert.equal(login.status, 200);
  const auth = await login.json();
  const api = async (route, method = 'GET', body) => {
    const response = await fetch(base + '/api/' + route, {
      method,
      headers: { Authorization: `Bearer ${auth.token}`, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const data = await response.json();
    assert(response.ok, `${route}: ${response.status} ${JSON.stringify(data)}`);
    return data;
  };
  assert.equal((await fetch(base + '/api/jobs/actions')).status, 401);
  evidence.checks.push('HTTP unauthenticated 401');
  const catalog = await api('jobs/actions');
  assert(catalog.items.some((i) => i.system));
  assert.equal(catalog.items.filter((i) => i.id === 'p5_echo').length, 1);
  assert.equal(catalog.versions.p5_echo.count, 2);
  evidence.checks.push('single logical catalog system/user and two versions');
  browser = await chromium.launch({ headless: true });
  page = await browser.newPage();
  page.on('pageerror', (e) => evidence.errors.push(e.message));
  page.on('response', async (response) => {
    if (response.url().endsWith('/api/triggers') && response.status() >= 400) {
      evidence.triggerRejection = await response.json();
      evidence.triggerDefinition = JSON.parse(response.request().postData());
    }
  });
  await page.goto(base + '/react/actions');
  await page.getByLabel('Local user key').fill(key);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await page.getByRole('button', { name: /P5 Echo 用户/ }).click();
  await page.getByLabel('Action version').selectOption('1');
  await page.getByLabel('suffix', { exact: true }).fill('owned-parameter');
  await page.getByRole('button', { name: '运行固定版本' }).click();
  await page
    .getByText(/OUTPUT-v1/)
    .first()
    .waitFor();
  await page
    .getByText(/PARAM-owned-parameter/)
    .first()
    .waitFor();
  evidence.checks.push('production fixed v1 parameters/stdout/stderr');
  await page.getByLabel('Action version').selectOption('2');
  await page.getByRole('button', { name: '运行固定版本' }).click();
  await page
    .getByText(/OUTPUT-v2/)
    .first()
    .waitFor();
  evidence.checks.push('explicit v2 invocation');
  await page.getByRole('button', { name: '加载引用与 Groups' }).click();
  await page.getByLabel('Group to upgrade').selectOption('p5_tools');
  await page.getByRole('button', { name: '预览此 Group 全部升级' }).click();
  await page.getByRole('button', { name: '应用已预览批次' }).click();
  await page
    .getByText(/"published": false/)
    .first()
    .waitFor();
  let groups = await api('action-groups');
  assert.equal(groups.items[0].tools[0].actionRef.revision, 2);
  let release = await api('action-groups/releases/' + groups.items[0].release);
  assert.equal(release.tools[0].actionRef.revision, 1);
  evidence.checks.push('Group preview/apply draft only old release v1');
  await page.getByRole('button', { name: 'Build P5 Tools', exact: true }).click();
  await sleep(500);
  groups = await api('action-groups');
  release = await api('action-groups/releases/' + groups.items[0].release);
  assert.equal(release.tools[0].actionRef.revision, 2);
  evidence.checks.push('explicit build v2');
  await page.getByRole('button', { name: '预览此版本更新所有 Groups' }).click();
  await page.getByRole('button', { name: '应用已预览批次' }).waitFor();
  evidence.checks.push('Action all Groups preview');
  await page.getByLabel(/P5 Job/).check();
  await page.getByRole('button', { name: '预览未来 Run 引用更新' }).click();
  await page.getByRole('button', { name: '应用已预览批次' }).click();
  await page
    .getByText(/"existingRuns": "frozen"/)
    .first()
    .waitFor();
  const seed = JSON.parse(await fs.readFile(path.join(runtime, 'seed.json'), 'utf8'));
  assert.equal((await api('jobs/' + seed.jobId)).steps[0].actionRef.revision, 2);
  evidence.checks.push('Job future Run reference preview/apply');
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await page.screenshot({ path: path.join(runtime, `actions-${width}.png`), fullPage: true });
    assert(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      `overflow ${width}`,
    );
    evidence.checks.push(`production viewport ${width}`);
  }
  await page.getByRole('button', { name: '返回目录', exact: true }).click();
  await page.getByRole('button', { name: /P5 Failure 用户/ }).click();
  await page.getByRole('button', { name: '运行固定版本' }).click();
  await page
    .getByText(/OUTPUT-failure/)
    .first()
    .waitFor();
  assert((await page.locator('article').innerText()).includes('7'));
  evidence.checks.push('real exit 7 output retained');
  await page.getByRole('button', { name: '返回目录', exact: true }).click();
  await page.getByRole('button', { name: /P5 Echo 用户/ }).click();
  let posts = 0;
  await page.route('**/api/jobs/actions/p5_echo/invocations', async (route) => {
    posts++;
    await route.fetch();
    await route.abort('connectionreset');
  });
  await page.getByRole('button', { name: '运行固定版本' }).click();
  await page.getByRole('button', { name: '查询原操作', exact: true }).click();
  await page
    .getByText(/OUTPUT-v2/)
    .first()
    .waitFor();
  assert.equal(posts, 1);
  assert.equal(
    (await fs.readFile(path.join(runtime, 'P5 Echo.effects'), 'utf8')).trim().split('\n').length,
    3,
  );
  evidence.checks.push('actual lost completed response: GET original identity no second effect');
  await page.unroute('**/api/jobs/actions/p5_echo/invocations');
  await page.getByRole('button', { name: '返回目录', exact: true }).click();
  await page.getByRole('button', { name: /P5 Protected 用户/ }).click();
  await page.getByRole('button', { name: '运行固定版本' }).click();
  await page
    .getByRole('alert')
    .filter({ hasText: /authorization_denied/ })
    .waitFor();
  assert.equal(
    await fs.stat(path.join(runtime, 'P5 Protected.effects')).then(
      () => true,
      () => false,
    ),
    false,
  );
  evidence.checks.push('real protected authorization 403 without effect');
  await page.goto(base + '/react/automation/canonical');
  await page.getByRole('button', { name: 'Triggers', exact: true }).click();
  await page.getByRole('button', { name: '创建 Triggers', exact: true }).click();
  await page.getByLabel('名称', { exact: true }).fill('P5 Direct Trigger');
  await page.getByLabel('Creator selection', { exact: true }).selectOption('registeredType');
  await page.getByLabel('Trigger target type').selectOption('action');
  await page.getByLabel('Trigger Action', { exact: true }).selectOption('p5_echo');
  await page.getByLabel('Trigger Action version').selectOption('1');
  await page.getByRole('button', { name: '保存定义', exact: true }).click();
  await page.getByRole('article', { name: 'Triggers P5 Direct Trigger' }).waitFor();
  let triggers = await api('triggers?limit=100');
  assert.equal(
    triggers.items.find((t) => t.name === 'P5 Direct Trigger').target.actionRef.revision,
    1,
  );
  evidence.checks.push('production Trigger Action fixed v1 target saved');
  await page.getByRole('button', { name: '创建 Triggers', exact: true }).click();
  await page.getByLabel('名称', { exact: true }).fill('P5 Job Trigger');
  await page.getByLabel('Creator selection', { exact: true }).selectOption('registeredType');
  await page.getByLabel('Target canonical Job', { exact: true }).selectOption(seed.jobId);
  await page.getByRole('button', { name: '保存定义', exact: true }).click();
  await page.getByRole('article', { name: 'Triggers P5 Job Trigger' }).waitFor();
  triggers = await api('triggers?limit=100');
  assert.equal(triggers.items.find((t) => t.name === 'P5 Job Trigger').target.configRevision, 2);
  evidence.checks.push('production Trigger Job fixed config target saved');
  await page.goto(base + '/react/actions');
  await page.getByRole('button', { name: /P5 Unknown 用户/ }).click();
  await page.getByRole('button', { name: '运行固定版本' }).click();
  await page.getByRole('button', { name: '查询原操作', exact: true }).click();
  await page.getByText(/调用状态：unknown/).waitFor();
  assert(await page.getByLabel('Action version').isDisabled());
  await page.reload();
  await page.getByRole('button', { name: /P5 Unknown 用户/ }).click();
  await page.getByRole('button', { name: '查询原操作', exact: true }).click();
  await page.getByText(/调用状态：unknown/).waitFor();
  assert.equal(
    (await fs.readFile(path.join(runtime, 'P5 Unknown.effects'), 'utf8')).trim().split('\n').length,
    1,
  );
  evidence.checks.push(
    'actual effect/checkpoint fault stays uncertain after reload, original GET, one effect',
  );
  assert.equal(evidence.errors.filter((e) => !e.includes('P5 fault')).length, 0);
  evidence.status = 'passed';
} catch (e) {
  evidence.status = 'failed';
  evidence.failure = String(e.stack ?? e);
  if (page) {
    evidence.pageText = await page.locator('body').innerText();
    await page.screenshot({ path: path.join(runtime, 'failure.png') });
  }
  throw e;
} finally {
  await fs.writeFile(path.join(runtime, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify(evidence, null, 2));
  await browser?.close();
  child.kill();
  log.end();
}
