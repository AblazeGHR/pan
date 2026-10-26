// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import CanonicalJobsView from './CanonicalJobsView';
import { useAutomationAuth } from '@/stores/automationStore';
import { runOperations } from '@/services/canonicalJobs';
import type { CanonicalKind } from '@/types/canonicalJobs';
let writes: { url: string; method: string; body: Record<string, unknown> }[];
let failure = 0;
let networkFailure = false;
let accepted = false;
const item = {
  id: 'canonical/one',
  name: 'Example',
  state: 'draft',
  stateRevision: 3,
  configRevision: 7,
  revision: 5,
  retention: { policyRef: 'default' },
  steps: [],
  schemaVersion: 2,
};
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const receipt = (status = 'completed') => ({
  operationId: 'op/one',
  requestKey: 'server-key',
  status,
  objectRefs: [],
  links: {},
});
beforeEach(() => {
  writes = [];
  failure = 0;
  networkFailure = false;
  accepted = false;
  useAutomationAuth.setState({ token: 'test-user', expiresAt: Date.now() + 60000 });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET';
      if (method !== 'GET') {
        writes.push({
          url,
          method,
          body: JSON.parse(String(init.body)) as Record<string, unknown>,
        });
        if (networkFailure) throw new TypeError('Network disconnected');
        if (failure)
          return response(
            {
              error: {
                code:
                  failure === 422
                    ? 'capability_unsupported'
                    : failure === 409
                      ? 'revision_conflict'
                      : 'authorization_denied',
              },
            },
            failure,
          );
        return response(receipt(accepted ? 'accepted' : 'completed'));
      }
      if (failure) return response({ error: { code: 'authorization_denied' } }, failure);
      if (url.includes('operations')) return response(receipt());
      if (url.includes('/runs?'))
        return response({
          items: [
            {
              runId: 'run/one',
              jobId: item.id,
              state: 'failed',
              stateRevision: 4,
              configRevision: 1,
            },
          ],
          nextCursor: null,
        });
      if (url.includes('limit=')) return response({ items: [item], nextCursor: null });
      return response(item);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  useAutomationAuth.getState().clear();
});
function host() {
  return render(
    <MemoryRouter>
      <CanonicalJobsView />
    </MemoryRouter>,
  );
}
const click = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
const names: Record<CanonicalKind, string> = {
  jobs: 'Jobs',
  triggers: 'Triggers',
  'event-creators': 'EventCreators',
  'job-templates': 'JobTemplates',
};
it.each(Object.entries(names))(
  'lists, creates, edits and retires %s with canonical CAS envelopes',
  async (kind, name) => {
    host();
    if (kind !== 'jobs') click(name);
    await screen.findByRole('article', { name: `${name} Example` });
    click(`创建 ${name}`);
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'Created' } });
    fireEvent.change(screen.getByLabelText('定义 JSON（名称单独填写）'), {
      target: { value: '{"description":"new"}' },
    });
    click('保存定义');
    await waitFor(() => expect(writes.length).toBe(1));
    await waitFor(() => expect(screen.queryByRole('form')).toBeNull());
    expect(writes[0]).toMatchObject({
      url: `/api/${kind}`,
      method: 'POST',
      body: { definition: { name: 'Created', description: 'new' }, expectedRevision: 0 },
    });
    click('编辑 Example');
    const definition = JSON.parse(
      (screen.getByLabelText('定义 JSON（名称单独填写）') as HTMLTextAreaElement).value,
    );
    expect(definition).not.toHaveProperty('stateRevision');
    expect(definition).not.toHaveProperty('id');
    expect(definition).not.toHaveProperty('schemaVersion');
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'Edited' } });
    click('保存定义');
    await waitFor(() => expect(writes.length).toBe(2));
    await waitFor(() => expect(screen.queryByRole('form')).toBeNull());
    expect(writes[1]).toMatchObject({
      url: `/api/${kind}/canonical%2Fone`,
      method: 'PUT',
      body: { expectedRevision: 7, definition: { name: 'Edited' } },
    });
    click('retire Example');
    click('确认 retire');
    await waitFor(() => expect(writes.length).toBe(3));
    expect(writes[2]).toMatchObject({
      method: 'DELETE',
      body: { expectedRevision: 7, expectedStateRevision: 3 },
    });
    expect(new Set(writes.map((w) => w.body.requestKey)).size).toBe(3);
  },
);
it.each(runOperations)(
  'performs %s using Run state revision and current Job config',
  async (operation) => {
    host();
    await screen.findByText('Example');
    click('Runs Example');
    await screen.findByText(/run\/one/);
    click(operation);
    if (operation === 'retry')
      fireEvent.change(screen.getByLabelText('Step ID（可选，默认失败边界）'), {
        target: { value: 'failed-step' },
      });
    click('确认操作');
    await waitFor(() => expect(writes.length).toBe(1));
    expect(writes[0]).toMatchObject({
      url: '/api/job-runs/run%2Fone/operations',
      method: 'POST',
      body: { operation, expectedStateRevision: 4 },
    });
    if (operation === 'run-again' || operation === 'clone-and-run')
      expect(writes[0]!.body.expectedRevision).toBe(7);
    else expect(writes[0]!.body).not.toHaveProperty('expectedRevision');
    if (operation === 'retry') expect(writes[0]!.body.stepId).toBe('failed-step');
    await screen.findByText('操作状态：completed');
  },
);
it.each([422, 403])('shows readable %s rejection for run operations', async (status) => {
  host();
  await screen.findByText('Example');
  click('Runs Example');
  await screen.findByText(/run\/one/);
  click('pause');
  failure = status;
  click('确认操作');
  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain(status === 422 ? '不支持此能力' : '没有执行此操作的权限');
  expect(screen.getByRole('form', { name: 'Run operation' })).toBeTruthy();
});
it('keeps an edit draft on conflict and close does not submit', async () => {
  host();
  await screen.findByText('Example');
  click('编辑 Example');
  fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'My draft' } });
  failure = 409;
  click('保存定义');
  expect((await screen.findByRole('alert')).textContent).toContain('版本冲突');
  expect((screen.getByLabelText('名称') as HTMLInputElement).value).toBe('My draft');
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  click('关闭编辑');
  expect(screen.getByRole('form')).toBeTruthy();
  confirm.mockReturnValue(true);
  click('关闭编辑');
  expect(screen.queryByRole('form')).toBeNull();
  expect(writes.length).toBe(1);
});
it('does not resend an unconfirmed request and queries its original identity', async () => {
  host();
  await screen.findByText('Example');
  click('编辑 Example');
  networkFailure = true;
  click('保存定义');
  await screen.findByText('Network disconnected');
  expect((screen.getByRole('button', { name: '保存定义' }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  click('查询原操作');
  await screen.findByText('操作状态：completed');
  const calls = vi.mocked(fetch).mock.calls;
  expect(
    calls.some(([url]) =>
      String(url).includes(`operations?operation=update&requestKey=${writes[0]!.body.requestKey}`),
    ),
  ).toBe(true);
  expect(writes.length).toBe(1);
});
it('shows accepted as pending effect and queries by operationId', async () => {
  host();
  await screen.findByText('Example');
  click('retire Example');
  accepted = true;
  click('确认 retire');
  await screen.findByText(/尚未确认效果成功/);
  expect((screen.getByRole('button', { name: '确认 retire' }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  click('查询原操作');
  await screen.findByText('操作状态：completed');
  expect(
    vi.mocked(fetch).mock.calls.some(([url]) => url === '/api/operations/op%2Fone'),
  ).toBe(true);
});
it('renders loading and list errors with a retry', async () => {
  let resolve!: (value: Response) => void;
  vi.mocked(fetch).mockImplementationOnce(
    () =>
      new Promise<Response>((done) => {
        resolve = done;
      }),
  );
  host();
  await screen.findByText('加载中…');
  resolve(response({ error: { code: 'authorization_denied' } }, 403));
  expect((await screen.findByRole('alert')).textContent).toContain('没有执行此操作的权限');
  click('刷新列表');
  await screen.findByText('Example');
});
it('keeps canonical navigation and performs no legacy transport requests', async () => {
  host();
  await screen.findByText('Example');
  expect(screen.getByRole('link', { name: 'Automation' }).getAttribute('href')).toBe('/automation');
  expect(vi.mocked(fetch).mock.calls.every(([url]) => /^\/api\/(?:jobs|triggers|event-creators|job-templates|job-runs|operations)(?:\/|\?|$)/.test(String(url)))).toBe(
    true,
  );
});
it('clears canonical data on logout and blocks unauthenticated reads', async () => {
  host();
  await screen.findByText('Example');
  click('Log out');
  await screen.findByText('请登录以加载 canonical 数据。');
  expect(screen.queryByText('Example')).toBeNull();
});
